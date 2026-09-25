from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import anthropic

from whatsgoingon.orchestrator.budget import AgentBudget, AgentTimeout
from whatsgoingon.retry import retry

DEFAULT_MAX_ITERATIONS = 6
DEFAULT_MAX_TOKENS = 4096
MAX_ATTEMPTS = 3
RETRY_BASE_DELAY_S = 2.0
# Same set the SDK retries by default: connection problems/timeouts, 409, 429, and 5xx/529.
# Anything else (400, 401, 404, ...) won't get better by asking again.
TRANSIENT_API_ERRORS: tuple[type[Exception], ...] = (
    anthropic.APIConnectionError,
    anthropic.ConflictError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
    anthropic.OverloadedError,
    anthropic.ServiceUnavailableError,
    anthropic.DeadlineExceededError,
)
# A research tool (FRED/Yahoo fetches, news search) gets at most this long per call - and never
# more than the agent has left - so one slow data source can't eat a whole agent turn.
TOOL_TIMEOUT_S = 30.0


class ToolTimeout(Exception):
    """A research tool didn't return in time; fed back to the model as an error result."""


def _retry_after_s(exc: Exception) -> float | None:
    """The server's own wait hint on a 429/529 (retry-after-ms or retry-after in seconds), if any.
    An HTTP-date retry-after isn't parsed - the exponential backoff covers that case."""
    headers = exc.response.headers if isinstance(exc, anthropic.APIStatusError) else {}
    for header, scale in (("retry-after-ms", 1000.0), ("retry-after", 1.0)):
        try:
            return float(headers[header]) / scale
        except (KeyError, ValueError):
            continue
    return None


def _run_tool(impl: Callable[..., Any], tool_input: dict[str, Any], *, timeout_s: float | None) -> Any:
    """Run a research tool, giving up after `timeout_s` (None: wait as long as it takes).

    The tool runs on a daemon thread because synchronous code can't be interrupted: on timeout
    the agent moves on (the model gets an error result), but the call itself is abandoned
    rather than cancelled and finishes - or not - in the background. Tools only read data, so
    an abandoned call has no side effects to worry about.
    """
    if timeout_s is None:
        return impl(**tool_input)
    outcome: dict[str, Any] = {}

    def target() -> None:
        try:
            outcome["result"] = impl(**tool_input)
        except Exception as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout_s)
    if thread.is_alive():
        raise ToolTimeout(f"tool didn't return within {timeout_s:.0f}s; try another approach")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["result"]


def _tool_timeout_s(budget: AgentBudget | None) -> float:
    remaining = budget.remaining_s() if budget is not None else None
    return TOOL_TIMEOUT_S if remaining is None else min(TOOL_TIMEOUT_S, remaining)


def _create_with_retries(
    client: anthropic.Anthropic,
    request: dict[str, Any],
    *,
    budget: AgentBudget | None,
    log: logging.LoggerAdapter,
) -> Any:
    """One messages.create() call, retried on transient errors with backoff (retry.py).

    The SDK's own retries are turned off for these calls (max_retries=0) so there's one retry
    layer, and it knows about the agent's deadline: each attempt's HTTP timeout is the time the
    agent has left, and no retry starts past the deadline. Without that, the SDK would happily
    spend 3 x its 10-minute default timeout on a Skeptic that isn't answering. Like the SDK, the
    backoff honours the server's retry-after hint; if that wait won't fit before the deadline,
    it gives up straight away instead of sleeping into a timeout.
    """
    deadline = budget.deadline if budget is not None else None
    no_sdk_retries = client.with_options(max_retries=0)

    def attempt() -> Any:
        options: dict[str, Any] = {}
        if budget is not None:
            budget.check_time()
            remaining = budget.remaining_s()
            if remaining is not None:
                options["timeout"] = remaining
        return no_sdk_retries.messages.create(**request, **options)

    try:
        return retry(
            attempt,
            attempts=MAX_ATTEMPTS,
            base_delay=RETRY_BASE_DELAY_S,
            exceptions=TRANSIENT_API_ERRORS,
            deadline=deadline,
            delay_hint=_retry_after_s,
            log=log,
        )
    except anthropic.APITimeoutError as exc:
        if budget is not None and budget.timed_out():
            # The request was cut off by the agent's own time limit - say that, not "timed out".
            raise AgentTimeout(agent=budget.agent, timeout_s=budget.timeout_s) from exc
        raise


def call_structured(
    client: anthropic.Anthropic,
    *,
    model: str,
    system: str,
    user_message: str,
    output_tool: dict,
    log: logging.LoggerAdapter,
    tools: Sequence[dict] = (),
    tool_impls: Mapping[str, Callable[..., Any]] | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    budget: AgentBudget | None = None,
) -> dict[str, Any]:
    """Tool-calling loop whose final answer is structured data instead of prose: the agent
    may call research `tools` for a few iterations, and finishes by calling `output_tool`,
    whose input dict is returned as-is.

    Same mechanics as agent/loop.py's run_agent(), with two differences that make it usable
    for message passing between agents:
    - tool_choice is "any", so every turn is a tool call and the answer always arrives as
      schema-shaped JSON rather than free text that would need parsing;
    - on the last iteration tool_choice names `output_tool` explicitly, so the loop can't
      run out of budget without an answer.

    Every model call and tool call is logged through `log` with token counts (and each model
    call's latency), and a final per-call total is logged when the agent submits.

    With a `budget`, it's checked before every model call (raising BudgetExceeded or
    AgentTimeout once a limit is hit - the caller decides the fallback) and each call's usage
    is recorded into it. Transient API errors are retried per call, within the time limit -
    see _create_with_retries(). Anything else (or a transient error that outlasts the retries)
    propagates as the SDK's own anthropic.APIError. Research tools are cut off after
    TOOL_TIMEOUT_S (or the agent's remaining time, if shorter) and the model gets an error
    result, as for any other tool failure; if that used up the agent's time, the next
    iteration's budget check raises AgentTimeout.
    """
    tool_impls = tool_impls or {}
    output_name = output_tool["name"]
    all_tools = [*tools, output_tool]
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
    input_tokens = output_tokens = tool_calls = 0
    started = time.monotonic()

    for iteration in range(1, max_iterations + 1):
        force_output = iteration == max_iterations or not tools
        tool_choice = {"type": "tool", "name": output_name} if force_output else {"type": "any"}
        if budget is not None:
            budget.check()
        request = dict(
            model=model,
            max_tokens=max_tokens,
            system=system,
            tools=all_tools,
            tool_choice=tool_choice,
            messages=messages,
        )
        call_started = time.monotonic()
        response = _create_with_retries(client, request, budget=budget, log=log)
        latency_s = time.monotonic() - call_started
        input_tokens += response.usage.input_tokens
        output_tokens += response.usage.output_tokens
        spent = None
        if budget is not None:
            spent = budget.record(
                model=model,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                latency_s=latency_s,
            )
        log.info(
            "agent model call",
            extra={
                "iteration": iteration,
                "model": model,
                "stop_reason": response.stop_reason,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "latency_s": round(latency_s, 3),
                **({"agent_cost_usd": round(spent.cost_usd, 6)} if spent is not None else {}),
            },
        )
        messages.append({"role": "assistant", "content": response.content})

        tool_uses = [block for block in response.content if block.type == "tool_use"]
        for block in tool_uses:
            if block.name == output_name:
                log.info(
                    "agent submitted output",
                    extra={
                        "iterations": iteration,
                        "tool_calls": tool_calls,
                        "total_input_tokens": input_tokens,
                        "total_output_tokens": output_tokens,
                        "elapsed_s": round(time.monotonic() - started, 3),
                    },
                )
                return block.input

        if not tool_uses:
            # Shouldn't happen with tool_choice any/tool, but e.g. a max_tokens cut-off can
            # leave no tool call - nudge rather than fail, the last iteration forces output.
            messages.append({"role": "user", "content": f"Submit your answer by calling {output_name}."})
            continue

        tool_results = []
        for block in tool_uses:
            tool_calls += 1
            impl = tool_impls.get(block.name)
            try:
                if impl is None:
                    raise ValueError(f"unknown tool: {block.name}")
                result = _run_tool(impl, block.input, timeout_s=_tool_timeout_s(budget))
                log.info(
                    "agent tool call",
                    extra={
                        "iteration": iteration,
                        "tool": block.name,
                        "tool_input": block.input,
                        "tool_result": result,
                    },
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, default=str),
                    }
                )
            except Exception as exc:
                log.warning(
                    "agent tool call failed",
                    extra={
                        "iteration": iteration,
                        "tool": block.name,
                        "tool_input": block.input,
                        "tool_error": str(exc),
                    },
                )
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": str(exc), "is_error": True}
                )
        messages.append({"role": "user", "content": tool_results})

    raise RuntimeError(f"agent did not call {output_name} within {max_iterations} iterations")

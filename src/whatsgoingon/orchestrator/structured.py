from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import anthropic

from whatsgoingon.orchestrator.budget import AgentBudget

DEFAULT_MAX_ITERATIONS = 6
DEFAULT_MAX_TOKENS = 4096


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

    Every model call and tool call is logged through `log` with token counts, and a final
    per-call total is logged when the agent submits.

    With a `budget`, it's checked before every model call (raising BudgetExceeded once it's
    used up - the caller decides the fallback) and each call's usage is recorded into it.
    """
    tool_impls = tool_impls or {}
    output_name = output_tool["name"]
    all_tools = [*tools, output_tool]
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
    input_tokens = output_tokens = tool_calls = 0

    for iteration in range(1, max_iterations + 1):
        force_output = iteration == max_iterations or not tools
        tool_choice = {"type": "tool", "name": output_name} if force_output else {"type": "any"}
        if budget is not None:
            budget.check()
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            tools=all_tools,
            tool_choice=tool_choice,
            messages=messages,
        )
        input_tokens += response.usage.input_tokens
        output_tokens += response.usage.output_tokens
        spent = None
        if budget is not None:
            spent = budget.record(
                model=model,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )
        log.info(
            "agent model call",
            extra={
                "iteration": iteration,
                "model": model,
                "stop_reason": response.stop_reason,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
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
                result = impl(**block.input)
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

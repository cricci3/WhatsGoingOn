from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

import anthropic

from whatsgoingon.pricing import estimate_cost_usd

logger = logging.getLogger(__name__)

DEFAULT_MAX_ITERATIONS = 8
DEFAULT_MAX_TOKENS = 4096


def run_agent(
    client: anthropic.Anthropic,
    *,
    model: str,
    system: str,
    initial_message: str,
    tools: list[dict],
    tool_impls: dict[str, Callable[..., Any]],
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> str:
    """Hand-rolled tool-calling loop: ask Claude, execute any tool calls, feed results
    back, repeat until it stops asking for tools (or the iteration budget runs out).

    Logs the run's tokens, estimated cost and latency when it ends - the single-agent baseline
    the Phase 2 debate cycle's spend is compared against (same price table, pricing.py)."""
    messages: list[dict[str, Any]] = [{"role": "user", "content": initial_message}]
    usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "model_latency_s": 0.0}
    started = time.monotonic()

    for iteration in range(1, max_iterations + 1):
        call_started = time.monotonic()
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            tools=tools,
            messages=messages,
        )
        usage["calls"] += 1
        usage["input_tokens"] += response.usage.input_tokens
        usage["output_tokens"] += response.usage.output_tokens
        usage["model_latency_s"] += time.monotonic() - call_started
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            _log_spend(model, usage, started)
            return "".join(block.text for block in response.content if block.type == "text")

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            impl = tool_impls.get(block.name)
            try:
                if impl is None:
                    raise ValueError(f"unknown tool: {block.name}")
                result = impl(**block.input)
                logger.info(
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
                logger.warning(
                    "agent tool call failed",
                    extra={
                        "iteration": iteration,
                        "tool": block.name,
                        "tool_input": block.input,
                        "tool_error": str(exc),
                    },
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(exc),
                        "is_error": True,
                    }
                )
        messages.append({"role": "user", "content": tool_results})

    logger.warning("agent loop hit max_iterations (%d) without reaching a final answer", max_iterations)
    _log_spend(model, usage, started)
    return "(agent did not reach a final narrative within the iteration budget)"


def _log_spend(model: str, usage: dict[str, Any], started: float) -> None:
    cost_usd = estimate_cost_usd(model, usage["input_tokens"], usage["output_tokens"])
    elapsed_s = time.monotonic() - started
    logger.info(
        "agent finished: %d calls, %d tokens, ~$%.4f, %.1fs",
        usage["calls"],
        usage["input_tokens"] + usage["output_tokens"],
        cost_usd,
        elapsed_s,
        extra={
            "model": model,
            **usage,
            "model_latency_s": round(usage["model_latency_s"], 3),
            "cost_usd": round(cost_usd, 6),
            "elapsed_s": round(elapsed_s, 3),
        },
    )

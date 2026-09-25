from __future__ import annotations

# USD per million tokens, (input, output) - Anthropic first-party API list prices. Prompt
# caching isn't used anywhere in the project, so cache read/write rates aren't modelled.
# Shared by the Phase 2 budget (orchestrator/budget.py) and the Phase 1 agent loop's cost log,
# so a 3-agent cycle and a single-agent one are costed the same way.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-fable-5": (10.00, 50.00),
    "claude-fable-5-1": (10.00, 50.00),
}
# An unknown model is priced like the most expensive known one: overestimating the spend
# trips the budget early, underestimating it would let a cycle overspend unnoticed.
UNKNOWN_MODEL_PRICE = max(PRICES_PER_MTOK.values())


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICES_PER_MTOK.get(model, UNKNOWN_MODEL_PRICE)
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000

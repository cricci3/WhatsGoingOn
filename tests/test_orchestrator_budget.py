"""Budget accounting on its own, and how call_structured() enforces it."""

import pytest
from orchestrator_fakes import client_with, response, tool_use

from whatsgoingon.logging_config import agent_logger
from whatsgoingon.orchestrator.budget import (
    UNKNOWN_MODEL_PRICE,
    BudgetExceeded,
    CycleBudget,
    estimate_cost_usd,
)
from whatsgoingon.orchestrator.structured import call_structured


def test_cost_uses_the_models_input_and_output_prices() -> None:
    # Haiku 4.5: $1 in / $5 out per million tokens.
    assert estimate_cost_usd("claude-haiku-4-5", 1_000_000, 100_000) == pytest.approx(1.50)


def test_unknown_model_is_costed_at_the_highest_known_price() -> None:
    price_in, price_out = UNKNOWN_MODEL_PRICE
    assert estimate_cost_usd("some-future-model", 1_000_000, 1_000_000) == pytest.approx(price_in + price_out)


def test_record_accumulates_per_agent_and_across_the_cycle() -> None:
    budget = CycleBudget()
    budget.record("analyst", model="claude-haiku-4-5", input_tokens=1000, output_tokens=100)
    budget.record("analyst", model="claude-haiku-4-5", input_tokens=2000, output_tokens=200)
    budget.record("skeptic", model="claude-haiku-4-5", input_tokens=500, output_tokens=50)

    analyst = budget.spend["analyst"]
    assert (analyst.calls, analyst.total_tokens) == (2, 3300)
    assert budget.total_tokens == 3850
    assert budget.cost_usd == pytest.approx((3500 * 1 + 350 * 5) / 1_000_000)


def test_agent_token_cap_only_blocks_that_agent() -> None:
    budget = CycleBudget(agent_max_tokens={"analyst": 1000})
    budget.record("analyst", model="claude-haiku-4-5", input_tokens=900, output_tokens=100)

    with pytest.raises(BudgetExceeded) as excinfo:
        budget.check("analyst")
    assert (excinfo.value.agent, excinfo.value.scope) == ("analyst", "agent")
    budget.check("skeptic")  # no cap of its own, and the cycle has no cost cap


def test_cycle_cost_cap_blocks_every_agent() -> None:
    budget = CycleBudget(max_cost_usd=0.001)
    budget.record("analyst", model="claude-haiku-4-5", input_tokens=1000, output_tokens=0)  # $0.001

    with pytest.raises(BudgetExceeded) as excinfo:
        budget.check("editor")
    assert (excinfo.value.agent, excinfo.value.scope) == ("editor", "cycle")
    assert "cycle's cost budget" in str(excinfo.value)


def test_no_limits_never_blocks() -> None:
    budget = CycleBudget()
    budget.record("analyst", model="claude-haiku-4-5", input_tokens=10**9, output_tokens=10**9)

    budget.check("analyst")


# --- enforcement in call_structured ---

OUTPUT_TOOL = {"name": "submit", "input_schema": {"type": "object"}}
LOOKUP_TOOL = {"name": "lookup", "input_schema": {"type": "object"}}


def _call(client, budget: CycleBudget, **overrides):
    kwargs = dict(
        model="claude-haiku-4-5",
        system="system",
        user_message="hello",
        output_tool=OUTPUT_TOOL,
        log=agent_logger("test", agent="analyst"),
        budget=budget.for_agent("analyst"),
    )
    return call_structured(client, **{**kwargs, **overrides})


def test_call_structured_records_every_model_call() -> None:
    client = client_with(
        response(tool_use("lookup", {}), input_tokens=100, output_tokens=10),
        response(tool_use("submit", {"ok": True}), input_tokens=200, output_tokens=20),
    )
    budget = CycleBudget()

    _call(client, budget, tools=[LOOKUP_TOOL], tool_impls={"lookup": lambda: {}})

    assert budget.spend["analyst"].calls == 2
    assert budget.spend["analyst"].total_tokens == 330


def test_call_structured_does_not_call_the_model_once_the_budget_is_used_up() -> None:
    client = client_with()
    budget = CycleBudget(agent_max_tokens={"analyst": 100})
    budget.record("analyst", model="claude-haiku-4-5", input_tokens=100, output_tokens=0)

    with pytest.raises(BudgetExceeded):
        _call(client, budget)
    client.messages.create.assert_not_called()


def test_call_structured_stops_mid_loop_when_a_call_crosses_the_limit() -> None:
    client = client_with(
        response(tool_use("lookup", {}), input_tokens=150, output_tokens=0),
        response(tool_use("submit", {})),
    )
    budget = CycleBudget(agent_max_tokens={"analyst": 100})

    with pytest.raises(BudgetExceeded):
        _call(client, budget, tools=[LOOKUP_TOOL], tool_impls={"lookup": lambda: {}})
    assert client.messages.create.call_count == 1  # overshoots by at most the one call


def test_output_submitted_by_the_call_that_crosses_the_limit_is_kept() -> None:
    client = client_with(response(tool_use("submit", {"ok": True}), input_tokens=500, output_tokens=0))
    budget = CycleBudget(agent_max_tokens={"analyst": 100})

    assert _call(client, budget) == {"ok": True}

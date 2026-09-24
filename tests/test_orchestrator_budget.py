"""Budget accounting (tokens, cost, time) on its own, and how call_structured() enforces it
and retries transient API errors."""

import threading
import time
from unittest.mock import Mock

import anthropic
import pytest
from orchestrator_fakes import api_error, client_with, response, tool_use

import whatsgoingon.orchestrator.structured as structured_module
from whatsgoingon.logging_config import agent_logger
from whatsgoingon.orchestrator.budget import (
    UNKNOWN_MODEL_PRICE,
    AgentBudget,
    AgentTimeout,
    BudgetExceeded,
    CycleBudget,
    estimate_cost_usd,
)
from whatsgoingon.orchestrator.structured import MAX_ATTEMPTS, _retry_after_s, call_structured


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


def _call(client, budget: CycleBudget | AgentBudget, **overrides):
    view = budget if isinstance(budget, AgentBudget) else budget.for_agent("analyst")
    kwargs = dict(
        model="claude-haiku-4-5",
        system="system",
        user_message="hello",
        output_tool=OUTPUT_TOOL,
        log=agent_logger("test", agent="analyst"),
        budget=view,
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


# --- time limits and retries ---


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("whatsgoingon.retry.time.sleep", lambda _seconds: None)


def test_for_agent_starts_the_clock_only_for_agents_with_a_time_limit() -> None:
    budget = CycleBudget(agent_timeout_s={"skeptic": 60})

    skeptic, editor = budget.for_agent("skeptic"), budget.for_agent("editor")

    assert 59 < skeptic.remaining_s() <= 60
    assert editor.deadline is None and editor.remaining_s() is None


def test_check_raises_agent_timeout_once_the_deadline_has_passed() -> None:
    view = AgentBudget(cycle=CycleBudget(), agent="skeptic", timeout_s=60, deadline=time.monotonic() - 1)

    with pytest.raises(AgentTimeout, match="skeptic didn't finish within its 60s"):
        view.check()


def test_transient_api_errors_are_retried(no_sleep: None) -> None:
    client = client_with(api_error(529), api_error(None), response(tool_use("submit", {"ok": True})))

    assert _call(client, CycleBudget()) == {"ok": True}
    assert client.messages.create.call_count == 3


def test_persistent_transient_errors_give_up_after_max_attempts(no_sleep: None) -> None:
    client = client_with(*[api_error(429)] * MAX_ATTEMPTS)

    with pytest.raises(anthropic.RateLimitError):
        _call(client, CycleBudget())
    assert client.messages.create.call_count == MAX_ATTEMPTS


def test_non_transient_api_errors_are_not_retried(no_sleep: None) -> None:
    client = client_with(api_error(400))

    with pytest.raises(anthropic.BadRequestError):
        _call(client, CycleBudget())
    assert client.messages.create.call_count == 1


def test_sdk_retries_are_off_and_each_request_gets_the_time_left() -> None:
    client = client_with(response(tool_use("submit", {})))
    budget = CycleBudget(agent_timeout_s={"analyst": 30})

    _call(client, budget)

    client.with_options.assert_called_once_with(max_retries=0)
    assert 29 < client.messages.create.call_args.kwargs["timeout"] <= 30


def test_no_request_timeout_is_set_without_a_time_limit() -> None:
    client = client_with(response(tool_use("submit", {})))

    _call(client, CycleBudget())

    assert "timeout" not in client.messages.create.call_args.kwargs


def test_out_of_time_before_a_call_raises_without_calling_the_model() -> None:
    client = client_with()
    view = AgentBudget(cycle=CycleBudget(), agent="analyst", timeout_s=5, deadline=time.monotonic() - 1)

    with pytest.raises(AgentTimeout):
        _call(client, view)
    client.messages.create.assert_not_called()


def test_request_cut_off_by_the_agents_deadline_is_reported_as_agent_timeout() -> None:
    client = client_with(api_error("timeout"))
    # Deadline ~now: too close for a retry's backoff, as it is when a request runs into it.
    view = Mock(spec=AgentBudget, agent="analyst", timeout_s=5, deadline=time.monotonic() + 0.1)
    view.remaining_s.return_value = 0.1
    view.timed_out.return_value = True  # the deadline passed while the request was in flight

    with pytest.raises(AgentTimeout) as excinfo:
        _call(client, view)
    assert isinstance(excinfo.value.__cause__, anthropic.APITimeoutError)


def test_retry_after_header_is_read_in_seconds_or_milliseconds() -> None:
    assert _retry_after_s(api_error(429, headers={"retry-after": "12"})) == 12.0
    assert _retry_after_s(api_error(429, headers={"retry-after-ms": "1500"})) == 1.5
    assert _retry_after_s(api_error(429, headers={"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"})) is None
    assert _retry_after_s(api_error(None)) is None


def test_rate_limit_asking_to_wait_past_the_deadline_is_not_retried(no_sleep: None) -> None:
    client = client_with(api_error(429, headers={"retry-after": "120"}), response(tool_use("submit", {})))
    budget = CycleBudget(agent_timeout_s={"analyst": 30})

    with pytest.raises(anthropic.RateLimitError):
        _call(client, budget)
    assert client.messages.create.call_count == 1


def test_slow_tool_is_cut_off_and_reported_to_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(structured_module, "TOOL_TIMEOUT_S", 0.05)
    release = threading.Event()
    client = client_with(
        response(tool_use("lookup", {})),
        response(tool_use("submit", {"ok": True}, tool_id="tool_2")),
    )

    try:
        result = _call(
            client, CycleBudget(), tools=[LOOKUP_TOOL], tool_impls={"lookup": lambda: release.wait(5)}
        )
    finally:
        release.set()

    assert result == {"ok": True}
    tool_result = client.messages.create.call_args_list[1].kwargs["messages"][2]["content"][0]
    assert tool_result["is_error"] is True
    assert "didn't return within" in tool_result["content"]


def test_tool_timeout_never_exceeds_the_agents_remaining_time() -> None:
    view = CycleBudget(agent_timeout_s={"analyst": 5}).for_agent("analyst")

    assert structured_module._tool_timeout_s(view) <= 5
    assert structured_module._tool_timeout_s(None) == structured_module.TOOL_TIMEOUT_S


def test_tool_errors_still_propagate_from_the_worker_thread() -> None:
    def boom():
        raise ValueError("bad series")

    with pytest.raises(ValueError, match="bad series"):
        structured_module._run_tool(boom, {}, timeout_s=5)

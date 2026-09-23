import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from orchestrator_fakes import client_with, response, tool_use

from whatsgoingon.logging_config import agent_logger
from whatsgoingon.orchestrator.structured import call_structured

OUTPUT_TOOL = {"name": "submit", "input_schema": {"type": "object"}}
LOOKUP_TOOL = {"name": "lookup", "input_schema": {"type": "object"}}


def _call(client, **overrides):
    kwargs = dict(
        model="claude-haiku-4-5",
        system="system",
        user_message="hello",
        output_tool=OUTPUT_TOOL,
        log=agent_logger("test", agent="tester"),
    )
    return call_structured(client, **{**kwargs, **overrides})


def test_returns_output_tool_input_and_forces_it_when_there_are_no_research_tools() -> None:
    client = client_with(response(tool_use("submit", {"answer": 1})))

    assert _call(client) == {"answer": 1}
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["tool_choice"] == {"type": "tool", "name": "submit"}
    assert kwargs["tools"] == [OUTPUT_TOOL]


def test_runs_research_tools_before_the_output_tool() -> None:
    client = client_with(
        response(tool_use("lookup", {"x": 1})),
        response(tool_use("submit", {"answer": 42}, tool_id="tool_2")),
    )
    lookup = Mock(return_value={"value": 42})

    result = _call(client, tools=[LOOKUP_TOOL], tool_impls={"lookup": lookup})

    assert result == {"answer": 42}
    lookup.assert_called_once_with(x=1)
    first, second = client.messages.create.call_args_list
    assert first.kwargs["tool_choice"] == {"type": "any"}
    tool_result = second.kwargs["messages"][2]["content"][0]
    assert tool_result["tool_use_id"] == "tool_1"
    assert "42" in tool_result["content"]


def test_last_iteration_forces_the_output_tool() -> None:
    client = client_with(
        response(tool_use("lookup", {})),
        response(tool_use("submit", {"done": True})),
    )

    _call(client, tools=[LOOKUP_TOOL], tool_impls={"lookup": Mock(return_value={})}, max_iterations=2)

    last_call = client.messages.create.call_args_list[1]
    assert last_call.kwargs["tool_choice"] == {"type": "tool", "name": "submit"}


def test_tool_failure_is_fed_back_as_error_result() -> None:
    client = client_with(
        response(tool_use("lookup", {})),
        response(tool_use("submit", {})),
    )

    _call(client, tools=[LOOKUP_TOOL], tool_impls={"lookup": Mock(side_effect=ValueError("boom"))})

    tool_result = client.messages.create.call_args_list[1].kwargs["messages"][2]["content"][0]
    assert tool_result["is_error"] is True
    assert "boom" in tool_result["content"]


def test_response_without_a_tool_call_gets_a_nudge_rather_than_failing() -> None:
    no_tool = SimpleNamespace(
        stop_reason="max_tokens",
        content=[SimpleNamespace(type="text", text="...")],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )
    client = client_with(no_tool, response(tool_use("submit", {"ok": True})))

    assert _call(client) == {"ok": True}
    assert "submit" in client.messages.create.call_args_list[1].kwargs["messages"][2]["content"]


def test_raises_if_output_tool_is_never_called() -> None:
    client = client_with(response(tool_use("lookup", {})))

    with pytest.raises(RuntimeError, match="did not call submit"):
        _call(client, tools=[LOOKUP_TOOL], tool_impls={"lookup": Mock(return_value={})}, max_iterations=1)


def test_logs_token_usage_tagged_with_the_agent(caplog: pytest.LogCaptureFixture) -> None:
    client = client_with(
        response(tool_use("lookup", {}), input_tokens=100, output_tokens=10),
        response(tool_use("submit", {}), input_tokens=200, output_tokens=20),
    )

    with caplog.at_level(logging.INFO):
        _call(client, tools=[LOOKUP_TOOL], tool_impls={"lookup": Mock(return_value={})})

    assert all(record.agent == "tester" for record in caplog.records)
    submitted = next(r for r in caplog.records if r.getMessage() == "agent submitted output")
    assert submitted.total_input_tokens == 300
    assert submitted.total_output_tokens == 30
    assert submitted.tool_calls == 1

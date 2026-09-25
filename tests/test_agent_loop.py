import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from whatsgoingon.agent.loop import run_agent


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(name: str, tool_input: dict, tool_id: str = "tool_1") -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=name, input=tool_input, id=tool_id)


def _response(stop_reason: str, content: list) -> SimpleNamespace:
    usage = SimpleNamespace(input_tokens=100, output_tokens=50)
    return SimpleNamespace(stop_reason=stop_reason, content=content, usage=usage)


def _client_with_responses(*responses) -> Mock:
    client = Mock()
    client.messages.create = Mock(side_effect=list(responses))
    return client


def test_returns_text_immediately_when_no_tool_use() -> None:
    client = _client_with_responses(_response("end_turn", [_text_block("Nothing much happened.")]))

    result = run_agent(
        client,
        model="claude-haiku-4-5",
        system="system",
        initial_message="hello",
        tools=[],
        tool_impls={},
    )

    assert result == "Nothing much happened."
    assert client.messages.create.call_count == 1


def test_executes_tool_then_returns_final_text() -> None:
    client = _client_with_responses(
        _response("tool_use", [_tool_use_block("lookup", {"x": 1})]),
        _response("end_turn", [_text_block("Final narrative.")]),
    )
    tool_impls = {"lookup": Mock(return_value={"value": 42})}

    result = run_agent(
        client,
        model="claude-haiku-4-5",
        system="system",
        initial_message="hello",
        tools=[{"name": "lookup"}],
        tool_impls=tool_impls,
    )

    assert result == "Final narrative."
    assert client.messages.create.call_count == 2
    tool_impls["lookup"].assert_called_once_with(x=1)

    # NB: Mock's call_args_list stores a reference to the same mutable `messages` list,
    # so index by known position rather than -1 (which reflects the list's final state).
    second_call_messages = client.messages.create.call_args_list[1].kwargs["messages"]
    tool_result_message = second_call_messages[2]
    assert tool_result_message["role"] == "user"
    assert tool_result_message["content"][0]["tool_use_id"] == "tool_1"
    assert "42" in tool_result_message["content"][0]["content"]


def test_tool_exception_is_reported_as_error_result_not_raised() -> None:
    client = _client_with_responses(
        _response("tool_use", [_tool_use_block("lookup", {})]),
        _response("end_turn", [_text_block("Recovered.")]),
    )
    tool_impls = {"lookup": Mock(side_effect=ValueError("boom"))}

    result = run_agent(
        client,
        model="claude-haiku-4-5",
        system="system",
        initial_message="hello",
        tools=[{"name": "lookup"}],
        tool_impls=tool_impls,
    )

    assert result == "Recovered."
    second_call_messages = client.messages.create.call_args_list[1].kwargs["messages"]
    tool_result = second_call_messages[2]["content"][0]
    assert tool_result["is_error"] is True
    assert "boom" in tool_result["content"]


def test_unknown_tool_name_reported_as_error() -> None:
    client = _client_with_responses(
        _response("tool_use", [_tool_use_block("does_not_exist", {})]),
        _response("end_turn", [_text_block("Recovered.")]),
    )

    result = run_agent(
        client,
        model="claude-haiku-4-5",
        system="system",
        initial_message="hello",
        tools=[],
        tool_impls={},
    )

    assert result == "Recovered."
    second_call_messages = client.messages.create.call_args_list[1].kwargs["messages"]
    tool_result = second_call_messages[2]["content"][0]
    assert tool_result["is_error"] is True
    assert "unknown tool" in tool_result["content"]


def test_gives_up_after_max_iterations() -> None:
    responses = [_response("tool_use", [_tool_use_block("lookup", {})]) for _ in range(5)]
    client = _client_with_responses(*responses)
    tool_impls = {"lookup": Mock(return_value={})}

    result = run_agent(
        client,
        model="claude-haiku-4-5",
        system="system",
        initial_message="hello",
        tools=[{"name": "lookup"}],
        tool_impls=tool_impls,
        max_iterations=3,
    )

    assert "did not reach a final narrative" in result
    assert client.messages.create.call_count == 3


def test_logs_tokens_cost_and_latency_when_done(caplog) -> None:
    client = _client_with_responses(
        _response("tool_use", [_tool_use_block("lookup", {})]),
        _response("end_turn", [_text_block("done")]),
    )

    with caplog.at_level(logging.INFO):
        run_agent(
            client,
            model="claude-haiku-4-5",
            system="s",
            initial_message="m",
            tools=[],
            tool_impls={"lookup": lambda: {}},
        )

    finished = next(r for r in caplog.records if r.getMessage().startswith("agent finished"))
    assert (finished.calls, finished.input_tokens, finished.output_tokens) == (2, 200, 100)
    assert finished.cost_usd == pytest.approx((200 * 1 + 100 * 5) / 1_000_000)
    assert finished.elapsed_s >= finished.model_latency_s >= 0

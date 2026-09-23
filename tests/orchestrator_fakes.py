"""Fake Anthropic responses shared by the orchestrator tests (not a test module itself)."""

from types import SimpleNamespace
from unittest.mock import Mock

from whatsgoingon.agent.state import SeriesDelta

CPI_DELTA = SeriesDelta(
    name="cpi",
    latest_date="2026-08-01",
    latest_value=310.0,
    reference_date="2026-07-01",
    reference_value=300.0,
)
OIL_DELTA = SeriesDelta(
    name="wti_oil",
    latest_date="2026-08-01",
    latest_value=90.0,
    reference_date="2026-07-01",
    reference_value=75.0,
)


def tool_use(name: str, tool_input: dict, tool_id: str = "tool_1") -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=name, input=tool_input, id=tool_id)


def response(*content, input_tokens: int = 100, output_tokens: int = 50) -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason="tool_use",
        content=list(content),
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def client_with(*responses) -> Mock:
    client = Mock()
    client.messages.create = Mock(side_effect=list(responses))
    return client

"""Fake Anthropic responses shared by the orchestrator tests (not a test module itself)."""

from types import SimpleNamespace
from unittest.mock import Mock

import anthropic
import httpx2

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


_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
_STATUS_ERRORS = {
    400: anthropic.BadRequestError,
    429: anthropic.RateLimitError,
    529: anthropic.OverloadedError,
}


def api_error(status: int | str | None, headers: dict[str, str] | None = None) -> anthropic.APIError:
    """A real SDK exception: a connection error for None, a timeout for "timeout", else the
    status error class the SDK raises for that HTTP status."""
    if status is None:
        return anthropic.APIConnectionError(request=_REQUEST)
    if status == "timeout":
        return anthropic.APITimeoutError(request=_REQUEST)
    cls = _STATUS_ERRORS[status]
    return cls(
        f"HTTP {status}", response=httpx2.Response(status, headers=headers, request=_REQUEST), body=None
    )


def client_with(*responses) -> Mock:
    client = Mock()
    client.messages.create = Mock(side_effect=list(responses))
    client.with_options = Mock(return_value=client)  # per-call options, same fake underneath
    return client

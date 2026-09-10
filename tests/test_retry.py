from unittest.mock import Mock

import pytest

from whatsgoingon.retry import retry


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("whatsgoingon.retry.time.sleep", lambda _seconds: None)


def test_returns_result_on_first_success() -> None:
    func = Mock(return_value="ok")

    result = retry(func)

    assert result == "ok"
    assert func.call_count == 1


def test_retries_on_matching_exception_then_succeeds() -> None:
    func = Mock(side_effect=[ValueError("boom"), "ok"])

    result = retry(func, attempts=3, exceptions=(ValueError,))

    assert result == "ok"
    assert func.call_count == 2


def test_raises_after_exhausting_attempts_on_exception() -> None:
    func = Mock(side_effect=ValueError("boom"))

    with pytest.raises(ValueError):
        retry(func, attempts=3, exceptions=(ValueError,))

    assert func.call_count == 3


def test_does_not_retry_non_matching_exception() -> None:
    func = Mock(side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError):
        retry(func, attempts=3, exceptions=(ValueError,))

    assert func.call_count == 1


def test_retries_on_unsuccessful_result_then_succeeds() -> None:
    func = Mock(side_effect=[[], [1, 2, 3]])

    result = retry(func, attempts=3, is_success=lambda r: len(r) > 0)

    assert result == [1, 2, 3]
    assert func.call_count == 2


def test_returns_last_result_after_exhausting_attempts_without_raising() -> None:
    func = Mock(return_value=[])

    result = retry(func, attempts=3, is_success=lambda r: len(r) > 0)

    assert result == []
    assert func.call_count == 3

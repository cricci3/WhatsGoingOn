import time
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


def test_no_retry_is_started_past_the_deadline() -> None:
    func = Mock(side_effect=ValueError("boom"))

    with pytest.raises(ValueError):
        retry(func, attempts=5, exceptions=(ValueError,), deadline=time.monotonic() + 0.5, base_delay=1.0)

    assert func.call_count == 1  # the first backoff alone would end past the deadline


def test_retries_while_the_deadline_allows() -> None:
    func = Mock(side_effect=[ValueError("boom"), "ok"])

    assert retry(func, exceptions=(ValueError,), deadline=time.monotonic() + 60, base_delay=1.0) == "ok"


def test_unsuccessful_result_is_returned_when_the_deadline_stops_retries() -> None:
    func = Mock(return_value=[])

    result = retry(func, attempts=5, is_success=bool, deadline=time.monotonic(), base_delay=1.0)

    assert result == []
    assert func.call_count == 1


def test_retry_warnings_go_to_the_given_logger() -> None:
    log = Mock()

    retry(Mock(side_effect=[ValueError("boom"), "ok"]), exceptions=(ValueError,), log=log)

    log.warning.assert_called_once()


def test_returns_last_result_after_exhausting_attempts_without_raising() -> None:
    func = Mock(return_value=[])

    result = retry(func, attempts=3, is_success=lambda r: len(r) > 0)

    assert result == []
    assert func.call_count == 3


def test_delay_hint_lengthens_the_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    slept = []
    monkeypatch.setattr("whatsgoingon.retry.time.sleep", slept.append)

    retry(
        Mock(side_effect=[ValueError(), "ok"]),
        exceptions=(ValueError,),
        base_delay=1.0,
        delay_hint=lambda _: 7.0,
    )

    assert slept == [7.0]


def test_delay_hint_is_ignored_when_shorter_than_the_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    slept = []
    monkeypatch.setattr("whatsgoingon.retry.time.sleep", slept.append)

    retry(
        Mock(side_effect=[ValueError(), "ok"]),
        exceptions=(ValueError,),
        base_delay=2.0,
        delay_hint=lambda _: 0.5,
    )

    assert slept == [2.0]


def test_hinted_wait_that_wont_fit_before_the_deadline_gives_up_at_once() -> None:
    func = Mock(side_effect=ValueError())

    with pytest.raises(ValueError):
        retry(func, exceptions=(ValueError,), deadline=time.monotonic() + 10, delay_hint=lambda _: 60.0)

    assert func.call_count == 1

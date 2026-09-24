from __future__ import annotations

import logging
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)


def retry[T](
    func: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
    is_success: Callable[[T], bool] = lambda _: True,
    deadline: float | None = None,
    delay_hint: Callable[[Exception], float | None] = lambda _: None,
    log: logging.Logger | logging.LoggerAdapter = logger,
) -> T:
    """Call func with exponential backoff, retrying on a matching exception or an unsuccessful result.

    If every attempt raises, the last exception propagates. If every attempt succeeds (no
    exception) but is_success keeps rejecting the result, the last result is returned as-is
    rather than raising, since an empty-but-valid result is a legitimate outcome.

    `deadline` is a time.monotonic() value: no retry is started (and no backoff slept) if it
    would begin at or after the deadline - the failure is handled as if attempts ran out, so a
    caller with a time limit never waits past it just to retry. `delay_hint` lets the caller
    read a wait time off the exception (e.g. an HTTP retry-after header); the backoff is then
    at least that long. `log` lets callers route the retry warnings through their own (e.g.
    per-agent) logger.
    """
    last_result: T | None = None
    for attempt in range(1, attempts + 1):
        delay = base_delay * (2 ** (attempt - 1))
        try:
            result = func()
        except exceptions as exc:
            delay = max(delay, delay_hint(exc) or 0.0)
            if attempt == attempts or _past(deadline, delay):
                raise
            log.warning("attempt %d/%d raised %s, retrying in %.1fs...", attempt, attempts, exc, delay)
        else:
            if is_success(result):
                return result
            last_result = result
            if attempt == attempts or _past(deadline, delay):
                return last_result
            log.warning("attempt %d/%d returned an unsuccessful result, retrying...", attempt, attempts)
        time.sleep(delay)
    return last_result  # type: ignore[return-value]


def _past(deadline: float | None, delay: float) -> bool:
    """True if a retry after sleeping `delay` would start at or after `deadline`."""
    return deadline is not None and time.monotonic() + delay >= deadline

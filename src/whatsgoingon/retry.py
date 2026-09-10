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
    is_success: Callable[[T], bool] = lambda _result: True,
) -> T:
    """Call func with exponential backoff, retrying on a matching exception or an unsuccessful result.

    If every attempt raises, the last exception propagates. If every attempt succeeds (no
    exception) but is_success keeps rejecting the result, the last result is returned as-is
    rather than raising, since an empty-but-valid result is a legitimate outcome.
    """
    last_result: T | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = func()
        except exceptions as exc:
            if attempt == attempts:
                raise
            logger.warning("attempt %d/%d raised %s, retrying...", attempt, attempts, exc)
        else:
            if is_success(result):
                return result
            last_result = result
            if attempt == attempts:
                return last_result
            logger.warning("attempt %d/%d returned an unsuccessful result, retrying...", attempt, attempts)
        time.sleep(base_delay * (2 ** (attempt - 1)))
    return last_result  # type: ignore[return-value]

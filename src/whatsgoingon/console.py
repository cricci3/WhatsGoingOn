from __future__ import annotations

import io
import sys


def use_utf8_output() -> None:
    """Make the CLIs print UTF-8 whatever the console's encoding.

    On Windows, stdout/stderr redirected to a file or pipe use the ANSI code page (cp1252),
    which can't encode some characters the model likes to write (e.g. "→", "≈"), so print()
    crashes with UnicodeEncodeError after the whole cycle has been paid for.
    PYTHONIOENCODING=utf-8 fixes it from outside; this fixes it for every invocation.
    """
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")

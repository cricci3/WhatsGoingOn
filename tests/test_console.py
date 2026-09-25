import io
import sys

from whatsgoingon.console import use_utf8_output


def test_redirected_cp1252_output_can_print_any_character(monkeypatch) -> None:
    out, err = io.BytesIO(), io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(out, encoding="cp1252"))
    monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(err, encoding="cp1252"))

    use_utf8_output()
    print("CPI → 3%")
    sys.stdout.flush()

    assert out.getvalue().decode("utf-8").strip() == "CPI → 3%"
    assert sys.stderr.encoding == "utf-8"


def test_non_text_streams_are_left_alone(monkeypatch) -> None:
    fake = io.StringIO()
    monkeypatch.setattr(sys, "stdout", fake)

    use_utf8_output()

    assert sys.stdout is fake

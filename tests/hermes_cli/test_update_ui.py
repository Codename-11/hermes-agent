import time

from hermes_cli import update_ui


class _FakeRawTTY:
    def __init__(self):
        self.writes = []

    def isatty(self):
        return True

    def write(self, data):
        self.writes.append(data)
        return len(data)

    def flush(self):
        pass


class _WrappedStdout:
    def __init__(self, raw):
        self._original = raw
        self.writes = []

    def isatty(self):
        return True

    def write(self, data):
        self.writes.append(data)
        return len(data)

    def flush(self):
        pass


def test_status_line_animates_only_on_raw_tty_not_update_log_wrapper(monkeypatch):
    raw = _FakeRawTTY()
    wrapped = _WrappedStdout(raw)
    monkeypatch.setattr(update_ui.sys, "stdout", wrapped)

    status = update_ui.StatusLine(interval=0.001)
    status.start("resolve: agent resolve")
    time.sleep(0.01)
    status.update("resolve: validate")
    time.sleep(0.01)
    status.success(note="resolved handoff")

    raw_output = "".join(raw.writes)
    wrapped_output = "".join(wrapped.writes)

    assert "\r\033[2K" in raw_output
    assert "resolve: agent resolve" in raw_output
    assert "resolve: validate" in raw_output
    assert any(frame in raw_output for frame in update_ui._SPINNER_FRAMES)

    assert wrapped_output == "✓ resolved handoff\n"
    assert "\r" not in wrapped_output
    assert not any(frame in wrapped_output for frame in update_ui._SPINNER_FRAMES)

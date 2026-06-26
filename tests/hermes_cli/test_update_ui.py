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


def test_status_line_preserves_scrollback_and_uses_no_raw_tty_control(monkeypatch):
    raw = _FakeRawTTY()
    wrapped = _WrappedStdout(raw)
    monkeypatch.setattr(update_ui.sys, "stdout", wrapped)

    status = update_ui.StatusLine(interval=0.001)
    status.start("resolve: agent resolve")
    status.update("resolve: validate")
    status.success(note="resolved handoff")

    raw_output = "".join(raw.writes)
    wrapped_output = "".join(wrapped.writes)

    assert raw_output == ""
    assert wrapped_output == (
        "⏳ resolve: agent resolve\n"
        "⏳ resolve: validate\n"
        "✓ resolved handoff\n"
    )
    assert "\r" not in wrapped_output
    assert "\033" not in wrapped_output
    assert not any(frame in wrapped_output for frame in update_ui._SPINNER_FRAMES)

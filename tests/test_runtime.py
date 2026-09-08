"""窓を持たない配布版（stdout/stderr が None）の起動条件のテスト。"""

from __future__ import annotations

import sys

from app import runtime


def test_bind_missing_std_streams_keeps_existing_streams() -> None:
    before_out, before_err = sys.stdout, sys.stderr

    runtime.bind_missing_std_streams()

    assert sys.stdout is before_out
    assert sys.stderr is before_err


def test_bind_missing_std_streams_binds_log_file_when_detached(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(runtime, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    runtime.bind_missing_std_streams()
    stream = sys.stdout
    try:
        # uvicorn のログ設定は sys.stdout.isatty() を呼ぶ（None だと起動に失敗する）。
        assert stream.isatty() is False
        assert sys.stderr is stream
        stream.write("日本語ログ\n")
        stream.flush()
    finally:
        stream.close()

    assert (tmp_path / "logs" / "app.log").read_text(encoding="utf-8") == "日本語ログ\n"


def test_log_path_lives_under_appdata_when_frozen(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(runtime, "is_frozen", lambda: True)
    monkeypatch.setenv("APPDATA", str(tmp_path))

    assert runtime.log_path() == tmp_path / "KakomonTrainer" / "logs" / "app.log"

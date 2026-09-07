"""インストール版（PyInstaller ビルド）の起動処理のテスト。

配布版は ``console=False`` で標準エラー出力を持たないため、設定不備で無言終了すると
利用者には何も起きていないように見える。ここではその経路を固定する。
"""

from __future__ import annotations

import ctypes

import pytest

from app import main as main_module
from app.config import ConfigError, Settings


@pytest.fixture
def frozen(monkeypatch, settings):
    """インストール版として振る舞わせ、外部作用（uvicorn・ブラウザ・ダイアログ）を記録する。"""
    import uvicorn
    import webbrowser

    monkeypatch.setattr(main_module, "settings", settings)
    monkeypatch.setattr(main_module, "is_frozen", lambda: True)
    monkeypatch.setattr(main_module, "_port_is_serving", lambda host, port: False)
    monkeypatch.setattr(Settings, "validate_ocr_or_die", lambda self: None)

    recorded: dict[str, list] = {"served": [], "opened": [], "dialogs": [], "waited": []}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: recorded["served"].append(kw))
    monkeypatch.setattr(webbrowser, "open", lambda url: recorded["opened"].append(url))
    monkeypatch.setattr(
        main_module,
        "_show_startup_error",
        lambda reason: recorded["dialogs"].append(reason),
    )
    # 実ポートを待ち受けるスレッドがテスト終了後まで残らないようにする。
    monkeypatch.setattr(
        main_module,
        "_open_browser_when_ready",
        lambda url, host, port: recorded["waited"].append(url),
    )
    return recorded


def test_frozen_startup_serves_and_opens_the_ui(frozen, settings):
    """設定が揃っていればサーバーを起動し、UI をブラウザで開く。"""
    main_module.main()

    assert frozen["served"] == [{"host": settings.host, "port": settings.port}]
    assert frozen["waited"] == [f"http://127.0.0.1:{settings.port}/"]
    assert frozen["dialogs"] == []


def test_frozen_startup_shows_a_dialog_instead_of_exiting_silently(frozen, monkeypatch):
    """OCR 認証が未設定なら、無言終了せずダイアログで知らせてから終了する。"""
    def refuse(self):
        raise ConfigError("GOOGLE_APPLICATION_CREDENTIALS が未設定です。")

    monkeypatch.setattr(Settings, "validate_ocr_or_die", refuse)

    with pytest.raises(SystemExit) as exit_info:
        main_module.main()

    assert exit_info.value.code == 1
    assert frozen["dialogs"] == ["GOOGLE_APPLICATION_CREDENTIALS が未設定です。"]
    assert frozen["served"] == [], "設定不備のままサーバーを起動してはいけない"


def test_frozen_startup_does_not_double_launch(frozen, monkeypatch, settings):
    """既に起動している場合はバインドに失敗させず、開いている UI を開き直す。"""
    monkeypatch.setattr(main_module, "_port_is_serving", lambda host, port: True)

    main_module.main()

    assert frozen["served"] == [], "二重起動でサーバーを立ち上げてはいけない"
    assert frozen["opened"] == [f"http://127.0.0.1:{settings.port}/"]


def test_source_checkout_starts_without_browser_or_dialog(frozen, monkeypatch, settings):
    """ソース実行の挙動は変えない（ブラウザは開かず、README どおり手動で開く）。"""
    monkeypatch.setattr(main_module, "is_frozen", lambda: False)

    main_module.main()

    assert frozen["served"] == [{"host": settings.host, "port": settings.port}]
    assert frozen["opened"] == []
    assert frozen["waited"] == []


@pytest.mark.parametrize("host, expected", [("0.0.0.0", "127.0.0.1"), ("::", "127.0.0.1"), ("", "127.0.0.1"), ("127.0.0.1", "127.0.0.1")])
def test_loopback_host_is_always_reachable_from_a_browser(monkeypatch, settings, host, expected):
    monkeypatch.setattr(main_module, "settings", settings.model_copy(update={"host": host}))
    assert main_module._loopback_host() == expected


def test_startup_dialog_names_the_env_file(monkeypatch):
    """ダイアログ本文に、利用者が直すべきファイルの絶対パスを必ず含める。"""
    shown: list[tuple] = []

    class FakeUser32:
        MessageBoxW = staticmethod(lambda *args: shown.append(args))

    monkeypatch.setattr(ctypes, "windll", type("W", (), {"user32": FakeUser32})())

    main_module._show_startup_error("OCR の認証情報がありません。")

    (_handle, text, title, flags) = shown[0]
    assert "OCR の認証情報がありません。" in text
    assert str(main_module.config_dir() / ".env") in text
    assert title.endswith("起動できません")
    assert flags == 0x10  # MB_ICONERROR


def test_open_browser_waits_for_the_server_to_accept_connections(monkeypatch):
    """サーバーが応答してから開く（起動前に開いて接続エラー画面を見せない）。"""
    import socket
    import webbrowser

    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        main_module._open_browser_when_ready("http://example/", "127.0.0.1", port, timeout=5.0)
    finally:
        listener.close()
    assert opened == ["http://example/"]


def test_open_browser_gives_up_when_the_server_never_starts(monkeypatch):
    import socket
    import webbrowser

    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))

    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    port = closed.getsockname()[1]
    closed.close()

    main_module._open_browser_when_ready("http://example/", "127.0.0.1", port, timeout=0.5)
    assert opened == []

"""GitHub Releases 自動更新クライアントのテスト。

ネットワークには出ず、``app.updater.urlopen`` を差し替えて GitHub API の応答を模す。
"""

from __future__ import annotations

import io
import json
import threading
from pathlib import Path

import pytest

from app import updater
from app.updater import Release, download_and_install, is_newer, latest_release
from app.version import __version__ as CURRENT

#: Inno Setup の ``OutputBaseFilename`` と一致していなければ更新資産を見つけられない。
#: packaging/KakomonTrainer.iss を変更したらこの定数も必ず追随させること。
ASSET_NAME = "KakomonTrainer-Setup.exe"

REPO = "eugene-rb/kakomon-trainer"


def _newer(version: str) -> str:
    major, minor, patch = (int(p) for p in version.split("."))
    return f"{major}.{minor}.{patch + 1}"


def _older(version: str) -> str:
    parts = [int(p) for p in version.split(".")]
    for i in (2, 1, 0):
        if parts[i] > 0:
            parts[i] -= 1
            return ".".join(str(p) for p in parts)
    raise AssertionError("0.0.0 より小さいバージョンは作れない")


def _payload(tag: str, *, asset_name: str = ASSET_NAME, url: str | None = None) -> dict:
    download = url or f"https://github.com/{REPO}/releases/download/{tag}/{asset_name}"
    return {
        "tag_name": tag,
        "body": "リリースノート",
        "assets": [{"name": asset_name, "browser_download_url": download}],
    }


@pytest.fixture
def fake_api(monkeypatch):
    """``urlopen`` を差し替え、渡された URL を記録しつつ固定の JSON を返す。"""
    calls: list[str] = []

    def install(payload: dict | None, *, error: Exception | None = None) -> list[str]:
        def fake_urlopen(request, timeout=None):
            calls.append(request.full_url)
            if error is not None:
                raise error
            return io.BytesIO(json.dumps(payload).encode("utf-8"))

        monkeypatch.setattr(updater, "urlopen", fake_urlopen)
        return calls

    return install


# ---------------------------------------------------------------------------
# バージョン比較
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "candidate, current, expected",
    [
        ("0.2.0", "0.1.0", True),
        ("v0.2.0", "0.1.0", True),
        ("0.1.0", "0.1.0", False),
        ("0.0.9", "0.1.0", False),
        ("1.0.0", "0.9.9", True),
        ("0.10.0", "0.9.0", True),  # 文字列比較なら誤判定する組み合わせ
        ("0.2.0-rc.1", "0.1.0", True),
        ("こんにちは", "0.1.0", False),
        ("0.2", "0.1.0", False),
        ("", "0.1.0", False),
    ],
)
def test_is_newer(candidate, current, expected):
    assert is_newer(candidate, current) is expected


# ---------------------------------------------------------------------------
# latest_release
# ---------------------------------------------------------------------------


def test_latest_release_finds_installer_asset(fake_api):
    """公開リリースが新しければ、インストーラー資産の URL を返す。"""
    tag = f"v{_newer(CURRENT)}"
    calls = fake_api(_payload(tag))

    release = latest_release(REPO)

    assert release is not None
    assert release.version == _newer(CURRENT)
    assert release.asset_name == ASSET_NAME
    assert release.notes == "リリースノート"
    assert calls == [f"https://api.github.com/repos/{REPO}/releases/latest"]


def test_latest_release_ignores_same_version(fake_api):
    """同一バージョンを「更新あり」にしない（毎回の起動で誤通知しないこと）。"""
    fake_api(_payload(f"v{CURRENT}"))
    assert latest_release(REPO) is None


def test_latest_release_ignores_older_version(fake_api):
    fake_api(_payload(f"v{_older(CURRENT)}"))
    assert latest_release(REPO) is None


def test_latest_release_requires_installer_suffix(fake_api):
    """``-Setup.exe`` 以外の添付物（ソース zip など）は更新対象にしない。"""
    fake_api(_payload(f"v{_newer(CURRENT)}", asset_name="KakomonTrainer-portable.zip"))
    assert latest_release(REPO) is None


def test_latest_release_rejects_non_github_download_url(fake_api):
    """添付 URL が github.com 以外なら無視する（差し替え配信を踏まない）。"""
    fake_api(_payload(f"v{_newer(CURRENT)}", url="https://example.com/evil/KakomonTrainer-Setup.exe"))
    assert latest_release(REPO) is None


@pytest.mark.parametrize("repository", ["", "  ", "not-a-repo", "owner/repo/extra", "owner/repo?x=1"])
def test_latest_release_rejects_bad_repository_without_network(fake_api, repository):
    """リポジトリ指定が不正なら、ネットワークに出る前に諦める。"""
    calls = fake_api(_payload(f"v{_newer(CURRENT)}"))
    assert latest_release(repository) is None
    assert calls == []


def test_latest_release_survives_network_error(fake_api):
    """更新確認の失敗でアプリを壊さない（None を返すだけ）。"""
    fake_api(None, error=OSError("接続できません"))
    assert latest_release(REPO) is None


def test_latest_release_survives_malformed_payload(monkeypatch):
    monkeypatch.setattr(updater, "urlopen", lambda request, timeout=None: io.BytesIO(b"<html>"))
    assert latest_release(REPO) is None


# ---------------------------------------------------------------------------
# download_and_install
# ---------------------------------------------------------------------------


def _release() -> Release:
    version = _newer(CURRENT)
    return Release(
        version=version,
        asset_url=f"https://github.com/{REPO}/releases/download/v{version}/{ASSET_NAME}",
        asset_name=ASSET_NAME,
    )


def test_download_and_install_refuses_source_checkout(monkeypatch):
    """ソース実行では自動更新しない（インストーラーが存在しないため）。"""
    monkeypatch.setattr(updater, "is_frozen", lambda: False)
    with pytest.raises(RuntimeError, match="インストール版"):
        download_and_install(_release(), lambda: None)


def test_download_and_install_runs_installer_then_shuts_down(monkeypatch, tmp_path):
    """インストーラーを保存し、サイレント実行してから自プロセスを終了させる。"""
    monkeypatch.setattr(updater, "is_frozen", lambda: True)
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(updater.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(updater, "urlopen", lambda request, timeout=None: io.BytesIO(b"MZ-installer"))
    launched: list[list[str]] = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda args, **kw: launched.append(args))
    stopped = threading.Event()

    download_and_install(_release(), stopped.set)

    saved = tmp_path / ASSET_NAME
    assert saved.read_bytes() == b"MZ-installer"
    assert stopped.wait(timeout=5), "更新後にアプリが終了しなかった"
    assert launched == [[str(saved), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/CLOSEAPPLICATIONS"]]


def test_download_and_install_cleans_up_failed_download(monkeypatch, tmp_path):
    """ダウンロードに失敗したら壊れたファイルを残さず、日本語メッセージで知らせる。"""
    monkeypatch.setattr(updater, "is_frozen", lambda: True)
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))

    def boom(request, timeout=None):
        raise OSError("切断されました")

    monkeypatch.setattr(updater, "urlopen", boom)

    with pytest.raises(RuntimeError, match="ダウンロードできませんでした"):
        download_and_install(_release(), lambda: None)
    assert list(Path(tmp_path).iterdir()) == []

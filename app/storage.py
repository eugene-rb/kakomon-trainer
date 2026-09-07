"""template.json / session.json / result.json の読み書き。

SPEC §4 のディレクトリ構成に対する永続化層。API 層（main.py）と処理層
（scanner / grader / annotator）の両方から使う。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from threading import RLock

from app.config import Settings
from app.models import SCHEMA_VERSION, Result, SessionState, Template, TemplateSummary

logger = logging.getLogger(__name__)
_json_lock = RLock()


def _read_text(path: Path) -> str:
    # Windows は読み取り中のファイル置換を拒否するため、短い I/O 区間を直列化する。
    with _json_lock:
        return path.read_text(encoding="utf-8")

TEMPLATE_FILE = "template.json"
SESSION_FILE = "session.json"
RESULT_FILE = "result.json"
GRADED_PDF = "graded.pdf"


class NotFoundError(FileNotFoundError):
    """対象のテンプレート／セッションが存在しない。"""


class TemplateSchemaError(RuntimeError):
    """template.json が現行スキーマより古い（別レイアウトの用紙を指している）。"""


def _write_json(path: Path, model) -> None:
    """Pydantic モデルを UTF-8 の JSON として原子的に近い形で書き出す。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    # 同じファイルへの同時保存でも一時ファイルが衝突しないようにする。
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(model.model_dump_json(indent=2))
        with _json_lock:
            tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# テンプレート
# --------------------------------------------------------------------------


def template_path(settings: Settings, template_id: str) -> Path:
    return settings.template_dir(template_id) / TEMPLATE_FILE


def load_template(settings: Settings, template_id: str) -> Template:
    path = template_path(settings, template_id)
    if not path.exists():
        raise NotFoundError(f"テンプレートが見つかりません: {template_id}")
    raw = _read_text(path)
    try:
        version = int(json.loads(raw).get("schema_version", 1))
    except (json.JSONDecodeError, TypeError, ValueError):
        version = 1
    if version < SCHEMA_VERSION:
        raise TemplateSchemaError(
            f"テンプレート {template_id} は旧レイアウト（v{version}）です。"
            f"blank.pdf のマーカー配置が変わったため、source.pdf からテンプレートを"
            f"作り直して blank.pdf を印刷し直してください。"
        )
    return Template.model_validate_json(raw)


def save_template(settings: Settings, template: Template) -> None:
    _write_json(template_path(settings, template.template_id), template)


def list_templates(settings: Settings) -> list[TemplateSummary]:
    """テンプレート一覧を作成日時の新しい順で返す。"""
    summaries: list[TemplateSummary] = []
    if not settings.templates_dir.exists():
        return summaries
    for directory in sorted(settings.templates_dir.iterdir()):
        if not directory.is_dir() or not (directory / TEMPLATE_FILE).exists():
            continue
        try:
            template = load_template(settings, directory.name)
        except Exception as e:  # 壊れた template.json で一覧全体を落とさない
            logger.warning("テンプレートを読み込めませんでした: %s (%s)", directory.name, e)
            continue
        summaries.append(
            TemplateSummary(
                template_id=template.template_id,
                title=template.title,
                created_at=template.created_at,
                page_count=len(template.pages),
                question_count=len(template.questions),
                total_max_score=template.total_max_score,
                scan_ready=bool(template.questions) and all(q.regions for q in template.questions),
            )
        )
    return sorted(summaries, key=lambda s: s.created_at, reverse=True)


def load_all_templates(settings: Settings) -> list[Template]:
    """全テンプレートを読み込む（QR によるテンプレート振り分けで使う）。"""
    templates: list[Template] = []
    if not settings.templates_dir.exists():
        return templates
    for directory in sorted(settings.templates_dir.iterdir()):
        if directory.is_dir() and (directory / TEMPLATE_FILE).exists():
            try:
                templates.append(load_template(settings, directory.name))
            except Exception as e:
                logger.warning("テンプレートを読み込めませんでした: %s (%s)", directory.name, e)
    return templates


# --------------------------------------------------------------------------
# セッション
# --------------------------------------------------------------------------


def session_path(settings: Settings, session_id: str) -> Path:
    return settings.session_dir(session_id) / SESSION_FILE


def load_session(settings: Settings, session_id: str) -> SessionState:
    path = session_path(settings, session_id)
    if not path.exists():
        raise NotFoundError(f"セッションが見つかりません: {session_id}")
    return SessionState.model_validate_json(_read_text(path))


def save_session(settings: Settings, session: SessionState) -> None:
    _write_json(session_path(settings, session.session_id), session)


def list_session_ids(settings: Settings) -> list[str]:
    if not settings.scans_dir.exists():
        return []
    return sorted(
        (d.name for d in settings.scans_dir.iterdir() if (d / SESSION_FILE).exists()),
        reverse=True,
    )


# --------------------------------------------------------------------------
# 採点結果
# --------------------------------------------------------------------------


def result_path(settings: Settings, session_id: str) -> Path:
    return settings.result_dir(session_id) / RESULT_FILE


def graded_pdf_path(settings: Settings, session_id: str) -> Path:
    return settings.result_dir(session_id) / GRADED_PDF


def load_result(settings: Settings, session_id: str) -> Result:
    path = result_path(settings, session_id)
    if not path.exists():
        raise NotFoundError(f"採点結果が見つかりません: {session_id}")
    return Result.model_validate_json(_read_text(path))


def save_result(settings: Settings, result: Result) -> None:
    _write_json(result_path(settings, result.session_id), result)


def read_json(path: Path) -> dict:
    return json.loads(_read_text(path))

"""FastAPI エントリポイント（SPEC §7.4, §9.10）。

長時間処理（OCR・採点）は POST を即座に返し、進捗は ``GET /api/sessions/{id}`` の
ポーリングで確認する方式（SPEC §9.10）。WebSocket は使わない。
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from threading import RLock

from fastapi import BackgroundTasks, Body, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app import logger as log_module
from app import pdfutil, refs as refs_module, scanner, storage, template_builder
from app.annotator import build_graded_pdf
from app.config import ConfigError, MissingCredentialError, get_settings
from app.runtime import is_frozen, resource_dir
from app.updater import download_and_install, latest_release
from app.version import __version__
from app.grader import grade_session
from app.llm import get_answer_check_backend, get_grading_backend
from app.models import (
    RefNode,
    Result,
    SessionState,
    StatsResponse,
    Template,
    TemplateSummary,
    TranscriptionUpdateRequest,
)
from app.ocr import get_ocr_backend
from app.storage import NotFoundError
from app.registration import RegistrationPreviewRequest, RegistrationGroup, plan_registration

# Windows の既定ロケール（cp932）のままだと日本語ログが化けるため UTF-8 に固定する。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - 再設定不可の環境
            pass

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

_ID_PATTERN = re.compile(r"^[A-Za-z0-9._\-()（）ぁ-んァ-ヶ一-龠]+$")

settings = get_settings()
# 単一プロセスのローカルアプリで、採点開始と転記保存の競合を防ぐ。
_session_mutation_lock = RLock()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.ensure_dirs()
    # OCR の認証情報は起動時ハードゲート（SPEC §10.3）
    settings.validate_ocr_or_die()
    # 採点プロバイダのキーは起動をブロックせず警告に留める（採点実行時に 503 で返す）
    warning = settings.warn_missing_grading_credentials()
    if warning:
        logger.warning("採点は現在利用できません: %s", warning)
    logger.info(
        "起動しました OCR=%s 採点プロバイダ=%s", settings.ocr_backend, settings.grading_provider
    )
    yield


app = FastAPI(title="模試自動採点システム", version="1.0.0", lifespan=lifespan)


def _validate_id(value: str, label: str) -> str:
    if not value or not _ID_PATTERN.match(value) or value in (".", ".."):
        raise HTTPException(status_code=400, detail=f"{label} に使えない文字が含まれています: {value}")
    return value


def _slug_from_filename(filename: str) -> str:
    """アップロードファイル名からテンプレート ID 候補を作る（拡張子除去 → 使えない文字を - に）。"""
    stem = re.sub(r"\.pdf$", "", filename or "", flags=re.IGNORECASE)
    slug = re.sub(r"[^A-Za-z0-9._\-()（）ぁ-んァ-ヶ一-龠]+", "-", stem)
    return slug.strip("-._ 　")


def _safe_child(base: Path, name: str) -> Path:
    """base 配下のファイルのみを許可する（パストラバーサル対策）。"""
    target = (base / name).resolve()
    if not target.is_relative_to(base.resolve()):
        raise HTTPException(status_code=400, detail="不正なパスです。")
    return target


def _load_template_or_404(template_id: str) -> Template:
    try:
        return storage.load_template(settings, template_id)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except storage.TemplateSchemaError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


def _load_session_or_404(session_id: str) -> SessionState:
    try:
        return storage.load_session(settings, session_id)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# --------------------------------------------------------------------------
# テンプレート API（SPEC §7.4）
# --------------------------------------------------------------------------


@app.get("/api/templates", response_model=list[TemplateSummary])
def list_templates() -> list[TemplateSummary]:
    return storage.list_templates(settings)


@app.post("/api/templates", response_model=Template)
async def create_template(
    template_id: str = Form(""),
    title: str = Form(""),
    file: UploadFile = File(...),
) -> Template:
    """元 PDF をアップロードしてテンプレートを作成し、blank.pdf を生成する。

    ``template_id`` / ``title`` が空ならアップロードファイル名から補う。
    """
    explicit_id = (template_id or "").strip()
    template_id = explicit_id or _slug_from_filename(file.filename or "")[:80] or "answer-sheet"
    if not explicit_id and re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", template_id, re.IGNORECASE):
        template_id = "sheet-" + template_id
    title = (title or "").strip() or re.sub(r"\.pdf$", "", file.filename or "", flags=re.IGNORECASE)
    _validate_id(template_id, "テンプレート ID")
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="元ファイルは PDF を指定してください。")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="空のPDFは登録できません。ファイルを選び直してください。")
    try:
        pdfutil.validate_pdf_bytes(content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    # ディレクトリを排他的に確保する。同名の自動登録や同時登録で既存の用紙を上書きしない。
    base_id = template_id
    suffix = 1
    while True:
        directory = _safe_child(settings.templates_dir, template_id)
        try:
            directory.mkdir(parents=True, exist_ok=False)
            break
        except FileExistsError:
            if explicit_id:
                raise HTTPException(status_code=409, detail=f"既に存在する管理用IDです: {template_id}。IDを空欄にすると自動で設定します。")
            suffix += 1
            template_id = f"{base_id}-{suffix}"
        except OSError as e:
            raise HTTPException(status_code=400, detail="用紙を登録できませんでした。管理用IDを空欄にして再試行してください。") from e
    try:
        (directory / "refs").mkdir()
        source_pdf = directory / "source.pdf"
        source_pdf.write_bytes(content)
        build = template_builder.build_blank_pdf(
            source_pdf, directory / "blank.pdf", template_id, dpi=settings.scan_dpi
        )
        template = Template(
            template_id=template_id, title=title, dpi=settings.scan_dpi, pages=build.pages
        )
        storage.save_template(settings, template)
    except Exception as e:
        shutil.rmtree(directory, ignore_errors=True)
        raise HTTPException(status_code=400, detail=f"blank.pdf の生成に失敗しました: {e}") from e

    for warning in build.warnings:
        logger.warning("テンプレート %s: %s", template_id, warning)
    return template


@app.post("/api/templates/registration-preview", response_model=list[RegistrationGroup])
def registration_preview(payload: RegistrationPreviewRequest) -> list[RegistrationGroup]:
    try:
        return plan_registration(payload.filenames)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/templates/register-files")
async def register_files(
    files: list[UploadFile] = File(...), title: str = Form(""), template_id: str = Form("")
) -> dict:
    """対応関係を再検証し、各用紙とその解説だけを登録する。"""
    try:
        groups = plan_registration([file.filename or "" for file in files])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if len(groups) > 1 and (title.strip() or template_id.strip()):
        raise HTTPException(status_code=400, detail="複数の用紙を登録するときは名前と管理用IDを空欄にしてください。共通名から自動設定します。")
    uploads = {file.filename: file for file in files}
    for upload in files:
        if not await upload.read(1):
            raise HTTPException(status_code=400, detail=f"空のファイルは登録できません: {upload.filename}")
        await upload.seek(0)
    templates, failures = [], []
    for group in groups:
        template = None
        try:
            template = await create_template(template_id=template_id, title=title.strip() or group.title, file=uploads[group.sheet])
            directory = _safe_child(settings.templates_dir, template.template_id)
            for name in group.references:
                _safe_child(directory / "refs", name).write_bytes(await uploads[name].read())
            template.default_refs = [f"refs/{name}" for name in group.references]
            storage.save_template(settings, template)
            templates.append({"sheet": group.sheet, "template": template.model_dump(mode="json")})
        except Exception as e:
            if template is not None:
                # このリクエストで新規に作った用紙だけを片付け、再試行可能にする。
                directory = _safe_child(settings.templates_dir, template.template_id)
                shutil.rmtree(directory, ignore_errors=True)
            failures.append({"sheet": group.sheet, "message": str(e.detail) if isinstance(e, HTTPException) else "参照資料を保存できませんでした。もう一度お試しください。"})
    return {"templates": templates, "failures": failures}


@app.get("/api/templates/{template_id}", response_model=Template)
def get_template(template_id: str) -> Template:
    return _load_template_or_404(_validate_id(template_id, "テンプレート ID"))


@app.put("/api/templates/{template_id}", response_model=Template)
def update_template(template_id: str, payload: Template = Body(...)) -> Template:
    """領域指定エディタからの保存。ページ情報（実測マーカー）は既存値を維持する。"""
    _validate_id(template_id, "テンプレート ID")
    current = _load_template_or_404(template_id)
    ids = [q.id for q in payload.questions]
    if len(ids) != len(set(ids)):
        raise HTTPException(status_code=400, detail="設問 ID が重複しています。")
    page_numbers = {page.page_no for page in current.pages}
    for question in payload.questions:
        if any(region.page_no not in page_numbers for region in question.regions):
            raise HTTPException(status_code=400, detail=f"{question.id}: 存在しないページが指定されています。")
    updated = payload.model_copy(
        update={
            "template_id": current.template_id,
            "created_at": current.created_at,
            "pages": current.pages,
            "source_pdf": current.source_pdf,
            "blank_pdf": current.blank_pdf,
            "dpi": current.dpi,
            "schema_version": current.schema_version,
        }
    )
    storage.save_template(settings, updated)
    return updated


@app.delete("/api/templates/{template_id}")
def delete_template(template_id: str) -> dict[str, str]:
    _validate_id(template_id, "テンプレート ID")
    directory = settings.template_dir(template_id)
    if not directory.exists():
        raise HTTPException(status_code=404, detail=f"テンプレートが見つかりません: {template_id}")
    for session_id in storage.list_session_ids(settings):
        if storage.load_session(settings, session_id).template_id == template_id:
            raise HTTPException(status_code=409, detail="採点セッションで使用しているテンプレートは削除できません。転記・採点結果の参照に必要です。")
    shutil.rmtree(directory)
    return {"status": "deleted", "template_id": template_id}


@app.get("/api/templates/{template_id}/page/{page_no}.png")
def template_page_png(template_id: str, page_no: int, dpi: int = Query(150, ge=36, le=600)) -> Response:
    """ブランク PDF のページ画像（エディタの表示用）。"""
    _validate_id(template_id, "テンプレート ID")
    blank = settings.template_dir(template_id) / "blank.pdf"
    if not blank.exists():
        raise HTTPException(status_code=404, detail="blank.pdf が見つかりません。")
    try:
        png = pdfutil.render_pdf_page_png(blank, page_no, dpi)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return Response(content=png, media_type="image/png")


@app.get("/api/templates/{template_id}/blank.pdf")
def template_blank_pdf(template_id: str) -> FileResponse:
    _validate_id(template_id, "テンプレート ID")
    blank = settings.template_dir(template_id) / "blank.pdf"
    if not blank.exists():
        raise HTTPException(status_code=404, detail="blank.pdf が見つかりません。")
    return FileResponse(blank, media_type="application/pdf", filename=f"{template_id}_blank.pdf")


@app.get("/api/templates/{template_id}/refs", response_model=list[RefNode])
def get_refs(template_id: str) -> list[RefNode]:
    _validate_id(template_id, "テンプレート ID")
    return refs_module.build_ref_tree(settings.template_dir(template_id) / "refs")


@app.post("/api/templates/{template_id}/refs", response_model=list[RefNode])
async def upload_refs(
    template_id: str, files: list[UploadFile] = File(...), subdir: str = Form("")
) -> list[RefNode]:
    """参照資料（模範解答・採点基準）をアップロードする。"""
    _validate_id(template_id, "テンプレート ID")
    refs_dir = settings.template_dir(template_id) / "refs"
    target_dir = _safe_child(refs_dir, subdir) if subdir else refs_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    for upload in files:
        name = Path(upload.filename or "").name
        if not name:
            continue
        if Path(name).suffix.lower() not in refs_module.REF_SUFFIXES:
            raise HTTPException(
                status_code=400,
                detail=f"対応していない形式です（.pdf .md .txt のみ）: {name}",
            )
        _safe_child(target_dir, name).write_bytes(await upload.read())
    return refs_module.build_ref_tree(refs_dir)


# --------------------------------------------------------------------------
# セッション API（SPEC §9.10）
# --------------------------------------------------------------------------


def _run_scan(session_id: str, input_paths: list[Path], template: Template) -> None:
    """バックグラウンドでスキャン処理（正規化〜OCR）を実行する。"""
    try:
        backend = get_ocr_backend(settings)
        candidates = storage.load_all_templates(settings)
        session = scanner.process_scan(
            settings, session_id, input_paths, template, candidates, backend
        )
    except Exception as e:  # 失敗も session.json に残して UI から見えるようにする
        logger.exception("スキャン処理に失敗しました: %s", session_id)
        session = SessionState(
            session_id=session_id,
            template_id=template.template_id,
            status="failed",
            error=f"スキャン処理に失敗しました: {e}",
        )
    storage.save_session(settings, session)


@app.post("/api/sessions")
async def create_session(
    background: BackgroundTasks,
    template_id: str = Form(...),
    files: list[UploadFile] = File(...),
) -> dict[str, str]:
    """スキャン投入。正規化・切り出し・OCR はバックグラウンドで実行する。"""
    _validate_id(template_id, "テンプレート ID")
    template = _load_template_or_404(template_id)
    if not template.questions:
        raise HTTPException(status_code=400, detail="設問が未設定のテンプレートです。先に領域を指定してください。")
    missing_regions = [q.id for q in template.questions if not q.regions]
    if missing_regions:
        raise HTTPException(status_code=400, detail=f"解答領域が未設定です: {', '.join(missing_regions)}。先に領域を指定してください。")
    for upload in files:
        if Path(upload.filename or "").suffix.lower() not in pdfutil.PDF_SUFFIXES | pdfutil.IMAGE_SUFFIXES:
            raise HTTPException(status_code=400, detail=f"対応していない入力形式です: {upload.filename}")

    session_id = scanner.make_session_id(template_id)
    session_dir = settings.session_dir(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)

    input_paths: list[Path] = []
    for index, upload in enumerate(files):
        suffix = Path(upload.filename or "").suffix.lower()
        if suffix not in pdfutil.PDF_SUFFIXES | pdfutil.IMAGE_SUFFIXES:
            raise HTTPException(status_code=400, detail=f"対応していない入力形式です: {upload.filename}")
        path = session_dir / (f"input{suffix}" if len(files) == 1 else f"input_{index:02d}{suffix}")
        path.write_bytes(await upload.read())
        input_paths.append(path)

    storage.save_session(
        settings,
        SessionState(
            session_id=session_id,
            template_id=template_id,
            status="processing",
            progress="処理を開始しています",
        ),
    )
    background.add_task(_run_scan, session_id, input_paths, template)
    return {"session_id": session_id, "status": "processing"}


@app.get("/api/sessions/{session_id}", response_model=SessionState)
def get_session(session_id: str) -> SessionState:
    return _load_session_or_404(_validate_id(session_id, "セッション ID"))


@app.put("/api/sessions/{session_id}/transcriptions", response_model=SessionState)
def update_transcriptions(
    session_id: str, payload: TranscriptionUpdateRequest = Body(...)
) -> SessionState:
    """転記テキストの修正を反映する（SPEC §9.4）。"""
    _validate_id(session_id, "セッション ID")
    with _session_mutation_lock:
        session = _load_session_or_404(session_id)
        if session.status in ("processing", "grading"):
            raise HTTPException(status_code=409, detail="処理中は転記を保存できません。完了までお待ちください。")
        ids = [item.question_id for item in payload.items]
        if len(ids) != len(set(ids)) or any(session.transcription(qid) is None for qid in ids):
            raise HTTPException(status_code=400, detail="転記の設問 ID が重複しているか、存在しません。ページを再読み込みしてください。")
        changed = False
        for item in payload.items:
            current = session.transcription(item.question_id)
            if item.transcription != current.transcription:
                current.transcription = item.transcription
                current.transcription_edited = True
                changed = True
            changed |= current.is_blank != item.is_blank
            current.is_blank = item.is_blank
        if changed and session.status == "graded":
            session.status = "ready"
            session.progress = "転記を修正しました。再採点してください。"
        storage.save_session(settings, session)
        return session


def _template_needs_llm(template: Template) -> bool:
    """LLM 採点を要する設問が 1 つでもあるか。「答えのみ」だけなら採点用 API キー不要。"""
    return any(q.answer_format != "answer_only" for q in template.questions)


def _run_grading(session_id: str) -> None:
    """バックグラウンドで採点 → 赤入れ PDF → ログ出力まで実行する。"""
    session = storage.load_session(settings, session_id)
    try:
        template = storage.load_template(settings, session.template_id)
        backend = get_grading_backend(settings) if _template_needs_llm(template) else None
        answer_check_backend = get_answer_check_backend(settings)

        def progress(message: str) -> None:
            session.progress = message
            storage.save_session(settings, session)

        result: Result = grade_session(
            settings,
            session,
            template,
            backend,
            progress=progress,
            answer_check_backend=answer_check_backend,
        )
        storage.save_result(settings, result)

        session.progress = "赤入れ PDF を生成しています"
        storage.save_session(settings, session)
        build_graded_pdf(
            settings, session, template, result, storage.graded_pdf_path(settings, session_id)
        )

        log_module.write_logs(settings, result, template)
        session.warnings.extend(result.warnings)
        session.status = "graded"
        session.progress = "採点が完了しました"
        session.error = None
    except Exception as e:
        logger.exception("採点に失敗しました: %s", session_id)
        session.status = "failed"
        session.error = f"採点に失敗しました: {e}"
    storage.save_session(settings, session)


@app.post("/api/sessions/{session_id}/grade")
def grade(session_id: str, background: BackgroundTasks) -> dict[str, str]:
    """採点を実行する（バックグラウンド）。キー未設定は 503 で明示する。"""
    _validate_id(session_id, "セッション ID")
    with _session_mutation_lock:
        session = _load_session_or_404(session_id)
        if session.status in ("processing", "grading"):
            raise HTTPException(status_code=409, detail="このセッションは処理中です。完了までお待ちください。")
        if not session.crops:
            raise HTTPException(status_code=400, detail="転記結果がありません。先にスキャンを投入してください。")

        # 答えのみのテンプレートは採点用 API キー不要。
        template = _load_template_or_404(session.template_id)
        if _template_needs_llm(template):
            try:
                settings.grading_credentials()
            except MissingCredentialError as e:
                raise HTTPException(status_code=503, detail=str(e)) from e

        session.status = "grading"
        session.progress = "採点を開始しています"
        session.error = None
        storage.save_session(settings, session)
    background.add_task(_run_grading, session_id)
    return {"session_id": session_id, "status": "grading"}


@app.get("/api/sessions/{session_id}/result", response_model=Result)
def get_result(session_id: str) -> Result:
    _validate_id(session_id, "セッション ID")
    if _load_session_or_404(session_id).status != "graded":
        raise HTTPException(status_code=409, detail="現在の転記に対する採点結果はまだありません。採点完了後に確認してください。")
    try:
        return storage.load_result(settings, session_id)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.get("/api/sessions/{session_id}/graded.pdf")
def get_graded_pdf(session_id: str) -> FileResponse:
    _validate_id(session_id, "セッション ID")
    if _load_session_or_404(session_id).status != "graded":
        raise HTTPException(status_code=409, detail="現在の転記に対する赤入れ PDF はまだありません。採点完了後に確認してください。")
    path = storage.graded_pdf_path(settings, session_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="赤入れ PDF がまだ生成されていません。")
    return FileResponse(path, media_type="application/pdf", filename=f"{session_id}_graded.pdf")


@app.get("/api/sessions/{session_id}/crops/{filename}")
def get_crop(session_id: str, filename: str) -> FileResponse:
    _validate_id(session_id, "セッション ID")
    path = _safe_child(settings.session_dir(session_id) / "crops", filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="切り出し画像が見つかりません。")
    return FileResponse(path, media_type="image/png")


@app.get("/api/sessions/{session_id}/normalized/{filename}")
def get_normalized(session_id: str, filename: str) -> FileResponse:
    """正規化済みページ画像（失敗ページの確認や UI 表示用）。"""
    _validate_id(session_id, "セッション ID")
    path = _safe_child(settings.session_dir(session_id) / "normalized", filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="正規化画像が見つかりません。")
    return FileResponse(path, media_type="image/png")


@app.get("/api/sessions/{session_id}/failed/{filename}")
def get_failed_page(session_id: str, filename: str) -> FileResponse:
    """位置合わせに失敗したページのサムネイル（SPEC §10.1）。"""
    _validate_id(session_id, "セッション ID")
    path = _safe_child(settings.session_dir(session_id) / "failed", filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="失敗ページの画像が見つかりません。")
    return FileResponse(path, media_type="image/png")


@app.get("/api/sessions")
def list_sessions() -> list[dict[str, str]]:
    """セッション一覧（新しい順）。"""
    out: list[dict[str, str]] = []
    for session_id in storage.list_session_ids(settings):
        try:
            session = storage.load_session(settings, session_id)
        except Exception:
            continue
        out.append(
            {
                "session_id": session.session_id,
                "template_id": session.template_id,
                "status": session.status,
                "created_at": session.created_at.isoformat(),
            }
        )
    return sorted(out, key=lambda item: item["created_at"], reverse=True)[:50]


@app.get("/api/stats", response_model=StatsResponse)
def get_stats(since: str | None = Query(None, description="YYYY-MM-DD")) -> StatsResponse:
    try:
        return log_module.load_stats(settings, since)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/api/config")
def get_config() -> dict[str, object]:
    """UI 表示用の設定情報（キーそのものは返さない）。"""
    grading_error = settings.warn_missing_grading_credentials()
    from app.ocr import get_ocr_backend as _factory  # 遅延 import（循環回避）

    try:
        supports_words = _factory(settings).supports_words
    except Exception:
        supports_words = settings.ocr_backend != "claude_vision"
    return {
        "version": __version__,
        "updates_enabled": bool(settings.update_repository) and is_frozen(),
        "ocr_backend": settings.ocr_backend,
        "ocr_supports_words": supports_words,
        "grading_provider": settings.grading_provider,
        "grading_ready": grading_error is None,
        "grading_error": grading_error,
        "double_grading": settings.double_grading,
    }


@app.get("/api/update")
def check_update() -> dict[str, object]:
    """Check the configured public GitHub repository for a newer installer."""
    release = latest_release(settings.update_repository)
    if release is None:
        return {"available": False, "version": __version__}
    return {"available": True, "version": __version__, "latest_version": release.version, "notes": release.notes}


@app.post("/api/update/install")
def install_update() -> dict[str, object]:
    release = latest_release(settings.update_repository)
    if release is None:
        raise HTTPException(status_code=409, detail="利用可能な更新はありません。")
    try:
        download_and_install(release, lambda: os._exit(0))
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": "更新をダウンロードしました。アプリを閉じてインストールを開始します。"}


# --------------------------------------------------------------------------
# 静的ファイル
# --------------------------------------------------------------------------

_STATIC_DIR = resource_dir() / "app" / "static"
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/")
def index() -> RedirectResponse:
    return RedirectResponse(url="/static/index.html")


def main() -> None:
    """``python -m app.main`` で起動する。"""
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()

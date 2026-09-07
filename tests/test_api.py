"""API レイヤーのテスト（M2 の検証: 領域を保存して再読込で復元されること）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.config import Settings


@pytest.fixture
def client(settings, monkeypatch):
    """テスト用のデータ領域を向いた TestClient。"""
    monkeypatch.setattr(main_module, "settings", settings)
    # startup で OCR 認証を要求されないようにする（この層のテストでは OCR を使わない）。
    # pydantic のモデルインスタンスにはメソッドを差せないのでクラス側を差し替える。
    monkeypatch.setattr(Settings, "validate_ocr_or_die", lambda self: None)
    with TestClient(main_module.app) as c:
        yield c


def test_list_templates_empty(client):
    assert client.get("/api/templates").json() == []


def test_create_template_and_editor_roundtrip(client, settings, source_pdf):
    """SPEC M2 の検証: 領域を指定して保存 → 再読込で同じ位置に復元されること。"""
    response = client.post(
        "/api/templates",
        data={"template_id": "tpl-api", "title": "APIテスト"},
        files={"file": ("source.pdf", source_pdf.read_bytes(), "application/pdf")},
    )
    assert response.status_code == 200, response.text
    template = response.json()
    assert template["template_id"] == "tpl-api"
    assert len(template["pages"]) == 1
    assert len(template["pages"][0]["markers"]) == 3
    assert len(template["pages"][0]["qr_quad"]) == 4

    template["questions"] = [
        {
            "id": "1-(1)",
            "type": "和文英訳",
            "max_score": 15,
            "regions": [{"page_no": 1, "rect": [0.12, 0.31, 0.88, 0.44]}],
            "refs": [],
            "note": "仮定法を重視",
            "language": "en",
        }
    ]
    saved = client.put("/api/templates/tpl-api", json=template)
    assert saved.status_code == 200, saved.text

    reloaded = client.get("/api/templates/tpl-api").json()
    assert reloaded["questions"][0]["regions"][0]["rect"] == [0.12, 0.31, 0.88, 0.44]
    assert reloaded["questions"][0]["max_score"] == 15
    assert reloaded["questions"][0]["language"] == "en"
    # ページ情報（実測マーカー・QR 隅）は編集で失われない
    assert len(reloaded["pages"][0]["markers"]) == 3
    assert len(reloaded["pages"][0]["qr_quad"]) == 4


def test_create_template_rejects_duplicate(client, source_pdf):
    files = {"file": ("source.pdf", source_pdf.read_bytes(), "application/pdf")}
    client.post("/api/templates", data={"template_id": "dup"}, files=files)
    again = client.post(
        "/api/templates",
        data={"template_id": "dup"},
        files={"file": ("source.pdf", source_pdf.read_bytes(), "application/pdf")},
    )
    assert again.status_code == 409


def test_create_template_rejects_non_pdf(client):
    response = client.post(
        "/api/templates",
        data={"template_id": "bad"},
        files={"file": ("a.png", b"not a pdf", "image/png")},
    )
    assert response.status_code == 400


def test_create_template_derives_id_and_title_from_filename(client, source_pdf):
    """template_id / title 未指定なら アップロードファイル名から補う。"""
    response = client.post(
        "/api/templates",
        data={},  # ID もタイトルも渡さない
        files={"file": ("京大英語 2021 第1問.pdf", source_pdf.read_bytes(), "application/pdf")},
    )
    assert response.status_code == 200, response.text
    template = response.json()
    assert template["template_id"] == "京大英語-2021-第1問"
    assert template["title"] == "京大英語 2021 第1問"


def test_create_template_keeps_explicit_id_over_filename(client, source_pdf):
    response = client.post(
        "/api/templates",
        data={"template_id": "explicit-id"},
        files={"file": ("something else.pdf", source_pdf.read_bytes(), "application/pdf")},
    )
    assert response.status_code == 200
    assert response.json()["template_id"] == "explicit-id"
    assert response.json()["title"] == "something else"  # title は空だったのでファイル名


def test_invalid_rect_is_rejected(client, settings, built_template):
    payload = built_template.model_dump(mode="json")
    payload["questions"][0]["regions"][0]["rect"] = [0.5, 0.1, 0.2, 0.4]  # x1 < x0
    response = client.put(f"/api/templates/{built_template.template_id}", json=payload)
    assert response.status_code == 422


def test_page_png_endpoint(client, built_template):
    response = client.get(f"/api/templates/{built_template.template_id}/page/1.png?dpi=100")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_blank_pdf_endpoint(client, built_template):
    response = client.get(f"/api/templates/{built_template.template_id}/blank.pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"


def test_refs_upload_and_tree(client, built_template):
    template_id = built_template.template_id
    response = client.post(
        f"/api/templates/{template_id}/refs",
        files=[("files", ("基準.md", "採点基準".encode("utf-8"), "text/markdown"))],
    )
    assert response.status_code == 200
    tree = response.json()
    assert any(node["name"] == "基準.md" for node in tree)
    assert client.get(f"/api/templates/{template_id}/refs").json() == tree


def test_refs_upload_rejects_bad_extension(client, built_template):
    response = client.post(
        f"/api/templates/{built_template.template_id}/refs",
        files=[("files", ("evil.exe", b"MZ", "application/octet-stream"))],
    )
    assert response.status_code == 400


def test_template_not_found(client):
    assert client.get("/api/templates/missing").status_code == 404


def test_old_layout_template_is_rejected(client, settings):
    """schema_version < 2（旧レイアウト）の template.json は 409 で弾く。"""
    directory = settings.template_dir("old-v1")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "template.json").write_text(
        '{"schema_version": 1, "template_id": "old-v1", "pages": [], "questions": []}',
        encoding="utf-8",
    )
    response = client.get("/api/templates/old-v1")
    assert response.status_code == 409
    assert "作り直して" in response.json()["detail"]


def test_grade_returns_503_without_api_key(client, settings, built_template):
    """採点キー未設定は起動を止めず、採点時に 503 で明示する。"""
    from app.models import CropInfo, SessionState
    from app.storage import save_session

    settings.grading_provider = "anthropic"
    settings.anthropic_api_key = ""
    session = SessionState(
        session_id="s-503",
        template_id=built_template.template_id,
        status="ready",
        crops=[
            CropInfo(
                question_id="1-(1)", index=0, page_no=1, region_rect=(0.1, 0.1, 0.9, 0.2),
                crop_origin_canvas=(0, 0), crop_w=10, crop_h=10, margin_px=8,
            )
        ],
    )
    save_session(settings, session)

    response = client.post("/api/sessions/s-503/grade")
    assert response.status_code == 503
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]


def test_grade_answer_only_template_allowed_without_key(client, settings, built_template):
    """答えのみだけのテンプレートは採点キー未設定でも 200（決定的照合で完結する）。"""
    from app.models import CropInfo, QuestionTranscription, SessionState
    from app.storage import save_session, save_template

    settings.grading_provider = "anthropic"
    settings.anthropic_api_key = ""
    settings.answer_only_llm_fallback = False
    built_template.questions[0].answer_format = "answer_only"
    built_template.questions[0].answer_key = "42"
    save_template(settings, built_template)

    session = SessionState(
        session_id="s-answer-only",
        template_id=built_template.template_id,
        status="ready",
        crops=[
            CropInfo(
                question_id="1-(1)", index=0, page_no=1, region_rect=(0.1, 0.1, 0.9, 0.2),
                crop_origin_canvas=(0, 0), crop_w=10, crop_h=10, margin_px=8,
            )
        ],
        transcriptions=[QuestionTranscription(question_id="1-(1)", transcription="42")],
    )
    save_session(settings, session)

    response = client.post("/api/sessions/s-answer-only/grade")
    assert response.status_code == 200


def test_session_not_found(client):
    assert client.get("/api/sessions/missing").status_code == 404


def test_path_traversal_is_blocked(client, built_template):
    response = client.get(f"/api/sessions/{built_template.template_id}/crops/..%2F..%2Fsecrets.txt")
    assert response.status_code in (400, 404)


def test_stats_endpoint(client):
    response = client.get("/api/stats")
    assert response.status_code == 200
    assert response.json()["question_count"] == 0


def test_config_endpoint(client, settings):
    settings.anthropic_api_key = ""
    body = client.get("/api/config").json()
    assert body["ocr_backend"] == "google_vision"
    assert body["grading_ready"] is False
    assert "ANTHROPIC_API_KEY" in body["grading_error"]


@pytest.mark.parametrize("status", ["processing", "grading"])
def test_processing_session_cannot_be_edited_or_graded(client, settings, status):
    from app.models import QuestionTranscription, SessionState
    from app.storage import load_session, save_session

    session = SessionState(session_id="busy", template_id="tpl", status=status,
                           transcriptions=[QuestionTranscription(question_id="Q1", transcription="original")])
    save_session(settings, session)
    response = client.put("/api/sessions/busy/transcriptions", json={
        "items": [{"question_id": "Q1", "transcription": "changed"}]
    })
    assert response.status_code == 409
    assert client.post("/api/sessions/busy/grade").status_code == 409
    stored = load_session(settings, "busy")
    assert stored.status == status
    assert stored.transcriptions[0].transcription == "original"


@pytest.mark.parametrize("change", [{"transcription": "corrected"}, {"is_blank": True}])
def test_editing_graded_transcription_invalidates_old_result(client, settings, change):
    from app.models import QuestionTranscription, SessionState
    from app.storage import save_session

    save_session(settings, SessionState(session_id="edited", template_id="tpl", status="graded",
        transcriptions=[QuestionTranscription(question_id="Q1", transcription="original")]))
    item = {"question_id": "Q1", "transcription": "original", "is_blank": False, **change}
    response = client.put("/api/sessions/edited/transcriptions", json={"items": [item]})
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert client.get("/api/sessions/edited/result").status_code == 409
    assert client.get("/api/sessions/edited/graded.pdf").status_code == 409


def test_saving_unchanged_transcription_keeps_graded_result(client, settings):
    from app.models import QuestionTranscription, SessionState
    from app.storage import save_session

    save_session(settings, SessionState(session_id="unchanged", template_id="tpl", status="graded",
        transcriptions=[QuestionTranscription(question_id="Q1", transcription="original")]))
    response = client.put("/api/sessions/unchanged/transcriptions", json={
        "items": [{"question_id": "Q1", "transcription": "original"}]
    })
    assert response.json()["status"] == "graded"


@pytest.mark.parametrize("ids", [["Q1", "missing"], ["Q1", "Q1"]])
def test_invalid_transcription_batch_does_not_partially_save(client, settings, ids):
    from app.models import QuestionTranscription, SessionState
    from app.storage import load_session, save_session

    save_session(settings, SessionState(session_id="batch", template_id="tpl", status="ready",
        transcriptions=[QuestionTranscription(question_id="Q1", transcription="original")]))
    response = client.put("/api/sessions/batch/transcriptions", json={
        "items": [{"question_id": qid, "transcription": "changed"} for qid in ids]
    })
    assert response.status_code == 400
    assert load_session(settings, "batch").transcriptions[0].transcription == "original"


def test_duplicate_grade_requests_start_only_one_job(client, settings, built_template, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from app.models import CropInfo, SessionState
    from app.storage import save_session, save_template

    built_template.questions[0].answer_format = "answer_only"
    save_template(settings, built_template)
    save_session(settings, SessionState(session_id="double", template_id=built_template.template_id,
        status="failed", crops=[CropInfo(question_id="1-(1)", index=0, page_no=1,
            region_rect=(0.1, 0.1, 0.9, 0.2), crop_origin_canvas=(0, 0), crop_w=10, crop_h=10, margin_px=0)]))
    jobs = []
    monkeypatch.setattr(main_module, "_run_grading", lambda sid: jobs.append(sid))
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post("/api/sessions/double/grade"), range(2)))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert jobs == ["double"]


def test_session_list_orders_by_creation_before_limiting(client, settings):
    from datetime import timedelta
    from app.models import SessionState, now_jst
    from app.storage import save_session

    newest = now_jst()
    for i in range(55):
        save_session(settings, SessionState(session_id=f"s-{i:02d}", template_id="tpl",
                                            created_at=newest - timedelta(minutes=i)))
    rows = client.get("/api/sessions").json()
    assert len(rows) == 50
    assert rows[0]["session_id"] == "s-00"
    assert rows[-1]["session_id"] == "s-49"


def test_template_rejects_duplicate_questions_and_unknown_pages(client, built_template):
    payload = built_template.model_dump(mode="json")
    payload["questions"] *= 2
    endpoint = f"/api/templates/{built_template.template_id}"
    assert client.put(endpoint, json=payload).status_code == 400
    payload["questions"] = payload["questions"][:1]
    payload["questions"][0]["regions"][0]["page_no"] = 999
    assert client.put(endpoint, json=payload).status_code == 400
    assert client.get(endpoint).json()["questions"][0]["regions"][0]["page_no"] == 1


def test_incomplete_template_cannot_start_scan(client, settings, built_template):
    from app.storage import save_template

    built_template.questions[0].regions = []
    save_template(settings, built_template)
    assert client.get("/api/templates").json()[0]["scan_ready"] is False
    response = client.post("/api/sessions", data={"template_id": built_template.template_id},
                           files={"files": ("scan.pdf", b"pdf", "application/pdf")})
    assert response.status_code == 400
    assert "解答領域" in response.json()["detail"]
    assert list(settings.scans_dir.iterdir()) == []


def test_template_in_use_cannot_be_deleted(client, settings, built_template):
    from app.models import SessionState
    from app.storage import save_session

    save_session(settings, SessionState(session_id="uses-template", template_id=built_template.template_id))
    endpoint = f"/api/templates/{built_template.template_id}"
    assert client.delete(endpoint).status_code == 409
    assert client.get(endpoint).status_code == 200


def test_safe_child_rejects_sibling_with_same_prefix(tmp_path):
    from fastapi import HTTPException

    base = tmp_path / "refs"
    with pytest.raises(HTTPException) as exc:
        main_module._safe_child(base, "../refs-other/file.txt")
    assert exc.value.status_code == 400
    assert main_module._safe_child(base, "sub/file.txt") == (base / "sub/file.txt").resolve()


def test_concurrent_json_writes_remain_valid(settings):
    from concurrent.futures import ThreadPoolExecutor
    from app.models import SessionState
    from app.storage import load_session, save_session

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: save_session(settings, SessionState(
            session_id="concurrent", template_id="tpl", progress=str(i))), range(20)))
    assert load_session(settings, "concurrent").progress in {str(i) for i in range(20)}
    assert not list(settings.session_dir("concurrent").glob("*.tmp"))


def test_automatic_template_ids_do_not_conflict(client, source_pdf):
    first = client.post("/api/templates", files={"file": ("exam.pdf", source_pdf.read_bytes(), "application/pdf")})
    second = client.post("/api/templates", files={"file": ("exam.pdf", source_pdf.read_bytes(), "application/pdf")})
    assert first.json()["template_id"] == "exam"
    assert second.json()["template_id"] == "exam-2"
    assert second.json()["title"] == "exam"


def test_registration_preview_does_not_write_data(client, settings):
    response = client.post("/api/templates/registration-preview", json={"filenames": ["exam_sheet.pdf", "exam_answers.pdf"]})
    assert response.status_code == 200
    assert response.json() == [{"title": "exam", "sheet": "exam_sheet.pdf", "references": ["exam_answers.pdf"]}]
    assert list(settings.templates_dir.iterdir()) == []


def test_named_registration_saves_paired_references(client, settings, source_pdf):
    response = client.post("/api/templates/register-files", files=[
        ("files", ("exam_answers.pdf", source_pdf.read_bytes(), "application/pdf")),
        ("files", ("exam_sheet.pdf", source_pdf.read_bytes(), "application/pdf")),
        ("files", ("exam_rubric.md", b"rubric", "text/markdown")),
    ])
    assert response.status_code == 200, response.text
    assert response.json()["failures"] == []
    template = response.json()["templates"][0]["template"]
    assert template["title"] == "exam"
    assert template["default_refs"] == ["refs/exam_answers.pdf", "refs/exam_rubric.md"]
    directory = settings.template_dir(template["template_id"])
    assert (directory / "refs/exam_rubric.md").read_bytes() == b"rubric"
    assert client.get(f"/api/templates/{template['template_id']}").json()["default_refs"] == template["default_refs"]


def test_registration_rejects_unpaired_files_before_writing(client, settings, source_pdf):
    response = client.post("/api/templates/register-files", files=[
        ("files", ("exam_sheet.pdf", source_pdf.read_bytes(), "application/pdf")),
        ("files", ("other_answers.pdf", source_pdf.read_bytes(), "application/pdf")),
    ])
    assert response.status_code == 400
    assert list(settings.templates_dir.iterdir()) == []


def test_batch_registration_reports_partial_failure_and_cleans_failed_sheet(client, settings, source_pdf):
    response = client.post("/api/templates/register-files", files=[
        ("files", ("valid_sheet.pdf", source_pdf.read_bytes(), "application/pdf")),
        ("files", ("bad_sheet.pdf", b"not a PDF", "application/pdf")),
    ])
    result = response.json()
    assert [row["sheet"] for row in result["templates"]] == ["valid_sheet.pdf"]
    assert [row["sheet"] for row in result["failures"]] == ["bad_sheet.pdf"]
    assert not settings.template_dir("bad_sheet").exists()
    assert settings.template_dir("valid_sheet").exists()

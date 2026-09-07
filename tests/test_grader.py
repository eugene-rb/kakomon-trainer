"""採点オーケストレーションのテスト（SPEC §12）。

LLM は呼ばず、バックエンドをスタブに差し替えて検証する。
"""

from __future__ import annotations

import pytest

from app.grader import (
    build_question_text,
    grade_session,
    normalize_issues,
    reconcile_score,
)
from app.llm.answer_check import AnswerCheckBackend, AnswerCheckResult
from app.llm.base import GradeResult, GradingRequest, LLMError, LLMGradingBackend
from app.models import CropInfo, Issue, Question, QuestionTranscription, SessionState


class StubBackend(LLMGradingBackend):
    """設定した結果を返すだけのバックエンド。"""

    provider = "stub"
    model = "stub-model"

    def __init__(self, results: list, fail_times: int = 0) -> None:
        self.results = list(results)
        self.calls: list[GradingRequest] = []
        self.fail_times = fail_times

    def grade(self, request: GradingRequest) -> GradeResult:
        self.calls.append(request)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise LLMError("一時的な失敗")
        return self.results[min(len(self.calls) - 1, len(self.results) - 1)]


class StubAnswerCheck(AnswerCheckBackend):
    """設定した照合結果を返すだけのフォールバックバックエンド。"""

    provider = "stub"
    model = "stub-haiku"

    def __init__(self, result: AnswerCheckResult) -> None:
        self.result = result
        self.calls: list = []

    def check(self, request):
        self.calls.append(request)
        return self.result


def _result(score: int, deductions: list[int]) -> GradeResult:
    return GradeResult(
        score=score,
        feedback="総評",
        issues=[
            {
                "quote": "quote",
                "kind": "文法",
                "tag": "時制・仮定法",
                "comment": "コメント",
                "deduction": d,
            }
            for d in deductions
        ],
        confidence="high",
    )


# --- スコア整合性の補正（SPEC §9.7）---


def test_reconcile_score_consistent():
    issues = [Issue(deduction=4)]
    score, warning = reconcile_score(11, 15, issues)
    assert (score, warning) == (11, None)


def test_reconcile_score_corrects_mismatch():
    issues = [Issue(deduction=4), Issue(deduction=2)]
    score, warning = reconcile_score(12, 15, issues)
    assert score == 9  # 15 - 6
    assert warning and "補正" in warning


def test_reconcile_score_clamps_above_max():
    score, warning = reconcile_score(20, 15, [])
    assert score == 15
    assert warning is not None


def test_reconcile_score_never_negative():
    score, _ = reconcile_score(0, 10, [Issue(deduction=30)])
    assert score == 0


# --- issues の正規化 ---


def test_normalize_issues_maps_unknown_enum_to_other():
    issues = normalize_issues(
        [{"quote": "q", "kind": "謎", "tag": "未知タグ", "comment": "c", "deduction": "3"}]
    )
    assert issues[0].kind == "その他"
    assert issues[0].tag == "その他"
    assert issues[0].deduction == 3


def test_normalize_issues_handles_broken_input():
    issues = normalize_issues([{"deduction": None}, "not a dict"])  # type: ignore[list-item]
    assert len(issues) == 1
    assert issues[0].deduction == 0


def test_build_question_text_includes_transcription():
    from app.models import Question

    question = Question(id="1-(1)", type="和文英訳", max_score=15, note="仮定法を重視", language="en")
    text = build_question_text(question, "If I had known")
    assert "1-(1)" in text and "和文英訳" in text and "15" in text
    assert "仮定法を重視" in text and "If I had known" in text


def test_build_question_text_marks_blank():
    from app.models import Question

    assert "（空欄）" in build_question_text(Question(id="q", max_score=5), "   ")


# --- セッション採点 ---


def _session(settings, template, transcription: str = "If I had known the fact") -> SessionState:
    return SessionState(
        session_id="s1",
        template_id=template.template_id,
        status="ready",
        transcriptions=[
            QuestionTranscription(question_id=q.id, transcription=transcription, is_blank=not transcription.strip())
            for q in template.questions
        ],
    )


def test_grade_session_applies_correction(settings, built_template):
    backend = StubBackend([_result(15, [4])])  # 15 と申告するが減点 4 → 11 に補正される
    result = grade_session(settings, _session(settings, built_template), built_template, backend)
    assert result.questions[0].score == 11
    assert result.total_score == 11
    assert any("補正" in w for w in result.warnings)


def test_grade_session_skips_blank_questions(settings, built_template):
    backend = StubBackend([_result(10, [])])
    session = _session(settings, built_template, transcription="")
    result = grade_session(settings, session, built_template, backend)
    assert backend.calls == []  # LLM を呼ばない
    assert result.questions[0].score == 0
    assert result.questions[0].skipped_blank is True


def test_grade_session_retries_then_succeeds(settings, built_template, monkeypatch):
    monkeypatch.setattr("app.grader.time.sleep", lambda _s: None)
    backend = StubBackend([_result(15, [0])], fail_times=2)
    result = grade_session(settings, _session(settings, built_template), built_template, backend)
    assert result.questions[0].score == 15


def test_grade_session_records_failure_after_retries(settings, built_template, monkeypatch):
    monkeypatch.setattr("app.grader.time.sleep", lambda _s: None)
    backend = StubBackend([_result(15, [])], fail_times=99)
    result = grade_session(settings, _session(settings, built_template), built_template, backend)
    assert result.questions[0].score is None
    assert result.questions[0].confidence == "low"
    assert any("採点に失敗" in w for w in result.warnings)


def test_double_grading_warns_on_large_gap(settings, built_template, monkeypatch):
    monkeypatch.setattr("app.grader.time.sleep", lambda _s: None)
    settings.double_grading = True
    settings.double_grading_threshold = 3
    # 1 回目 15点(減点0) → 2 回目 5点(減点10)
    backend = StubBackend([_result(15, []), _result(5, [10])])
    result = grade_session(settings, _session(settings, built_template), built_template, backend)
    assert result.questions[0].score == 15  # 採用するのは 1 回目
    assert any("点差" in w for w in result.warnings)


def test_grade_session_passes_crop_images_and_refs(settings, built_template):
    template_dir = settings.template_dir(built_template.template_id)
    (template_dir / "refs").mkdir(parents=True, exist_ok=True)
    (template_dir / "refs" / "基準.md").write_text("採点基準テキスト", encoding="utf-8")
    built_template.questions[0].refs = ["refs/基準.md"]

    session = _session(settings, built_template)
    backend = StubBackend([_result(15, [])])
    grade_session(settings, session, built_template, backend)

    request = backend.calls[0]
    assert [r.name for r in request.refs] == ["基準.md"]
    assert "採点基準テキスト" in request.refs[0].read_text()


# --- 答えのみ設問 ---


def _answer_only(built_template, answer_key: str, max_score: int = 5, tolerance: float = 0.0):
    built_template.questions[0].answer_format = "answer_only"
    built_template.questions[0].answer_key = answer_key
    built_template.questions[0].max_score = max_score
    built_template.questions[0].answer_tolerance = tolerance
    return built_template


def test_answer_only_correct_without_backend(settings, built_template):
    template = _answer_only(built_template, "42")
    result = grade_session(settings, _session(settings, template, "42"), template, None)
    q = result.questions[0]
    assert q.score == 5
    assert q.grader == "deterministic"
    assert q.model == "answer-match"
    assert q.issues == []
    assert result.model == "answer-match"
    assert result.provider == "deterministic"


def test_answer_only_incorrect_creates_wrong_answer_issue(settings, built_template):
    template = _answer_only(built_template, "42")
    result = grade_session(settings, _session(settings, template, "41"), template, None)
    q = result.questions[0]
    assert q.score == 0
    assert len(q.issues) == 1
    assert q.issues[0].tag == "誤答"
    assert q.issues[0].deduction == 5
    assert q.issues[0].quote == ""  # 領域枠フォールバックに載せる


def test_answer_only_numeric_tolerance_from_settings(settings, built_template):
    settings.answer_only_numeric_tolerance = 0.1
    template = _answer_only(built_template, "9.8")
    result = grade_session(settings, _session(settings, template, "9.85"), template, None)
    assert result.questions[0].score == 5


def test_answer_only_uses_fallback_when_uncertain(settings, built_template):
    template = _answer_only(built_template, "光合成", max_score=4)
    stub = StubAnswerCheck(AnswerCheckResult(correct=True, note="同義", confidence="high"))
    result = grade_session(
        settings, _session(settings, template, "光合成をする"), template, None,
        answer_check_backend=stub,
    )
    q = result.questions[0]
    assert stub.calls, "曖昧ケースはフォールバックに回るはず"
    assert q.score == 4
    assert q.grader == "llm"
    assert q.model == "stub-haiku"
    assert result.model == "stub-haiku"


def test_answer_only_no_fallback_flags_for_review(settings, built_template):
    template = _answer_only(built_template, "光合成")
    result = grade_session(settings, _session(settings, template, "光合成をする"), template, None)
    q = result.questions[0]
    assert q.score == 0
    assert q.confidence == "low"
    assert any("人手確認" in w for w in result.warnings)


def test_grade_session_mixed_records_grader_per_question(settings, built_template):
    built_template.questions[0].answer_format = "essay"
    built_template.questions.append(
        Question(id="Q2", answer_format="answer_only", answer_key="42", max_score=5)
    )
    session = SessionState(
        session_id="s1",
        template_id=built_template.template_id,
        status="ready",
        transcriptions=[
            QuestionTranscription(question_id="1-(1)", transcription="If I had known"),
            QuestionTranscription(question_id="Q2", transcription="42"),
        ],
    )
    result = grade_session(settings, session, built_template, StubBackend([_result(15, [])]))
    assert result.questions[0].grader == "llm"
    assert result.questions[0].model == "stub-model"
    assert result.questions[1].grader == "deterministic"
    assert result.questions[1].model == "answer-match"
    assert result.total_score == 20  # 15 + 5
    assert result.model == "stub-model"  # 記述式 backend を優先


# --- 採点形式ごとの観点・言語・作図 ---


def _write_crop(settings, session_id: str, question_id: str, index: int = 0) -> CropInfo:
    crops_dir = settings.session_dir(session_id) / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)
    (crops_dir / f"{question_id}_{index}.png").write_bytes(b"\x89PNG\r\n\x1a\n fake")
    return CropInfo(
        question_id=question_id, index=index, page_no=1, region_rect=(0.1, 0.1, 0.9, 0.2),
        crop_origin_canvas=(0.0, 0.0), crop_w=10, crop_h=10, margin_px=8,
    )


def test_criteria_appended_to_system_prompt(settings, built_template):
    built_template.questions[0].answer_format = "translation_ja"
    backend = StubBackend([_result(15, [])])
    grade_session(settings, _session(settings, built_template, "訳文"), built_template, backend)
    system_prompt = backend.calls[0].system_prompt
    # criteria/translation_ja.md の本文にしか無いフレーズで、確実に連結されていることを確認
    assert "多義語" in system_prompt
    assert "作用域" in system_prompt


def test_normalize_issues_keeps_zushi_tag():
    """図示の誤りタグが Literal → tool schema → normalize を通ること。"""
    issues = normalize_issues([{"tag": "図示の誤り", "kind": "形式", "comment": "軸ラベル抜け", "deduction": 2}])
    assert issues[0].tag == "図示の誤り"


def test_resolved_language_overrides_question_language(settings, built_template):
    built_template.questions[0].answer_format = "translation_en"
    built_template.questions[0].language = "ja"
    backend = StubBackend([_result(15, [])])
    grade_session(settings, _session(settings, built_template, "some english"), built_template, backend)
    assert "解答言語: en" in backend.calls[0].question_text


def test_visual_format_graded_from_image_without_transcription(settings, built_template):
    built_template.questions[0].answer_format = "graph"
    crop = _write_crop(settings, "s-graph", "1-(1)")
    session = SessionState(
        session_id="s-graph", template_id=built_template.template_id, status="ready", crops=[crop]
    )  # 転記レコードなし
    backend = StubBackend([_result(15, [])])
    result = grade_session(settings, session, built_template, backend)
    assert backend.calls, "作図は OCR 転記が無くても採点対象"
    assert result.questions[0].score == 15
    assert result.questions[0].skipped_blank is False
    assert "添付画像が本体" in backend.calls[0].question_text


def test_visual_format_without_image_is_flagged_not_zeroed(settings, built_template):
    built_template.questions[0].answer_format = "chem_structure"
    session = SessionState(
        session_id="s-nostruct", template_id=built_template.template_id, status="ready"
    )
    backend = StubBackend([_result(15, [])])
    result = grade_session(settings, session, built_template, backend)
    assert backend.calls == []
    assert result.questions[0].score is None
    assert result.questions[0].skipped_blank is False
    assert any("答案画像がありません" in w for w in result.warnings)

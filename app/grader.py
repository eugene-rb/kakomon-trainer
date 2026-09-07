"""フェーズ2後半: LLM 採点（SPEC §9.5〜§9.7）。

**設問ごとに 1 リクエスト**。まとめて投げない（採点基準の混線を防ぐため）。
**転記と採点は必ず別ステップ**（SPEC §9.1）。転記ミスと実力不足を区別できなくなるため、
このモジュールは確定済みの転記テキストだけを受け取る。
"""

from __future__ import annotations

import logging
import random
import time
from pathlib import Path

from app import answer_match
from app import refs as refs_module
from app.config import Settings
from app.llm import (
    AnswerCheckBackend,
    AnswerCheckRequest,
    GradeResult,
    GradingRequest,
    LLMError,
    LLMGradingBackend,
)
from app.models import (
    ISSUE_KINDS,
    ISSUE_TAGS,
    Issue,
    Question,
    Result,
    ResultQuestion,
    SessionState,
    Template,
    format_label,
    grading_path,
    now_jst,
    resolved_language,
)

logger = logging.getLogger(__name__)

#: SPEC §10.3: 指数バックオフで最大 3 回
MAX_RETRIES = 3
_BACKOFF_BASE = 2.0


def load_system_prompt(settings: Settings) -> str:
    """システムプロンプトを外部ファイルから読む（SPEC §9.6: 編集可能にすること）。"""
    path = settings.prompts_dir / "grading_system.md"
    if not path.exists():
        raise FileNotFoundError(f"システムプロンプトが見つかりません: {path}")
    return path.read_text(encoding="utf-8")


def load_answer_check_system_prompt(settings: Settings) -> str:
    """答えのみ設問のフォールバック照合用システムプロンプトを読む。"""
    path = settings.prompts_dir / "answer_check_system.md"
    if not path.exists():
        raise FileNotFoundError(f"システムプロンプトが見つかりません: {path}")
    return path.read_text(encoding="utf-8")


def load_grading_criteria(settings: Settings, answer_format: str) -> str:
    """設問タイプ別の採点観点（app/prompts/criteria/<値>.md）を読む。

    共通プロンプト（grading_system.md）に連結して使う。ファイルが無ければ空文字列を返し、
    共通ルールだけで採点する（欠落は採点を止めない）。
    """
    path = settings.prompts_dir / "criteria" / f"{answer_format}.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def build_question_text(question: Question, transcription: str) -> str:
    """設問情報と転記テキストのテキストブロックを作る（SPEC §9.5 の 3.）。"""
    is_visual = grading_path(question.answer_format) == "llm_visual"
    lines = [
        "# 採点対象",
        f"設問 ID: {question.id}",
        f"採点形式: {format_label(question.answer_format)}",
        f"満点: {question.max_score}",
        f"解答言語: {resolved_language(question.answer_format, question.language)}",
    ]
    if question.type:
        lines.append(f"メモ: {question.type}")
    if question.note:
        lines.append(f"採点上の注意: {question.note}")
    if is_visual:
        lines += [
            "",
            "# 答案（作図・グラフ。下の転記は OCR による粗い補助。添付画像が本体）",
            transcription if transcription.strip() else "（転記なし。添付画像を見て採点すること）",
        ]
    else:
        lines += [
            "",
            "# 答案（OCR による転記。添付画像が原本）",
            transcription if transcription.strip() else "（空欄）",
        ]
    return "\n".join(lines)


def _load_crop_images(session_dir: Path, session: SessionState, question_id: str) -> list[bytes]:
    """設問に対応する切り出し画像を記入順で読み込む。"""
    images: list[bytes] = []
    for crop in session.crops_for(question_id):
        path = session_dir / "crops" / crop.filename
        if path.exists():
            images.append(path.read_bytes())
    return images


def _call_with_retry(backend: LLMGradingBackend, request: GradingRequest) -> GradeResult:
    """指数バックオフ付きで採点を呼ぶ（SPEC §10.3）。"""
    last: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return backend.grade(request)
        except LLMError as e:
            last = e
            if attempt < MAX_RETRIES - 1:
                delay = _BACKOFF_BASE**attempt + random.uniform(0, 0.5)
                logger.warning(
                    "採点に失敗しました（%d/%d）。%.1f 秒後に再試行します: %s",
                    attempt + 1,
                    MAX_RETRIES,
                    delay,
                    e,
                )
                time.sleep(delay)
    raise last if last else LLMError("採点に失敗しました")


def normalize_issues(raw_issues: list[dict]) -> list[Issue]:
    """LLM が返した issues を検証済みモデルに変換する。

    enum 外の値が返ってきた場合は例外にせず「その他」に丸める。採点全体を
    落とすより、指摘内容を残したほうが復習ログとして価値があるため。
    """
    issues: list[Issue] = []
    for raw in raw_issues:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind", "その他"))
        tag = str(raw.get("tag", "その他"))
        if kind not in ISSUE_KINDS:
            logger.warning("未知の kind を「その他」に丸めます: %s", kind)
            kind = "その他"
        if tag not in ISSUE_TAGS:
            logger.warning("未知の tag を「その他」に丸めます: %s", tag)
            tag = "その他"
        try:
            deduction = int(raw.get("deduction", 0) or 0)
        except (TypeError, ValueError):
            deduction = 0
        issues.append(
            Issue(
                quote=str(raw.get("quote", "") or ""),
                kind=kind,  # type: ignore[arg-type]
                tag=tag,  # type: ignore[arg-type]
                comment=str(raw.get("comment", "") or ""),
                deduction=deduction,
            )
        )
    return issues


def reconcile_score(score: int, max_score: int, issues: list[Issue]) -> tuple[int, str | None]:
    """スコアと減点合計の整合性を補正する（SPEC §9.7）。

    ``max_score`` を超える score、または ``max_score - Σdeduction != score`` の場合は
    警告を出したうえで ``score = max(0, max_score - Σdeduction)`` に補正する。

    Returns:
        ``(補正後スコア, 警告文または None)``
    """
    total_deduction = sum(i.deduction for i in issues)
    expected = max(0, max_score - total_deduction)
    if score > max_score or score < 0 or score != expected:
        return expected, (
            f"スコアと減点合計が整合しません（返答 score={score}, "
            f"満点 {max_score} - 減点合計 {total_deduction} = {expected}）。{expected} 点に補正しました。"
        )
    return score, None


def grade_answer_only_question(
    settings: Settings,
    question: Question,
    transcription: str,
    edited: bool,
    *,
    answer_check_backend: AnswerCheckBackend | None,
    refs: list,
    crop_images: list[bytes],
) -> tuple[ResultQuestion, str | None]:
    """答えのみ設問を採点する（決定的照合、曖昧時のみ Haiku フォールバック）。

    Returns:
        ``(ResultQuestion, 警告文または None)``
    """
    tolerance = question.answer_tolerance or settings.answer_only_numeric_tolerance
    match = answer_match.judge(question.answer_key, transcription, tolerance=tolerance)

    warning: str | None = None
    grader: str = "deterministic"
    model: str = "answer-match"
    confidence: str = "high"

    if match.verdict == "uncertain" and answer_check_backend is not None:
        try:
            checked = answer_check_backend.check(
                AnswerCheckRequest(
                    system_prompt=load_answer_check_system_prompt(settings),
                    expected=match.expected or question.answer_key.strip(),
                    given=match.given,
                    question_text=build_question_text(question, transcription),
                    refs=refs,
                    crop_images=crop_images,
                )
            )
            correct = checked.correct
            confidence = checked.confidence
            grader = "llm"
            model = answer_check_backend.model
        except LLMError as e:
            logger.warning("フォールバック照合に失敗しました（%s）: %s", question.id, e)
            correct = False
            confidence = "low"
            warning = f"{question.id}: フォールバック照合に失敗しました（{e}）。要人手確認。"
    elif match.verdict == "uncertain":
        correct = False
        confidence = "low"
        warning = f"{question.id}: {match.detail}。要人手確認。"
    else:
        correct = match.verdict == "correct"

    expected = match.expected or question.answer_key.strip()
    if correct:
        score, issues, feedback = question.max_score, [], "正答"
    else:
        score = 0
        comment = f"正答: {expected}" if expected else "誤答"
        if match.given:
            comment += f"（解答: {match.given}）"
        if confidence == "low":
            comment += " ※要人手確認"
        issues = [
            Issue(quote="", kind="内容", tag="誤答", comment=comment, deduction=question.max_score)
        ]
        feedback = (f"要人手確認。正答: {expected}" if confidence == "low"
                    else f"誤答。正答: {expected}") if expected else "誤答"

    score, mismatch = reconcile_score(score, question.max_score, issues)
    if mismatch and warning is None:
        warning = f"{question.id}: {mismatch}"

    return (
        ResultQuestion(
            id=question.id,
            max_score=question.max_score,
            score=score,
            transcription=transcription,
            transcription_edited=edited,
            feedback=feedback,
            issues=issues,
            confidence=confidence if confidence in ("high", "medium", "low") else "low",  # type: ignore[arg-type]
            grader=grader,  # type: ignore[arg-type]
            model=model,
        ),
        warning,
    )


def grade_session(
    settings: Settings,
    session: SessionState,
    template: Template,
    backend: LLMGradingBackend | None,
    progress=None,
    *,
    answer_check_backend: AnswerCheckBackend | None = None,
) -> Result:
    """セッション全体を採点して Result を組み立てる。

    Args:
        backend: 記述式設問の採点バックエンド。答えのみ設問だけのセッションでは ``None`` 可。
        answer_check_backend: 答えのみ設問の曖昧ケースを回すフォールバック（無ければ人手確認）。
        progress: ``progress(message: str)`` 形式のコールバック（UI ポーリング用）。
    """
    system_prompt = load_system_prompt(settings)
    session_dir = settings.session_dir(session.session_id)
    template_dir = settings.template_dir(template.template_id)

    result = Result(
        session_id=session.session_id,
        template_id=template.template_id,
        graded_at=now_jst(),
        model=backend.model if backend else "",
        provider=backend.provider if backend else "",
        total_max_score=template.total_max_score,
    )
    llm_fallback_used = False

    for number, question in enumerate(template.questions, start=1):
        if progress:
            progress(f"採点中 {number}/{len(template.questions)}: {question.id}")

        transcription_state = session.transcription(question.id)
        transcription = transcription_state.transcription if transcription_state else ""
        edited = transcription_state.transcription_edited if transcription_state else False
        path = grading_path(question.answer_format)
        if transcription_state is not None:
            is_blank = transcription_state.is_blank  # 転記確認画面での人手チェックを尊重
        elif path == "llm_visual":
            is_blank = False  # 作図・グラフは OCR 転記が空でも白紙とは限らない
        else:
            is_blank = not transcription.strip()

        # 未記入とマークされた設問は LLM を呼ばずに 0 点（SPEC §9.4）
        if is_blank:
            result.questions.append(
                ResultQuestion(
                    id=question.id,
                    max_score=question.max_score,
                    score=0,
                    transcription=transcription,
                    transcription_edited=edited,
                    feedback="未記入のため 0 点です。",
                    confidence="high",
                    skipped_blank=True,
                    grader="deterministic" if path == "deterministic" else "llm",
                )
            )
            continue

        crop_images = _load_crop_images(session_dir, session, question.id)
        refs = refs_module.resolve_refs(template_dir, question.refs)

        # 答えのみ設問: 決定的照合（曖昧時のみ Haiku）
        if question.answer_format == "answer_only":
            graded_question, warning = grade_answer_only_question(
                settings,
                question,
                transcription,
                edited,
                answer_check_backend=answer_check_backend,
                refs=refs,
                crop_images=crop_images,
            )
            result.questions.append(graded_question)
            if warning:
                result.warnings.append(warning)
            if graded_question.grader == "llm":
                llm_fallback_used = True
            continue

        # LLM 採点（記述・論述・和訳・作図など。キー未設定なら失敗として残す）
        if backend is None:
            result.questions.append(
                ResultQuestion(
                    id=question.id,
                    max_score=question.max_score,
                    score=None,
                    transcription=transcription,
                    transcription_edited=edited,
                    feedback="",
                    confidence="low",
                )
            )
            result.warnings.append(f"{question.id}: 採点用 API キーが未設定のため採点できませんでした。")
            continue

        # 作図・グラフは答案画像が本体。画像が無ければ LLM を呼ばず失敗として残す。
        if path == "llm_visual" and not crop_images:
            result.questions.append(
                ResultQuestion(
                    id=question.id,
                    max_score=question.max_score,
                    score=None,
                    transcription=transcription,
                    transcription_edited=edited,
                    feedback="",
                    confidence="low",
                )
            )
            result.warnings.append(
                f"{question.id}: 作図・グラフ設問ですが答案画像がありません。採点できませんでした。"
            )
            continue

        criteria = load_grading_criteria(settings, question.answer_format)
        request = GradingRequest(
            system_prompt=system_prompt + (f"\n\n---\n\n{criteria}" if criteria else ""),
            refs=refs,
            crop_images=crop_images,
            question_text=build_question_text(question, transcription),
        )

        try:
            graded = _call_with_retry(backend, request)
        except LLMError as e:
            # 3 回失敗した設問は score=null で残し、他の設問の採点は継続する（SPEC §10.3）
            result.questions.append(
                ResultQuestion(
                    id=question.id,
                    max_score=question.max_score,
                    score=None,
                    transcription=transcription,
                    transcription_edited=edited,
                    feedback="",
                    confidence="low",
                )
            )
            result.warnings.append(f"{question.id}: 採点に失敗しました（{e}）")
            continue

        issues = normalize_issues(graded.issues)
        score, warning = reconcile_score(graded.score, question.max_score, issues)
        if warning:
            result.warnings.append(f"{question.id}: {warning}")

        # SPEC §9.7: 2 回採点して点差が閾値以上なら要確認。採用するのは 1 回目。
        if settings.double_grading:
            try:
                second = _call_with_retry(backend, request)
                second_issues = normalize_issues(second.issues)
                second_score, _ = reconcile_score(second.score, question.max_score, second_issues)
                if abs(second_score - score) >= settings.double_grading_threshold:
                    result.warnings.append(
                        f"{question.id}: 2回採点の点差が{settings.double_grading_threshold}点以上"
                        f"（{score} 点 / {second_score} 点）。要確認。"
                    )
            except LLMError as e:
                result.warnings.append(f"{question.id}: 2 回目の採点に失敗しました（{e}）")

        result.questions.append(
            ResultQuestion(
                id=question.id,
                max_score=question.max_score,
                score=score,
                transcription=transcription,
                transcription_edited=edited,
                feedback=graded.feedback,
                issues=issues,
                confidence=graded.confidence if graded.confidence in ("high", "medium", "low") else "low",
                grader="llm",
                model=backend.model,
            )
        )

    # result.model / provider は「実際に採点に使ったもの」を残す（記述式=backend、
    # 答えのみだけ＋フォールバック使用=Haiku、決定的照合のみ=answer-match）。
    if backend is None:
        if llm_fallback_used and answer_check_backend is not None:
            result.model = answer_check_backend.model
            result.provider = answer_check_backend.provider
        else:
            result.model = "answer-match"
            result.provider = "deterministic"

    result.total_score = sum(q.score or 0 for q in result.questions)
    return result

"""ログ出力と集計（SPEC §9.9）。

- ``data/logs/grading.jsonl``: 追記のみ、1 行 1 設問
- ``data/logs/review/<session_id>.md``: 復習用 Markdown
- ``GET /api/stats``: grading.jsonl を読んでタグ別の出現回数と平均得点率を返す
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

from app.config import Settings
from app.models import Result, StatsResponse, TagStat, Template

logger = logging.getLogger(__name__)

JSONL_NAME = "grading.jsonl"


def jsonl_path(settings: Settings) -> Path:
    return settings.logs_dir / JSONL_NAME


def review_path(settings: Settings, session_id: str) -> Path:
    return settings.logs_dir / "review" / f"{session_id}.md"


def append_grading_log(settings: Settings, result: Result, template: Template) -> None:
    """採点結果を 1 設問 1 行で JSONL に追記する。"""
    path = jsonl_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = result.graded_at.isoformat()

    lines: list[str] = []
    for question in result.questions:
        definition = template.question(question.id)
        rate = (
            round(question.score / question.max_score, 3)
            if question.score is not None and question.max_score
            else None
        )
        record = {
            "ts": timestamp,
            "session_id": result.session_id,
            "template_id": result.template_id,
            "question_id": question.id,
            "type": definition.type if definition else "",
            "answer_format": definition.answer_format if definition else "essay",
            "score": question.score,
            "max_score": question.max_score,
            "rate": rate,
            "tags": sorted({i.tag for i in question.issues}),
            "kinds": sorted({i.kind for i in question.issues}),
            "transcription_edited": question.transcription_edited,
            "confidence": question.confidence,
            "grader": question.grader,
            "model": question.model,
        }
        lines.append(json.dumps(record, ensure_ascii=False))

    with path.open("a", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")


def write_review_markdown(settings: Settings, result: Result, template: Template) -> Path:
    """復習用 Markdown を書き出す（SPEC §9.9 の書式）。"""
    path = review_path(settings, result.session_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    date_str = result.graded_at.strftime("%Y-%m-%d")
    lines = [f"# {date_str} {template.title or template.template_id}", ""]
    lines.append(f"**{result.total_score} / {result.total_max_score} 点**")
    lines.append("")

    for question in result.questions:
        definition = template.question(question.id)
        qtype = definition.type if definition else ""
        score = question.score if question.score is not None else "採点失敗"
        lines.append(f"## {question.id} {qtype} — {score} / {question.max_score}")
        lines.append("")
        lines.append("### 答案")
        body = question.transcription.strip() or "（未記入）"
        lines.extend(f"> {line}" for line in body.splitlines())
        lines.append("")

        if question.issues:
            lines.append("### 指摘")
            for number, issue in enumerate(question.issues, start=1):
                quote = f"「{issue.quote}」" if issue.quote else ""
                lines.append(f"{number}. **{issue.tag}**（-{issue.deduction}）{quote}")
                lines.append(f"   {issue.comment}")
            lines.append("")

        if question.feedback:
            lines.append("### 総評")
            lines.append(question.feedback)
            lines.append("")

    if result.warnings:
        lines.append("### 警告")
        lines.extend(f"- {w}" for w in result.warnings)
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_logs(settings: Settings, result: Result, template: Template) -> None:
    """JSONL と Markdown の両方を書き出す。"""
    append_grading_log(settings, result, template)
    write_review_markdown(settings, result, template)


def load_stats(settings: Settings, since: str | None = None) -> StatsResponse:
    """grading.jsonl を集計する（SPEC §9.9 の集計 API）。

    Args:
        since: ``YYYY-MM-DD``。この日以降の記録のみ対象にする。
    """
    # 引数の検証はログの有無より先に行う（ログが空でも不正な since は弾く）
    since_date: date | None = None
    if since:
        try:
            since_date = date.fromisoformat(since)
        except ValueError as e:
            raise ValueError(f"since は YYYY-MM-DD 形式で指定してください: {since}") from e

    path = jsonl_path(settings)
    response = StatsResponse(since=since)
    if not path.exists():
        return response

    tag_counts: dict[str, int] = {}
    type_rates: dict[str, list[float]] = {}
    rates: list[float] = []
    count = 0

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("壊れたログ行を読み飛ばします: %s", line[:80])
            continue

        if since_date is not None:
            try:
                ts = datetime.fromisoformat(record["ts"]).date()
            except (KeyError, ValueError):
                continue
            if ts < since_date:
                continue

        count += 1
        for tag in record.get("tags", []) or []:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
        rate = record.get("rate")
        if isinstance(rate, (int, float)):
            rates.append(float(rate))
            qtype = record.get("type") or "（種別なし）"
            type_rates.setdefault(qtype, []).append(float(rate))

    response.question_count = count
    response.average_rate = round(sum(rates) / len(rates), 3) if rates else 0.0
    response.tags = [
        TagStat(tag=tag, count=n, total_deduction=0)
        for tag, n in sorted(tag_counts.items(), key=lambda kv: kv[1], reverse=True)
    ]
    response.by_type = {
        qtype: round(sum(values) / len(values), 3) for qtype, values in sorted(type_rates.items())
    }
    return response

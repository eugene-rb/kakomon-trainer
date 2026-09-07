"""ログ出力と集計のテスト（SPEC §12）。"""

from __future__ import annotations

import json

from app.logger import append_grading_log, jsonl_path, load_stats, write_review_markdown
from app.models import Issue, Result, ResultQuestion


def _result(session_id: str, score: int, tags: list[str], template_id: str) -> Result:
    return Result(
        session_id=session_id,
        template_id=template_id,
        model="test-model",
        total_score=score,
        total_max_score=15,
        questions=[
            ResultQuestion(
                id="1-(1)",
                max_score=15,
                score=score,
                transcription="If I had known the fact",
                transcription_edited=True,
                feedback="仮定法過去完了の帰結節が誤り。",
                issues=[
                    Issue(quote="I would tell", kind="文法", tag=tag, comment="直しかた", deduction=2)
                    for tag in tags
                ],
            )
        ],
    )


def test_append_grading_log_writes_one_line_per_question(settings, built_template):
    append_grading_log(settings, _result("s1", 11, ["時制・仮定法"], built_template.template_id), built_template)
    append_grading_log(settings, _result("s2", 6, ["語順"], built_template.template_id), built_template)

    lines = jsonl_path(settings).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    record = json.loads(lines[0])
    assert record["session_id"] == "s1"
    assert record["question_id"] == "1-(1)"
    assert record["type"] == "和文英訳"
    assert record["score"] == 11
    assert record["rate"] == round(11 / 15, 3)
    assert record["tags"] == ["時制・仮定法"]
    assert record["transcription_edited"] is True


def test_load_stats_aggregates_tags_and_rates(settings, built_template):
    append_grading_log(settings, _result("s1", 12, ["時制・仮定法"], built_template.template_id), built_template)
    append_grading_log(settings, _result("s2", 6, ["時制・仮定法", "語順"], built_template.template_id), built_template)

    stats = load_stats(settings)
    assert stats.question_count == 2
    assert stats.average_rate == round((12 / 15 + 6 / 15) / 2, 3)
    counts = {t.tag: t.count for t in stats.tags}
    assert counts["時制・仮定法"] == 2
    assert counts["語順"] == 1
    assert stats.tags[0].tag == "時制・仮定法"  # 多い順
    assert "和文英訳" in stats.by_type


def test_load_stats_filters_by_since(settings, built_template):
    append_grading_log(settings, _result("s1", 12, ["語順"], built_template.template_id), built_template)
    assert load_stats(settings, since="2999-01-01").question_count == 0
    assert load_stats(settings, since="2000-01-01").question_count == 1


def test_load_stats_rejects_bad_since(settings):
    import pytest

    with pytest.raises(ValueError):
        load_stats(settings, since="2026/01/01")


def test_load_stats_empty_when_no_log(settings):
    stats = load_stats(settings)
    assert stats.question_count == 0
    assert stats.tags == []


def test_load_stats_skips_broken_lines(settings, built_template):
    append_grading_log(settings, _result("s1", 12, ["語順"], built_template.template_id), built_template)
    with jsonl_path(settings).open("a", encoding="utf-8") as f:
        f.write("これは壊れた行\n")
    assert load_stats(settings).question_count == 1


def test_write_review_markdown(settings, built_template):
    result = _result("s1", 11, ["時制・仮定法"], built_template.template_id)
    path = write_review_markdown(settings, result, built_template)
    text = path.read_text(encoding="utf-8")

    assert "**11 / 15 点**" in text
    assert "## 1-(1) 和文英訳 — 11 / 15" in text
    assert "> If I had known the fact" in text
    assert "**時制・仮定法**（-2）" in text
    assert "仮定法過去完了の帰結節が誤り。" in text

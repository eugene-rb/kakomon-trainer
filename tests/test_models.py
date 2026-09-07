"""データモデルの検証（採点形式の分類まわり、ネットワーク不要）。"""

from __future__ import annotations

import pytest

from app.config import PROJECT_ROOT
from app.models import (
    ANSWER_FORMAT_META,
    ISSUE_TAGS,
    Question,
    format_label,
    grading_path,
    resolved_language,
)


def test_default_answer_format_is_essay():
    assert Question(id="Q1", max_score=5).answer_format == "essay"


@pytest.mark.parametrize("legacy", ["written", "", None])
def test_legacy_answer_format_coerced_to_essay(legacy):
    q = Question.model_validate({"id": "Q1", "max_score": 5, "answer_format": legacy})
    assert q.answer_format == "essay"


def test_unknown_answer_format_is_rejected():
    with pytest.raises(ValueError):
        Question.model_validate({"id": "Q1", "max_score": 5, "answer_format": "grafh"})


def test_grading_path_mapping():
    assert grading_path("answer_only") == "deterministic"
    assert grading_path("translation_ja") == "llm_text"
    assert grading_path("graph") == "llm_visual"
    assert grading_path("chem_structure") == "llm_visual"
    assert grading_path("見知らぬ値") == "llm_text"  # 未知は採点を止めない


def test_resolved_language_follows_format():
    assert resolved_language("translation_ja", "en") == "ja"
    assert resolved_language("translation_en", "ja") == "en"
    assert resolved_language("composition_en", "ja") == "en"
    assert resolved_language("essay", "ja") == "ja"  # 固定しない形式は設問値のまま


def test_format_label_falls_back_to_value():
    assert format_label("translation_ja") == "和訳"
    assert format_label("未知") == "未知"


def test_zushi_tag_registered():
    assert "図示の誤り" in ISSUE_TAGS


def test_every_llm_format_has_a_criteria_file():
    """llm_text / llm_visual の全形式に観点ファイルが存在すること。"""
    criteria_dir = PROJECT_ROOT / "app" / "prompts" / "criteria"
    for value, (_, path, _) in ANSWER_FORMAT_META.items():
        if path == "deterministic":
            continue
        assert (criteria_dir / f"{value}.md").exists(), f"criteria/{value}.md がありません"

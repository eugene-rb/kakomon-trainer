"""LLM 抽象化レイヤーのテスト（ネットワーク不要の純関数部分）。"""

from __future__ import annotations

import pytest

from app.config import MissingCredentialError, Settings
from app.llm import get_answer_check_backend, get_grading_backend
from app.llm.tool_schema import (
    ANSWER_CHECK_SCHEMA,
    ANSWER_CHECK_TOOL_NAME,
    GRADE_INPUT_SCHEMA,
    TOOL_NAME,
    anthropic_answer_check_tool,
    anthropic_answer_check_tool_choice,
    anthropic_tool,
    anthropic_tool_choice,
    openai_tool,
    openai_tool_choice,
)
from app.models import ISSUE_KINDS, ISSUE_TAGS


def test_tool_schema_enums_come_from_models():
    """タグ・種別の情報源が models.py に一本化されていること（drift 防止）。"""
    issue = GRADE_INPUT_SCHEMA["properties"]["issues"]["items"]["properties"]
    assert issue["tag"]["enum"] == list(ISSUE_TAGS)
    assert issue["kind"]["enum"] == list(ISSUE_KINDS)
    assert "その他" in ISSUE_TAGS and "その他" in ISSUE_KINDS


def test_tool_schema_required_fields_match_spec():
    assert GRADE_INPUT_SCHEMA["required"] == ["score", "feedback", "issues", "confidence"]
    item = GRADE_INPUT_SCHEMA["properties"]["issues"]["items"]
    assert item["required"] == ["quote", "kind", "tag", "comment", "deduction"]
    assert GRADE_INPUT_SCHEMA["properties"]["confidence"]["enum"] == ["high", "medium", "low"]


def test_anthropic_tool_shape():
    """Anthropic は input_schema キー、tool_choice は {"type":"tool","name":...}。"""
    tool = anthropic_tool()
    assert tool["name"] == TOOL_NAME
    assert tool["input_schema"] is GRADE_INPUT_SCHEMA
    assert anthropic_tool_choice() == {"type": "tool", "name": TOOL_NAME}


def test_openai_tool_shape():
    """OpenAI 互換は parameters キーで function にネストする。"""
    tool = openai_tool()
    assert tool["type"] == "function"
    assert tool["function"]["name"] == TOOL_NAME
    assert tool["function"]["parameters"] is GRADE_INPUT_SCHEMA
    assert openai_tool_choice() == {"type": "function", "function": {"name": TOOL_NAME}}


def test_both_providers_share_the_same_schema():
    assert anthropic_tool()["input_schema"] is openai_tool()["function"]["parameters"]


# --- 答えのみ設問のフォールバック照合ツール ---


def test_wrong_answer_tag_is_registered():
    assert "誤答" in ISSUE_TAGS


def test_answer_check_schema_shape():
    assert ANSWER_CHECK_SCHEMA["required"] == ["correct", "note", "confidence"]
    assert ANSWER_CHECK_SCHEMA["properties"]["correct"]["type"] == "boolean"
    assert ANSWER_CHECK_SCHEMA["properties"]["confidence"]["enum"] == ["high", "medium", "low"]
    assert ANSWER_CHECK_SCHEMA["additionalProperties"] is False
    assert anthropic_answer_check_tool()["input_schema"] is ANSWER_CHECK_SCHEMA
    assert anthropic_answer_check_tool_choice() == {"type": "tool", "name": ANSWER_CHECK_TOOL_NAME}


def test_get_answer_check_backend_none_when_disabled():
    settings = Settings(answer_only_llm_fallback=False, anthropic_api_key="sk-x", _env_file=None)
    assert get_answer_check_backend(settings) is None


def test_get_answer_check_backend_none_without_key():
    settings = Settings(answer_only_llm_fallback=True, anthropic_api_key="", _env_file=None)
    assert get_answer_check_backend(settings) is None


def test_get_answer_check_backend_builds_haiku():
    settings = Settings(
        answer_only_llm_fallback=True,
        anthropic_api_key="sk-x",
        answer_only_model="claude-haiku-4-5",
        _env_file=None,
    )
    backend = get_answer_check_backend(settings)
    assert backend is not None
    assert backend.model == "claude-haiku-4-5"
    assert backend.provider == "anthropic"


# --- 認証情報の解決（SPEC §10.3 の遅延検証）---


def test_missing_api_key_raises_missing_credential():
    settings = Settings(grading_provider="anthropic", anthropic_api_key="", _env_file=None)
    with pytest.raises(MissingCredentialError, match="ANTHROPIC_API_KEY"):
        settings.grading_credentials()


def test_missing_model_raises_missing_credential():
    settings = Settings(
        grading_provider="openai", openai_api_key="sk-test", openai_model="", grading_model="",
        _env_file=None,
    )
    with pytest.raises(MissingCredentialError, match="モデル名"):
        settings.grading_credentials()


def test_provider_specific_model_wins_over_shared():
    """プロバイダ別モデルが共通 GRADING_MODEL より優先されること。

    プロバイダだけ切り替えて古いモデル名が残る事故を防ぐための仕様。
    """
    settings = Settings(
        grading_provider="openai",
        openai_api_key="sk-test",
        openai_model="model-for-openai",
        grading_model="claude-sonnet-5",
        _env_file=None,
    )
    _key, model, _base = settings.grading_credentials()
    assert model == "model-for-openai"


def test_shared_model_used_as_fallback():
    settings = Settings(
        grading_provider="openai", openai_api_key="sk-test", openai_model="",
        grading_model="shared-model", _env_file=None,
    )
    _key, model, _base = settings.grading_credentials()
    assert model == "shared-model"


def test_kimi_defaults_to_moonshot_base_url():
    settings = Settings(
        grading_provider="kimi", kimi_api_key="sk-test", kimi_model="kimi-model", _env_file=None
    )
    _key, _model, base_url = settings.grading_credentials()
    assert base_url == "https://api.moonshot.ai/v1"


def test_openai_compatible_requires_base_url():
    settings = Settings(
        grading_provider="openai_compatible", llm_api_key="k", llm_model="m", llm_base_url="",
        _env_file=None,
    )
    with pytest.raises(MissingCredentialError, match="ベース URL"):
        settings.grading_credentials()


def test_factory_builds_openai_backend_for_kimi():
    settings = Settings(
        grading_provider="kimi", kimi_api_key="sk-test", kimi_model="kimi-model", _env_file=None
    )
    backend = get_grading_backend(settings)
    assert backend.provider == "kimi"
    assert backend.model == "kimi-model"


def test_factory_builds_anthropic_backend():
    settings = Settings(
        grading_provider="anthropic", anthropic_api_key="sk-test",
        anthropic_model="claude-sonnet-5", _env_file=None,
    )
    backend = get_grading_backend(settings)
    assert backend.provider == "anthropic"
    assert backend.model == "claude-sonnet-5"


def test_warn_missing_grading_credentials_returns_message():
    settings = Settings(grading_provider="anthropic", anthropic_api_key="", _env_file=None)
    assert "ANTHROPIC_API_KEY" in (settings.warn_missing_grading_credentials() or "")


def test_openai_content_builder_renders_pdf_as_images(tmp_path):
    """OpenAI 互換では PDF をページ画像に変換して送る（PDF パートが存在しないため）。"""
    import pymupdf

    from app.llm.base import GradingRequest
    from app.llm.openai_compatible_backend import _build_content
    from app.refs import RefFile

    pdf = tmp_path / "ref.pdf"
    doc = pymupdf.open()
    doc.new_page(width=200, height=200)
    doc.save(str(pdf))
    doc.close()

    content = _build_content(
        GradingRequest(
            system_prompt="sys",
            refs=[RefFile(path=pdf, kind="pdf")],
            crop_images=[b"\x89PNG\r\n\x1a\n"],
            question_text="設問",
        )
    )
    types = [part["type"] for part in content]
    assert types.count("image_url") == 2  # 参照 PDF 1 ページ + 切り出し画像
    assert content[-1]["text"] == "設問"
    assert all(
        part["image_url"]["url"].startswith("data:image/png;base64,")
        for part in content
        if part["type"] == "image_url"
    )


def test_anthropic_content_builder_uses_native_document(tmp_path):
    """Anthropic は PDF をネイティブの document ブロックで送る。"""
    import pymupdf

    from app.llm.anthropic_backend import _build_content
    from app.llm.base import GradingRequest
    from app.refs import RefFile

    pdf = tmp_path / "ref.pdf"
    doc = pymupdf.open()
    doc.new_page(width=200, height=200)
    doc.save(str(pdf))
    doc.close()
    md = tmp_path / "criteria.md"
    md.write_text("採点基準", encoding="utf-8")

    content = _build_content(
        GradingRequest(
            system_prompt="sys",
            refs=[RefFile(path=pdf, kind="pdf"), RefFile(path=md, kind="md")],
            crop_images=[b"\x89PNG\r\n\x1a\n"],
            question_text="設問",
        )
    )
    assert content[0]["type"] == "document"
    assert content[0]["source"]["media_type"] == "application/pdf"
    assert "\n" not in content[0]["source"]["data"]  # base64 に改行を含めない
    assert content[1]["type"] == "text" and "採点基準" in content[1]["text"]
    assert content[2]["type"] == "image"
    assert content[3]["text"] == "設問"

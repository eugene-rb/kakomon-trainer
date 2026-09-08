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


# ---------------------------------------------------------------------------
# プロンプトキャッシュと強制ツール呼び出しのフォールバック
# ---------------------------------------------------------------------------

CACHE_CONTROL = {"type": "ephemeral", "ttl": "1h"}


def _md_ref(tmp_path, name="criteria.md", text="採点基準"):
    from app.refs import RefFile

    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return RefFile(path=path, kind="md")


def _request(tmp_path, refs=None):
    from app.llm.base import GradingRequest

    return GradingRequest(
        system_prompt="採点してください",
        refs=refs if refs is not None else [_md_ref(tmp_path)],
        crop_images=[b"\x89PNG\r\n\x1a\n"],
        question_text="設問",
    )


def test_cache_breakpoint_sits_after_the_references(tmp_path):
    """答案より前＝設問が同じなら不変の位置で区切る（生徒ごとの再送を読み出しにする）。"""
    from app.llm.anthropic_backend import _build_content

    content = _build_content(_request(tmp_path, refs=[_md_ref(tmp_path, "a.md"), _md_ref(tmp_path, "b.md")]))

    cached = [i for i, part in enumerate(content) if "cache_control" in part]
    assert cached == [1], "最後の参照資料だけに区切りを置くこと"
    assert content[1]["cache_control"] == CACHE_CONTROL
    assert content[-1]["text"] == "設問"


def test_cache_breakpoint_is_omitted_without_references(tmp_path):
    """参照資料が無ければ答案側には区切らない（可変部分をキャッシュしても無意味）。"""
    from app.llm.anthropic_backend import _build_content

    content = _build_content(_request(tmp_path, refs=[]))
    assert all("cache_control" not in part for part in content)


def test_prompt_cache_can_be_disabled(tmp_path):
    from app.llm.anthropic_backend import _build_content, _build_system

    content = _build_content(_request(tmp_path), prompt_cache=False)
    assert all("cache_control" not in part for part in content)
    assert all("cache_control" not in b for b in _build_system("sys", prompt_cache=False))


def test_system_carries_the_cache_breakpoint():
    from app.llm.anthropic_backend import _build_system

    blocks = _build_system("sys", prompt_cache=True)
    assert blocks == [{"type": "text", "text": "sys", "cache_control": CACHE_CONTROL}]


def test_system_gains_a_tool_instruction_when_forcing_is_dropped():
    """強制を外した分、ツールを呼ぶよう明示する（本文に採点を書かせない）。"""
    from app.llm.anthropic_backend import _build_system

    blocks = _build_system("sys", prompt_cache=True, force_tool_choice=False)
    assert len(blocks) == 2
    assert TOOL_NAME in blocks[1]["text"]
    assert "cache_control" not in blocks[0], "区切りは最後のブロックに 1 つ"
    assert blocks[1]["cache_control"] == CACHE_CONTROL


def test_oversized_reference_pdf_fails_loudly(tmp_path, monkeypatch):
    """模範解答を黙って落として採点を続けない（結果が正常に見えてしまうため）。"""
    from app.llm import anthropic_backend
    from app.llm.base import LLMError
    from app.refs import RefFile

    pdf = tmp_path / "huge.pdf"
    pdf.write_bytes(b"%PDF-1.7" + b"0" * 4096)
    monkeypatch.setattr(anthropic_backend, "_MAX_PDF_BYTES", 1024)

    with pytest.raises(LLMError) as info:
        anthropic_backend._build_content(_request(tmp_path, refs=[RefFile(path=pdf, kind="pdf")]))
    assert "huge.pdf" in str(info.value)
    assert ".md" in str(info.value), "回避策（.md 化）を案内すること"


# --- grade() のフォールバック -------------------------------------------------


class _FakeBadRequest(Exception):
    pass


def _tool_response(score=8):
    from types import SimpleNamespace

    block = SimpleNamespace(
        type="tool_use",
        name=TOOL_NAME,
        input={"score": score, "feedback": "良い", "issues": [], "confidence": "high"},
    )
    return SimpleNamespace(content=[block], stop_reason="tool_use")


def _backend(responses):
    from types import SimpleNamespace

    from app.llm.anthropic_backend import AnthropicGradingBackend

    backend = AnthropicGradingBackend(api_key="sk-test", model="claude-opus-5")
    calls: list[dict] = []

    def create(**kwargs):
        calls.append(kwargs)
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    backend._anthropic = SimpleNamespace(BadRequestError=_FakeBadRequest)
    backend._client = SimpleNamespace(messages=SimpleNamespace(create=create))
    return backend, calls


def test_grade_forces_the_tool_and_never_disables_thinking(tmp_path):
    backend, calls = _backend([_tool_response()])

    assert backend.grade(_request(tmp_path)).score == 8
    assert calls[0]["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert "thinking" not in calls[0], "拡張思考は既定のまま（Opus 5 は思考が既定で有効）"


def test_rejected_forced_tool_use_retries_with_auto_not_disabled_thinking(tmp_path):
    """強制が拒否されたら思考を切るのではなく、強制のほうを取り下げる。"""
    backend, calls = _backend([
        _FakeBadRequest("tool_choice: `any` and `tool` are not supported with thinking"),
        _tool_response(score=5),
    ])

    assert backend.grade(_request(tmp_path)).score == 5
    assert len(calls) == 2
    assert "tool_choice" not in calls[1]
    assert "thinking" not in calls[1], "thinking: disabled は Opus 5 で採点を静かに落とす"
    assert TOOL_NAME in calls[1]["system"][1]["text"]


def test_forced_tool_use_stays_disabled_for_later_questions(tmp_path):
    """一度拒否されたモデルに毎回 400 を食わせない。"""
    backend, calls = _backend([
        _FakeBadRequest("tool_choice is not supported for this model"),
        _tool_response(),
        _tool_response(),
    ])

    backend.grade(_request(tmp_path))
    backend.grade(_request(tmp_path))
    assert len(calls) == 3
    assert all("tool_choice" not in call for call in calls[1:])


def test_unrelated_bad_request_is_not_retried(tmp_path):
    """関係のない 400 まで強制解除で握りつぶさない。"""
    from app.llm.base import LLMError

    backend, calls = _backend([_FakeBadRequest("credit balance is too low")])

    with pytest.raises(LLMError, match="拒否"):
        backend.grade(_request(tmp_path))
    assert len(calls) == 1
    assert backend._force_tool_choice is True


def test_anthropic_model_defaults_to_opus(tmp_path):
    """.env で ANTHROPIC_MODEL を空にしたとき、黙って安いモデルへ落ちない。"""
    assert Settings(_env_file=None).anthropic_model == "claude-opus-5"


def test_rejected_cache_control_falls_back_to_no_caching(tmp_path):
    """キャッシュ指定が通らないモデルでも採点は続ける（キャッシュは任意の最適化）。"""
    backend, calls = _backend([
        _FakeBadRequest("cache_control ttl '1h' is not supported"),
        _tool_response(score=7),
    ])

    assert backend.grade(_request(tmp_path)).score == 7
    assert any("cache_control" in part for part in calls[0]["messages"][0]["content"])
    assert all("cache_control" not in part for part in calls[1]["messages"][0]["content"])
    assert calls[1]["tool_choice"] == {"type": "tool", "name": TOOL_NAME}, "強制は維持する"


def test_both_optional_settings_can_be_dropped_in_one_call(tmp_path):
    """キャッシュと強制ツール呼び出しの両方を拒むモデルでも、1 回の grade() で回復する。"""
    backend, calls = _backend([
        _FakeBadRequest("cache_control is not supported"),
        _FakeBadRequest("tool_choice is not supported for this model"),
        _tool_response(score=6),
    ])

    assert backend.grade(_request(tmp_path)).score == 6
    assert len(calls) == 3
    assert all("cache_control" not in part for part in calls[2]["messages"][0]["content"])
    assert "tool_choice" not in calls[2]

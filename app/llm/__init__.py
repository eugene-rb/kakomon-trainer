"""LLM 採点バックエンドのファクトリ。"""

from __future__ import annotations

from app.config import Settings
from app.llm.answer_check import (
    AnswerCheckBackend,
    AnswerCheckRequest,
    AnswerCheckResult,
)
from app.llm.base import (
    GradeResult,
    GradingRequest,
    LLMError,
    LLMGradingBackend,
    LLMResponseError,
)

__all__ = [
    "AnswerCheckBackend",
    "AnswerCheckRequest",
    "AnswerCheckResult",
    "GradeResult",
    "GradingRequest",
    "LLMError",
    "LLMGradingBackend",
    "LLMResponseError",
    "get_answer_check_backend",
    "get_grading_backend",
]


def get_grading_backend(settings: Settings) -> LLMGradingBackend:
    """設定に応じた採点バックエンドを構築する。

    API キー・モデル名の解決は ``Settings.grading_credentials()`` が行い、
    未設定なら ``MissingCredentialError``（呼び出し側で 503 に変換）を送出する。
    """
    api_key, model, base_url = settings.grading_credentials()
    provider = settings.grading_provider

    if provider == "anthropic":
        from app.llm.anthropic_backend import AnthropicGradingBackend

        return AnthropicGradingBackend(
            api_key=api_key, model=model, max_tokens=settings.grading_max_tokens
        )

    from app.llm.openai_compatible_backend import OpenAICompatibleGradingBackend

    return OpenAICompatibleGradingBackend(
        api_key=api_key,
        model=model,
        base_url=base_url,
        provider=provider,
        max_tokens=settings.grading_max_tokens,
    )


def get_answer_check_backend(settings: Settings) -> AnswerCheckBackend | None:
    """答えのみ設問のフォールバック照合バックエンドを構築する。

    採点プロバイダの設定とは独立に、常に Anthropic Haiku を使う（``ANTHROPIC_API_KEY`` を流用）。
    フォールバックが無効、または ``ANTHROPIC_API_KEY`` 未設定なら ``None`` を返し、
    呼び出し側は曖昧ケースを confidence=low ＋ 警告で人手確認に回す。
    """
    if not settings.answer_only_llm_fallback or not settings.anthropic_api_key:
        return None

    from app.llm.answer_check import AnthropicAnswerCheckBackend

    return AnthropicAnswerCheckBackend(
        api_key=settings.anthropic_api_key,
        model=settings.answer_only_model or "claude-haiku-4-5",
    )

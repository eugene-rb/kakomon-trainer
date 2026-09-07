"""OpenAI 互換 chat completions API による採点。

1 クラスで OpenAI 本家・Moonshot(Kimi)・その他の OpenAI 互換ベンダーをカバーする
（``base_url`` / API キー / モデル名の差し替えだけで切り替わる）。

実測で確認した wire フォーマット（インストール済み SDK の型定義より）:
    - ツール定義: ``{"type": "function", "function": {"name", "description", "parameters"}}``
    - 強制呼び出し: ``tool_choice={"type": "function", "function": {"name": "submit_grade"}}``
    - 画像: ``{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}``
    - PDF を直接渡すコンテンツパートは存在しない → ``pdf_render`` でページ画像化する
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

from app.llm import pdf_render
from app.llm.base import GradeResult, GradingRequest, LLMError, LLMGradingBackend, LLMResponseError
from app.llm.tool_schema import TOOL_NAME, openai_tool, openai_tool_choice

logger = logging.getLogger(__name__)


class OpenAICompatibleGradingBackend(LLMGradingBackend):
    """OpenAI 互換 API による採点バックエンド。"""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        provider: str = "openai",
        max_tokens: int = 8192,
        timeout: float = 300.0,
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as e:  # pragma: no cover
            raise LLMError("openai がインストールされていません。") from e
        self._client = OpenAI(api_key=api_key, base_url=base_url or None, timeout=timeout)
        self.model = model
        self.provider = provider
        self._max_tokens = max_tokens

    def grade(self, request: GradingRequest) -> GradeResult:
        messages = [
            {"role": "system", "content": request.system_prompt},
            {"role": "user", "content": _build_content(request)},
        ]
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=[openai_tool()],
                tool_choice=openai_tool_choice(),
                # SPEC §9.7 の温度固定。OpenAI 互換 API では現在も有効。
                temperature=0,
                max_tokens=self._max_tokens,
            )
        except Exception as e:
            raise LLMError(f"{self.provider} の呼び出しに失敗しました: {e}") from e

        if not response.choices:
            raise LLMResponseError(f"{self.provider} が空のレスポンスを返しました。")
        message = response.choices[0].message
        tool_calls = getattr(message, "tool_calls", None) or []
        for call in tool_calls:
            function = getattr(call, "function", None)
            if function is None or function.name != TOOL_NAME:
                continue
            try:
                # 文字列エスケープの差異があるため必ず JSON としてパースする（生文字列比較は禁止）
                data = json.loads(function.arguments)
            except json.JSONDecodeError as e:
                raise LLMResponseError(
                    f"{self.provider} のツール引数を JSON として解釈できませんでした: {e}"
                ) from e
            return GradeResult(
                score=int(data.get("score", 0)),
                feedback=str(data.get("feedback", "")),
                issues=list(data.get("issues", []) or []),
                confidence=str(data.get("confidence", "low")),
                raw=data,
            )
        raise LLMResponseError(
            f"{self.provider} が採点ツール呼び出しを返しませんでした"
            f"（finish_reason={response.choices[0].finish_reason}）"
        )


def _data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.standard_b64encode(png).decode("ascii")


def _build_content(request: GradingRequest) -> list[dict[str, Any]]:
    """SPEC §9.5 の構成を OpenAI 互換のコンテンツパートに変換する。"""
    content: list[dict[str, Any]] = []

    for ref in request.refs:
        if ref.kind == "pdf":
            content.append({"type": "text", "text": f"<参照資料（画像として添付）: {ref.name}>"})
            for png in pdf_render.pdf_to_page_pngs(ref.path):
                content.append({"type": "image_url", "image_url": {"url": _data_url(png)}})
        else:
            content.append({"type": "text", "text": f"<参照資料: {ref.name}>\n{ref.read_text()}"})

    for png in request.crop_images:
        content.append({"type": "image_url", "image_url": {"url": _data_url(png)}})

    content.append({"type": "text", "text": request.question_text})
    return content

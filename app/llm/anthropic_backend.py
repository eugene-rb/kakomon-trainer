"""Anthropic Messages API による採点（SPEC §9.5）。

参照資料の PDF は **ネイティブの document ブロック** で添付できるのが Anthropic の利点で、
仕様書の記載どおりに実装している。

注意（実測で確認した現行 API の仕様）:
    仕様書 §9.5 は ``temperature: 0`` を要求しているが、現行の Claude モデル
    （Sonnet 5 / Opus 5 など）では ``temperature`` は **API から削除**されており、
    送ると 400 になる（インストール済み SDK の ``messages.create`` シグネチャにも存在しない）。
    そのため Anthropic では temperature を送らない。採点の再現性は
    「設問ごとに 1 リクエスト」「同一プロンプト」「DOUBLE_GRADING による点差検出」で担保する。
    OpenAI 互換バックエンド側では temperature=0 を送っている。
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from app.llm.base import GradeResult, GradingRequest, LLMError, LLMGradingBackend, LLMResponseError
from app.llm.tool_schema import TOOL_NAME, anthropic_tool, anthropic_tool_choice

logger = logging.getLogger(__name__)

#: 1 リクエストに添付する PDF の上限（API 制限 32MB に対する安全側の目安）
_MAX_PDF_BYTES = 25 * 1024 * 1024


class AnthropicGradingBackend(LLMGradingBackend):
    """Anthropic Claude による採点バックエンド。"""

    provider = "anthropic"

    def __init__(self, api_key: str, model: str, max_tokens: int = 8192, timeout: float = 300.0) -> None:
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover
            raise LLMError("anthropic がインストールされていません。") from e
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
        self.model = model
        self._max_tokens = max_tokens
        #: 強制ツール呼び出しと thinking の併用が拒否された場合に無効化するためのフラグ
        self._disable_thinking = False

    def grade(self, request: GradingRequest) -> GradeResult:
        content = _build_content(request)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self._max_tokens,
            "system": request.system_prompt,
            "messages": [{"role": "user", "content": content}],
            "tools": [anthropic_tool()],
            "tool_choice": anthropic_tool_choice(),
        }
        if self._disable_thinking:
            kwargs["thinking"] = {"type": "disabled"}

        try:
            response = self._client.messages.create(**kwargs)
        except self._anthropic.BadRequestError as e:
            # 強制ツール呼び出しと拡張思考の併用が拒否されるモデルがあるため、
            # 一度だけ thinking を明示的に無効化して再試行する。
            if not self._disable_thinking and "thinking" in str(e).lower():
                logger.warning("thinking を無効化して再試行します: %s", e)
                self._disable_thinking = True
                kwargs["thinking"] = {"type": "disabled"}
                try:
                    response = self._client.messages.create(**kwargs)
                except Exception as e2:
                    raise LLMError(f"Anthropic の呼び出しに失敗しました: {e2}") from e2
            else:
                raise LLMError(f"Anthropic がリクエストを拒否しました: {e}") from e
        except Exception as e:
            raise LLMError(f"Anthropic の呼び出しに失敗しました: {e}") from e

        if getattr(response, "stop_reason", None) == "refusal":
            raise LLMResponseError("Anthropic が安全性の理由で応答を拒否しました。")

        for block in response.content:
            if getattr(block, "type", "") == "tool_use" and block.name == TOOL_NAME:
                data = dict(block.input)
                return GradeResult(
                    score=int(data.get("score", 0)),
                    feedback=str(data.get("feedback", "")),
                    issues=list(data.get("issues", []) or []),
                    confidence=str(data.get("confidence", "low")),
                    raw=data,
                )
        raise LLMResponseError(
            f"採点ツール呼び出しが返りませんでした（stop_reason={getattr(response, 'stop_reason', None)}）"
        )


def _build_content(request: GradingRequest) -> list[dict[str, Any]]:
    """SPEC §9.5 のリクエスト構成でコンテンツブロックを組み立てる。

    1. 参照資料（PDF は document ブロック、md/txt は text ブロック）
    2. 切り出し画像
    3. 設問情報と転記テキスト
    """
    content: list[dict[str, Any]] = []

    for ref in request.refs:
        if ref.kind == "pdf":
            data = ref.read_bytes()
            if len(data) > _MAX_PDF_BYTES:
                logger.warning("参照 PDF が大きすぎるため添付を省略します: %s", ref.path)
                continue
            content.append(
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        # base64 に改行を含めないこと（API 制約）
                        "data": base64.standard_b64encode(data).decode("ascii"),
                    },
                }
            )
        else:
            content.append(
                {"type": "text", "text": f"<参照資料: {ref.name}>\n{ref.read_text()}"}
            )

    for png in request.crop_images:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.standard_b64encode(png).decode("ascii"),
                },
            }
        )

    content.append({"type": "text", "text": request.question_text})
    return content

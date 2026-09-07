"""答えのみ設問のフォールバック照合（Claude Haiku）。

``app.answer_match.judge`` が ``verdict="uncertain"`` を返した文字列ケースだけをここに投げる。
減点積み上げもタグ付けも不要なので ``submit_grade`` ではなく軽量な ``submit_answer_check``
（correct / note / confidence の 3 項目）を強制ツール指定で使う。

参照資料 PDF はネイティブ document ブロックで、切り出し画像は image ブロックで添付する
（OCR 由来の誤認識を画像で読み替えられるようにするため）。
"""

from __future__ import annotations

import base64
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.llm.base import LLMError, LLMResponseError
from app.llm.tool_schema import (
    ANSWER_CHECK_TOOL_NAME,
    anthropic_answer_check_tool,
    anthropic_answer_check_tool_choice,
)
from app.models import Confidence
from app.refs import RefFile

logger = logging.getLogger(__name__)

#: 1 リクエストに添付する PDF の上限（API 制限 32MB に対する安全側の目安）
_MAX_PDF_BYTES = 25 * 1024 * 1024


@dataclass
class AnswerCheckRequest:
    """1 設問分のフォールバック照合リクエスト。"""

    system_prompt: str
    #: 模範解答（複数候補は改行結合済み）
    expected: str
    #: 答案（確定済みの転記テキスト）
    given: str
    #: 設問情報（種別・満点・採点上の注意など）
    question_text: str = ""
    refs: list[RefFile] = field(default_factory=list)
    crop_images: list[bytes] = field(default_factory=list)


@dataclass
class AnswerCheckResult:
    """``submit_answer_check`` の返り値。"""

    correct: bool
    note: str
    confidence: Confidence


class AnswerCheckBackend(ABC):
    """フォールバック照合バックエンドの共通インターフェース。"""

    provider: str = "unknown"
    model: str = ""

    @abstractmethod
    def check(self, request: AnswerCheckRequest) -> AnswerCheckResult:
        ...


class AnthropicAnswerCheckBackend(AnswerCheckBackend):
    """Claude Haiku による正誤照合。"""

    provider = "anthropic"

    def __init__(self, api_key: str, model: str, timeout: float = 120.0) -> None:
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover
            raise LLMError("anthropic がインストールされていません。") from e
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
        self.model = model

    def check(self, request: AnswerCheckRequest) -> AnswerCheckResult:
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=request.system_prompt,
                messages=[{"role": "user", "content": _build_content(request)}],
                tools=[anthropic_answer_check_tool()],
                tool_choice=anthropic_answer_check_tool_choice(),
            )
        except self._anthropic.BadRequestError as e:
            raise LLMError(f"Anthropic がリクエストを拒否しました: {e}") from e
        except Exception as e:
            raise LLMError(f"Anthropic の呼び出しに失敗しました: {e}") from e

        if getattr(response, "stop_reason", None) == "refusal":
            raise LLMResponseError("Anthropic が安全性の理由で応答を拒否しました。")

        for block in response.content:
            if getattr(block, "type", "") == "tool_use" and block.name == ANSWER_CHECK_TOOL_NAME:
                data = dict(block.input)
                confidence = str(data.get("confidence", "low"))
                return AnswerCheckResult(
                    correct=bool(data.get("correct", False)),
                    note=str(data.get("note", "")),
                    confidence=confidence if confidence in ("high", "medium", "low") else "low",  # type: ignore[arg-type]
                )
        raise LLMResponseError(
            f"照合ツール呼び出しが返りませんでした（stop_reason={getattr(response, 'stop_reason', None)}）"
        )


def _build_content(request: AnswerCheckRequest) -> list[dict[str, Any]]:
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
                        "data": base64.standard_b64encode(data).decode("ascii"),
                    },
                }
            )
        else:
            content.append({"type": "text", "text": f"<参照資料: {ref.name}>\n{ref.read_text()}"})

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

    lines = ["# 照合対象"]
    if request.question_text:
        lines.append(request.question_text)
    lines += [
        "",
        "# 模範解答",
        request.expected,
        "",
        "# 答案（OCR による転記。添付画像が原本）",
        request.given if request.given.strip() else "（空欄）",
    ]
    content.append({"type": "text", "text": "\n".join(lines)})
    return content

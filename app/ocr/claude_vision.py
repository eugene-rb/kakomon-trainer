"""Claude の Vision による OCR フォールバック（SPEC §8.2）。

``words`` は返せないため空リストになる。その結果、赤入れは「設問単位の赤枠＋欄外コメント」に
フォールバックする（SPEC §8.2 の注記。UI 側でもその旨を明示する）。
"""

from __future__ import annotations

import base64
import logging

from app.ocr.base import OCRBackend, OCRError, OCRResult

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "あなたは手書き答案の文字起こしを行います。"
    "画像に書かれている文字をそのまま書き起こしてください。"
    "解釈・要約・添削・修正は一切しないこと。"
    "書かれていない文字を補ってはいけません。判読できない箇所は □ で表してください。"
    "書き起こしたテキストだけを出力し、前置きや説明は付けないこと。"
)

_USER_PROMPT = {
    "ja": "この画像の日本語の手書き答案を、書かれているとおりに書き起こしてください。",
    "en": "この画像の英語の手書き答案を、書かれているとおりに書き起こしてください。",
}


class ClaudeVisionBackend(OCRBackend):
    """Claude Vision による転記。単語 bbox は返さない。"""

    name = "claude_vision"
    supports_words = False

    def __init__(self, api_key: str, model: str, max_tokens: int = 2048) -> None:
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover
            raise OCRError("anthropic がインストールされていません。") from e
        if not api_key:
            raise OCRError(
                "OCR_BACKEND=claude_vision には ANTHROPIC_API_KEY が必要です。"
            )
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    def recognize(self, image_png: bytes, language: str) -> OCRResult:
        b64 = base64.standard_b64encode(image_png).decode("ascii")
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=_SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": b64,
                                },
                            },
                            {"type": "text", "text": _USER_PROMPT.get(language, _USER_PROMPT["ja"])},
                        ],
                    }
                ],
            )
        except Exception as e:
            raise OCRError(f"Claude Vision の呼び出しに失敗しました: {e}") from e

        text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        ).strip()
        return OCRResult(text=text, words=[])

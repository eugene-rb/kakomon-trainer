"""Azure Document Intelligence による OCR（SPEC §8.2）。

``prebuilt-read`` モデルを使う。``azure-ai-documentintelligence`` は任意依存のため
（requirements-optional.txt）、未インストール時はこのバックエンドを選んだ瞬間に
分かりやすい日本語エラーを出す。
"""

from __future__ import annotations

import logging

from app.ocr.base import OCRBackend, OCRError, OCRResult, Word

logger = logging.getLogger(__name__)


class AzureDocumentIntelligenceBackend(OCRBackend):
    """Azure Document Intelligence バックエンド。"""

    name = "azure_di"
    supports_words = True

    def __init__(self, endpoint: str, key: str, timeout: float = 120.0) -> None:
        try:
            from azure.ai.documentintelligence import DocumentIntelligenceClient
            from azure.core.credentials import AzureKeyCredential
        except ImportError as e:
            raise OCRError(
                "azure-ai-documentintelligence がインストールされていません。"
                "OCR_BACKEND=azure_di を使うには "
                "`pip install -r requirements-optional.txt` を実行してください。"
            ) from e
        if not endpoint or not key:
            raise OCRError("AZURE_DI_ENDPOINT と AZURE_DI_KEY を .env に設定してください。")
        self._client = DocumentIntelligenceClient(
            endpoint=endpoint, credential=AzureKeyCredential(key)
        )
        self._timeout = timeout

    def recognize(self, image_png: bytes, language: str) -> OCRResult:
        try:
            poller = self._client.begin_analyze_document(
                "prebuilt-read", body=image_png, content_type="application/octet-stream"
            )
            result = poller.result(timeout=self._timeout)
        except Exception as e:
            raise OCRError(f"Azure Document Intelligence の呼び出しに失敗しました: {e}") from e

        text = (getattr(result, "content", "") or "").strip()
        return OCRResult(text=text, words=_extract_words(result))


def _extract_words(result) -> list[Word]:
    """解析結果から単語と bbox を取り出す。

    Azure は ``polygon`` を ``[x1, y1, x2, y2, ...]`` の平坦なリストで返すため、
    偶数番目を x、奇数番目を y として外接矩形に丸める。
    """
    words: list[Word] = []
    for page in getattr(result, "pages", []) or []:
        for word in getattr(page, "words", []) or []:
            polygon = getattr(word, "polygon", None) or []
            if len(polygon) < 4:
                continue
            xs = polygon[0::2]
            ys = polygon[1::2]
            words.append(
                Word(
                    text=word.content,
                    bbox=(
                        int(min(xs)),
                        int(min(ys)),
                        int(max(xs)),
                        int(max(ys)),
                    ),
                )
            )
    return words

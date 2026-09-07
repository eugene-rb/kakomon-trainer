"""Google Cloud Vision による OCR（SPEC §8.2）。

``DOCUMENT_TEXT_DETECTION`` を使う。手書きに対応しており、単語バウンディングボックスを
返せるため、これが既定のバックエンドになっている。
"""

from __future__ import annotations

import logging

from app.ocr.base import OCRBackend, OCRError, OCRResult, Word

logger = logging.getLogger(__name__)

#: SPEC §6.1 の language ('ja'|'en') を Vision の language hint に写す
_LANGUAGE_HINTS = {"ja": ["ja"], "en": ["en"]}


class GoogleVisionBackend(OCRBackend):
    """Google Cloud Vision バックエンド。"""

    name = "google_vision"
    supports_words = True

    def __init__(self, timeout: float = 60.0) -> None:
        try:
            from google.cloud import vision  # 遅延 import（未インストール環境でも起動できるように）
        except ImportError as e:  # pragma: no cover
            raise OCRError(
                "google-cloud-vision がインストールされていません。"
                "`pip install -r requirements.txt` を実行してください。"
            ) from e
        self._vision = vision
        self._timeout = timeout
        # 認証情報は config.apply_google_credentials() で環境変数に設定済みである前提。
        self._client = vision.ImageAnnotatorClient()

    def recognize(self, image_png: bytes, language: str) -> OCRResult:
        vision = self._vision
        image = vision.Image(content=image_png)
        context = vision.ImageContext(language_hints=_LANGUAGE_HINTS.get(language, []))
        try:
            response = self._client.document_text_detection(
                image=image, image_context=context, timeout=self._timeout
            )
        except Exception as e:  # SDK は多様な例外を投げるため広めに捕捉して統一する
            raise OCRError(f"Google Vision の呼び出しに失敗しました: {e}") from e

        if response.error.message:
            raise OCRError(f"Google Vision がエラーを返しました: {response.error.message}")

        annotation = response.full_text_annotation
        text = (annotation.text or "").strip()
        return OCRResult(text=text, words=_extract_words(annotation))


def _extract_words(annotation) -> list[Word]:
    """full_text_annotation から単語と bbox を取り出す。

    座標は切り出し画像内のピクセル座標。``bounding_box.vertices`` は 4 点なので、
    外接矩形に丸める。
    """
    words: list[Word] = []
    for page in annotation.pages:
        for block in page.blocks:
            for paragraph in block.paragraphs:
                for word in paragraph.words:
                    text = "".join(symbol.text for symbol in word.symbols)
                    if not text:
                        continue
                    xs = [v.x for v in word.bounding_box.vertices]
                    ys = [v.y for v in word.bounding_box.vertices]
                    if not xs or not ys:
                        continue
                    words.append(
                        Word(text=text, bbox=(min(xs), min(ys), max(xs), max(ys)))
                    )
    return words

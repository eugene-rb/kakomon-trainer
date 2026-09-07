"""OCR バックエンドの抽象基底（SPEC §8.1）。

``recognize()`` は切り出し画像 1 枚を認識する。``words``（単語バウンディングボックス）が
取れるかどうかが赤入れの精度に直結する（SPEC §8.2, §9.8）。取れないバックエンドは
空リストを返し、赤入れは設問単位のフォールバックになる。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.models import OCRWordRecord


@dataclass
class Word:
    """認識された単語 1 個。

    ``bbox`` は **切り出し画像内のピクセル座標** ``(x0, y0, x1, y1)``。
    キャンバス座標ではないので、赤入れ時は ``geometry.crop_rect_to_canvas`` で
    CropInfo に記録された実測原点を使って変換すること。
    """

    text: str
    bbox: tuple[int, int, int, int]


@dataclass
class OCRResult:
    """切り出し画像 1 枚の認識結果。"""

    text: str
    words: list[Word] = field(default_factory=list)


class OCRError(RuntimeError):
    """OCR バックエンドの失敗。設問単位のリトライ対象（SPEC §10.3）。"""


class OCRBackend(ABC):
    """OCR バックエンドの共通インターフェース。"""

    #: UI 表示用の名前
    name: str = "unknown"
    #: 単語バウンディングボックスを返せるか（赤入れ精度に影響する。SPEC §8.2）
    supports_words: bool = False

    @abstractmethod
    def recognize(self, image_png: bytes, language: str) -> OCRResult:
        """切り出し画像 1 枚を認識する。language は 'ja' | 'en'。"""
        ...


def words_to_records(words: list[Word]) -> list[OCRWordRecord]:
    """``Word`` を永続化用モデルに変換する。"""
    return [OCRWordRecord(text=w.text, bbox=w.bbox) for w in words]


def records_to_words(records: list[OCRWordRecord]) -> list[Word]:
    """永続化用モデルを ``Word`` に戻す。"""
    return [Word(text=r.text, bbox=tuple(r.bbox)) for r in records]  # type: ignore[arg-type]

"""文字列突き合わせ用の正規化（共有ユーティリティ）。

赤入れ時の「引用 → OCR 単語」の突き合わせ（``app.annotator``）と、
答えのみ設問の「答案 → 模範解答」の突き合わせ（``app.answer_match``）で
同じ正規化を使う。情報源を 1 か所にして drift を防ぐため独立モジュールにしている。
"""

from __future__ import annotations

import unicodedata

#: 両端から落とす句読点・記号。日本語には単語間の空白がないため空白は「圧縮」ではなく「除去」する。
_EDGE_PUNCT = ".,;:!?\"'`()[]{}。、！？「」『』…-—–"


def normalize_for_match(text: str) -> str:
    """突き合わせ用の正規化。

    小文字化・全角/半角統一（NFKC）・空白の除去・両端の句読点除去を行う。
    """
    normalized = unicodedata.normalize("NFKC", text).lower()
    normalized = "".join(ch for ch in normalized if not ch.isspace())
    return normalized.strip(_EDGE_PUNCT)

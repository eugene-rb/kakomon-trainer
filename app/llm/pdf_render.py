"""参照資料の PDF をページ画像に変換する（OpenAI 互換バックエンド用）。

Anthropic は PDF をネイティブの document ブロックで受け取れるが、OpenAI 互換の
chat completions API には PDF を渡すコンテンツパートが存在しない
（インストール済み SDK の ContentPart 型は text / image_url / input_audio / refusal のみ）。
Kimi 等の互換ベンダーがどこまで独自拡張を受けるかも保証がないため、
**全 OpenAI 互換プロバイダで PDF はページ画像に変換して送る**。これが安全側の共通解になる。
"""

from __future__ import annotations

import logging
from pathlib import Path

from app import pdfutil

logger = logging.getLogger(__name__)

#: 参照資料のレンダリング解像度。文字が読めれば十分なので低めに抑える。
REF_RENDER_DPI = 150
#: 1 つの PDF から送るページ数の上限（トークン浪費と API 制限への保険）
MAX_REF_PAGES = 20


def pdf_to_page_pngs(path: Path, dpi: int = REF_RENDER_DPI, max_pages: int = MAX_REF_PAGES) -> list[bytes]:
    """PDF を PNG バイト列のリストに変換する。"""
    pngs: list[bytes] = []
    with pdfutil.open_pdf(path) as doc:
        total = doc.page_count
        for index, page in enumerate(doc):
            if index >= max_pages:
                logger.warning(
                    "参照 PDF のページ数が上限を超えたため %d/%d ページのみ添付します: %s",
                    max_pages,
                    total,
                    path.name,
                )
                break
            pngs.append(page.get_pixmap(dpi=dpi).tobytes("png"))
    return pngs

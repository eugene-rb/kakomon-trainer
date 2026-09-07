"""フェーズ1: ブランク PDF 生成とマーカー中心の実測（SPEC §7.2）。

``source.pdf`` の各ページを一回り縮小配置した新規ページに ArUco 3 個（0/1/3）と
右下 QR 1 個を焼き込んで ``blank.pdf`` を作り、それを 300dpi でレンダリングして
**実際に検出できたマーカー中心と QR の 4 隅** を ``template.json`` に記録する。
計算値ではなく実測値を使うのは、PDF の座標変換誤差を吸収するため（SPEC §7.2 の注記）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pymupdf

from app import markers, pdfutil
from app.geometry import mm_to_pt
from app.models import MarkerRecord, PageInfo

logger = logging.getLogger(__name__)

#: マーカー画像を PDF に貼るときの解像度（十分な精細さがあればよい）
_MARKER_RENDER_PX = 300

#: 重なり警告の判定閾値（マーカー footprint 内の非白ピクセル比率）
_OVERLAP_RATIO_THRESHOLD = 0.02
_OVERLAP_CHECK_DPI = 150
#: 「白」とみなす明度の下限
_WHITE_LEVEL = 245


@dataclass
class BuildResult:
    """ブランク PDF 生成の結果。"""

    blank_pdf: Path
    pages: list[PageInfo] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


#: 本文（source.pdf の内容）をマーカー・QR と被らせないための縮小率（中央寄せ）。
SOURCE_INSET_RATIO = 0.95


def inset_rect_pt(page_w_pt: float, page_h_pt: float) -> pymupdf.Rect:
    """本文を ``SOURCE_INSET_RATIO`` に中央寄せで縮小配置する矩形。"""
    iw = page_w_pt * SOURCE_INSET_RATIO
    ih = page_h_pt * SOURCE_INSET_RATIO
    x0 = (page_w_pt - iw) / 2.0
    y0 = (page_h_pt - ih) / 2.0
    return pymupdf.Rect(x0, y0, x0 + iw, y0 + ih)


def marker_rects_pt(page_w_pt: float, page_h_pt: float) -> dict[int, pymupdf.Rect]:
    """ArUco マーカー（黒枠部分）の配置矩形を PDF ポイントで返す。

    SPEC §5.2: 1 辺 12mm、ページ端から 8mm の余白位置。
    ID は 0=左上, 1=右上, 3=左下（右下 2 は欠番＝QR を置く）。
    """
    size = mm_to_pt(markers.MARKER_SIZE_MM)
    margin = mm_to_pt(markers.MARKER_MARGIN_MM)
    right = page_w_pt - margin - size
    bottom = page_h_pt - margin - size
    origins = {
        markers.MARKER_TOP_LEFT: (margin, margin),
        markers.MARKER_TOP_RIGHT: (right, margin),
        markers.MARKER_BOTTOM_LEFT: (margin, bottom),
    }
    return {
        mid: pymupdf.Rect(x, y, x + size, y + size) for mid, (x, y) in origins.items()
    }


def qr_rect_pt(page_w_pt: float, page_h_pt: float) -> pymupdf.Rect:
    """QR の配置矩形を PDF ポイントで返す（右下隅、端から 8mm、1 辺 15mm）。"""
    size = mm_to_pt(markers.QR_SIZE_MM)
    margin = mm_to_pt(markers.QR_MARGIN_MM)
    left = page_w_pt - margin - size
    top = page_h_pt - margin - size
    return pymupdf.Rect(left, top, left + size, top + size)


def _expand(rect: pymupdf.Rect, ratio: float) -> pymupdf.Rect:
    """矩形を各辺に ``辺長 * ratio`` だけ広げる（静穏帯付き画像の配置用）。"""
    dx = rect.width * ratio
    dy = rect.height * ratio
    return pymupdf.Rect(rect.x0 - dx, rect.y0 - dy, rect.x1 + dx, rect.y1 + dy)


def _content_overlap_ratio(page_image: np.ndarray, rect_pt: pymupdf.Rect,
                           page_w_pt: float, page_h_pt: float) -> float:
    """ページ画像のうち、指定 PDF 矩形にあたる領域の非白ピクセル比率を返す。"""
    h, w = page_image.shape[:2]
    x0 = max(0, int(rect_pt.x0 / page_w_pt * w))
    y0 = max(0, int(rect_pt.y0 / page_h_pt * h))
    x1 = min(w, int(rect_pt.x1 / page_w_pt * w))
    y1 = min(h, int(rect_pt.y1 / page_h_pt * h))
    if x1 <= x0 or y1 <= y0:
        return 0.0
    patch = page_image[y0:y1, x0:x1]
    gray = patch if patch.ndim == 2 else patch.mean(axis=2)
    return float((gray < _WHITE_LEVEL).mean())


def build_blank_pdf(
    source_pdf: Path, blank_pdf: Path, template_id: str, dpi: int = 300
) -> BuildResult:
    """source.pdf にマーカーと QR を焼き込んで blank.pdf を生成し、中心を実測する。

    Args:
        source_pdf: 元の白紙解答用紙。
        blank_pdf: 出力先。
        template_id: QR に埋め込むテンプレート ID。
        dpi: キャンバスの解像度（SPEC §5.1: 300dpi）。

    Returns:
        生成結果。``pages`` には実測したマーカー中心を含む PageInfo が入る。
    """
    result = BuildResult(blank_pdf=blank_pdf)
    marker_images = {mid: markers.aruco_marker_png(mid, _MARKER_RENDER_PX) for mid in markers.MARKER_IDS}

    with pdfutil.open_pdf(source_pdf) as src, pymupdf.open() as out:
        for page_index in range(src.page_count):
            page_no = page_index + 1
            src_rect = src[page_index].rect
            page_w_pt, page_h_pt = float(src_rect.width), float(src_rect.height)

            # 本文を一回り縮小して中央に配置（マーカー・QR と被らないよう余白を作る）
            page = out.new_page(width=page_w_pt, height=page_h_pt)
            page.show_pdf_page(inset_rect_pt(page_w_pt, page_h_pt), src, page_index)

            m_rects = marker_rects_pt(page_w_pt, page_h_pt)
            q_rect = qr_rect_pt(page_w_pt, page_h_pt)

            # 焼き込み前（縮小配置後）の内容と重なっていないか確認する（自動移動はしない）。
            # 判定は「実際に貼られる領域」= 静穏帯込みの拡張矩形で行う。黒枠だけで判定すると
            # 静穏帯（白）に隠れる内容を見逃す。
            before = pdfutil.render_page_to_array(page, _OVERLAP_CHECK_DPI)
            _, marker_scale = next(iter(marker_images.values()))
            marker_pad_ratio = (marker_scale - 1.0) / 2.0
            for mid, rect in m_rects.items():
                painted = _expand(rect, marker_pad_ratio)
                ratio = _content_overlap_ratio(before, painted, page_w_pt, page_h_pt)
                if ratio > _OVERLAP_RATIO_THRESHOLD:
                    result.warnings.append(
                        f"ページ {page_no}: マーカー {mid} の位置に既存の内容があります"
                        f"（非白ピクセル {ratio:.1%}）。解答用紙側の余白を空けてください。"
                    )
            qr_ratio = _content_overlap_ratio(before, q_rect, page_w_pt, page_h_pt)
            if qr_ratio > _OVERLAP_RATIO_THRESHOLD:
                result.warnings.append(
                    f"ページ {page_no}: QR の位置（右下）に既存の内容があります"
                    f"（非白ピクセル {qr_ratio:.1%}）。"
                )

            # 焼き込み。静穏帯込みの画像を貼るので、黒枠がちょうど 12mm になるよう広げる。
            for mid, rect in m_rects.items():
                png, _scale = marker_images[mid]
                page.insert_image(_expand(rect, marker_pad_ratio), stream=png)

            qr_png = markers.generate_qr_png(
                markers.build_qr_payload(template_id, page_no), _MARKER_RENDER_PX
            )
            page.insert_image(q_rect, stream=qr_png)

        blank_pdf.parent.mkdir(parents=True, exist_ok=True)
        out.save(str(blank_pdf))

    result.pages = measure_pages(blank_pdf, dpi)
    for page in result.pages:
        missing = sorted(set(markers.MARKER_IDS) - {m.id for m in page.markers})
        if missing:
            result.warnings.append(
                f"ページ {page.page_no}: 生成した blank.pdf からマーカー {missing} を"
                f"再検出できませんでした。解答用紙の四隅に十分な余白があるか確認してください。"
            )
        if len(page.qr_quad) != 4:
            result.warnings.append(
                f"ページ {page.page_no}: 生成した blank.pdf から QR の 4 隅を検出できませんでした。"
                f"右下の余白を確認してください。"
            )
    return result


def measure_pages(blank_pdf: Path, dpi: int = 300) -> list[PageInfo]:
    """blank.pdf を dpi でレンダリングし、マーカー中心を実測して PageInfo を作る。

    計算値ではなく実測値を使うのが要点（SPEC §7.2）。
    """
    pages: list[PageInfo] = []
    with pdfutil.open_pdf(blank_pdf) as doc:
        for page_index, page in enumerate(doc):
            image = pdfutil.render_page_to_array(page, dpi)
            canvas_h, canvas_w = image.shape[:2]
            detected = markers.detect_aruco_markers(image)
            located = markers.detect_qr_located(image)
            qr_quad: list[tuple[float, float]] = []
            if located is not None and located[1] is not None and len(located[1]) == 4:
                qr_quad = [(float(x), float(y)) for x, y in located[1]]
            pages.append(
                PageInfo(
                    page_no=page_index + 1,
                    canvas_w=canvas_w,
                    canvas_h=canvas_h,
                    markers=[
                        MarkerRecord(id=mid, cx=c[0], cy=c[1])
                        for mid, c in sorted(detected.items())
                    ],
                    qr_quad=qr_quad,
                )
            )
    return pages

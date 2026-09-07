"""ブランク PDF 生成とマーカー実測のテスト。"""

from __future__ import annotations

import pymupdf
import pytest

from app import markers, pdfutil, template_builder
from app.geometry import mm_to_pt


def test_marker_rects_follow_spec(tmp_path):
    """1 辺 12mm、ページ端から 8mm（SPEC §5.2）。右下(2)は欠番＝QR。"""
    rects = template_builder.marker_rects_pt(595.0, 842.0)
    assert set(rects) == {0, 1, 3}
    size = mm_to_pt(12.0)
    margin = mm_to_pt(8.0)

    assert rects[0].x0 == pytest.approx(margin)
    assert rects[0].y0 == pytest.approx(margin)
    assert rects[0].width == pytest.approx(size)
    assert rects[1].x1 == pytest.approx(595.0 - margin)   # 右上
    assert rects[3].x0 == pytest.approx(margin)           # 左下
    assert rects[3].y1 == pytest.approx(842.0 - margin)


def test_qr_rect_is_bottom_right():
    rect = template_builder.qr_rect_pt(595.0, 842.0)
    margin = mm_to_pt(8.0)
    assert rect.width == pytest.approx(mm_to_pt(15.0))
    assert rect.x1 == pytest.approx(595.0 - margin)
    assert rect.y1 == pytest.approx(842.0 - margin)


def test_inset_rect_is_centered_and_smaller():
    rect = template_builder.inset_rect_pt(595.0, 842.0)
    assert rect.width == pytest.approx(595.0 * template_builder.SOURCE_INSET_RATIO)
    assert (rect.x0 + rect.x1) / 2 == pytest.approx(595.0 / 2)
    assert (rect.y0 + rect.y1) / 2 == pytest.approx(842.0 / 2)


def test_build_blank_pdf_measures_markers(settings, source_pdf, tmp_path):
    """生成した blank.pdf からマーカーを再検出し、実測値が記録されること（SPEC §7.2）。"""
    blank = tmp_path / "blank.pdf"
    result = template_builder.build_blank_pdf(source_pdf, blank, "tpl-1", dpi=300)

    assert blank.exists()
    assert result.warnings == []
    assert len(result.pages) == 1

    page = result.pages[0]
    assert (page.canvas_w, page.canvas_h) == (2480, 3509)  # A4 @300dpi
    assert {m.id for m in page.markers} == {0, 1, 3}
    assert len(page.qr_quad) == 4  # 右下 QR の 4 隅を実測

    # 実測値が理論値（8mm + 12mm/2 = 14mm ≒ 165px）に一致すること
    expected = 14.0 / 25.4 * 300
    top_left = next(m for m in page.markers if m.id == 0)
    assert top_left.cx == pytest.approx(expected, abs=2.0)
    assert top_left.cy == pytest.approx(expected, abs=2.0)

    # QR は右下隅（4 隅とも右下領域にある）
    qx = [p[0] for p in page.qr_quad]
    qy = [p[1] for p in page.qr_quad]
    assert min(qx) > page.canvas_w * 0.7 and min(qy) > page.canvas_h * 0.7


def test_blank_pdf_qr_is_readable(settings, source_pdf, tmp_path):
    blank = tmp_path / "blank.pdf"
    template_builder.build_blank_pdf(source_pdf, blank, "tpl-qr", dpi=300)
    image = pdfutil.pdf_page_arrays(blank, 300)[0]
    assert markers.detect_qr_payload(image) == ("tpl-qr", 1)


def test_build_blank_pdf_warns_on_content_overlap(settings, tmp_path):
    """マーカー位置に既存の内容があれば警告する（自動移動はしない。SPEC §7.2）。"""
    source = tmp_path / "busy.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    # 左上マーカーの位置を黒く塗りつぶす
    rect = template_builder.marker_rects_pt(595.0, 842.0)[0]
    page.draw_rect(rect, color=(0, 0, 0), fill=(0, 0, 0))
    doc.save(str(source))
    doc.close()

    result = template_builder.build_blank_pdf(source, tmp_path / "blank.pdf", "tpl-2", dpi=300)
    assert any("マーカー 0" in w for w in result.warnings)


def test_measure_pages_multipage(settings, tmp_path):
    source = tmp_path / "two.pdf"
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    doc.new_page(width=595, height=842)
    doc.save(str(source))
    doc.close()

    blank = tmp_path / "blank.pdf"
    result = template_builder.build_blank_pdf(source, blank, "tpl-3", dpi=300)
    assert [p.page_no for p in result.pages] == [1, 2]

    image = pdfutil.pdf_page_arrays(blank, 300)[1]
    assert markers.detect_qr_payload(image) == ("tpl-3", 2)  # ページごとに QR が違う

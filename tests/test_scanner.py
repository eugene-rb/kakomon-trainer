"""スキャン処理（正規化・QR 判定・切り出し・OCR）のテスト。"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from app import geometry, markers, pdfutil, scanner
from app.geometry import AlignmentError
from app.ocr.base import OCRBackend, OCRResult, Word


class StubOCR(OCRBackend):
    name = "stub"
    supports_words = True

    def __init__(self, text: str = "If I had known the fact") -> None:
        self.text = text
        self.calls: list[tuple[int, str]] = []

    def recognize(self, image_png: bytes, language: str) -> OCRResult:
        self.calls.append((len(image_png), language))
        words = [
            Word(text=t, bbox=(i * 60, 10, i * 60 + 50, 40))
            for i, t in enumerate(self.text.split())
        ]
        return OCRResult(text=self.text, words=words)


def _scanned_image(settings, template, distort: bool = True) -> np.ndarray:
    """blank.pdf をレンダリングし、必要なら歪ませて「スキャン画像」を作る。"""
    blank = settings.template_dir(template.template_id) / "blank.pdf"
    image = pdfutil.pdf_page_arrays(blank, 300)[0]
    if not distort:
        return image
    h, w = image.shape[:2]
    matrix = cv2.getPerspectiveTransform(
        np.float32([[0, 0], [w, 0], [w, h], [0, h]]),
        np.float32([[40, 90], [w - 30, 30], [w - 60, h - 40], [20, h - 100]]),
    )
    return cv2.warpPerspective(image, matrix, (w, h), borderValue=(255, 255, 255))


def test_normalize_page_resolves_template_from_qr(settings, built_template):
    loaded = scanner.LoadedPage(source_index=0, image=_scanned_image(settings, built_template), origin="scan.pdf")
    page = scanner.normalize_page(loaded, [built_template], fallback_page_no=1)

    assert page.template.template_id == built_template.template_id
    assert page.page_no == 1
    assert page.qr_detected is True
    # alignment_error_px は ArUco 中心と QR 隅の残差の max。QR 隅は検出ノイズが数 px あるため
    # ArUco 単独より緩い（透視補正そのものの精度は test_full_normalization_pipeline_absolute_error
    # が ArUco 残差 < 3px で担保している）。
    assert page.alignment_error_px is not None and page.alignment_error_px < 6.0


def test_normalize_page_fails_without_markers(settings, built_template):
    blank_image = np.full((3508, 2480, 3), 255, np.uint8)
    loaded = scanner.LoadedPage(source_index=0, image=blank_image, origin="scan.png")
    with pytest.raises(AlignmentError, match="位置合わせに失敗"):
        scanner.normalize_page(loaded, [built_template], fallback_page_no=1)


def test_crop_regions_records_actual_origin(settings, built_template):
    page = scanner.NormalizedPage(
        source_index=0,
        image=_scanned_image(settings, built_template, distort=False),
        template=built_template,
        page_no=1,
        qr_detected=True,
        alignment_error_px=0.0,
        marker_ids=[0, 1, 3],
    )
    session_dir = settings.session_dir("s1")
    crops = scanner.crop_regions(session_dir, [page], built_template, margin_px=8)

    assert len(crops) == 1
    crop = crops[0]
    assert (session_dir / "crops" / crop.filename).exists()

    page_info = built_template.pages[0]
    canvas = geometry.norm_rect_to_canvas(
        geometry.make_norm_rect(*built_template.questions[0].regions[0].rect),
        page_info.canvas_w,
        page_info.canvas_h,
    )
    # 内部の領域なのでマージン 8px がそのまま効く
    assert crop.crop_origin_canvas == (round(canvas[0]) - 8, round(canvas[1]) - 8)
    assert crop.margin_px == 8


def test_process_scan_end_to_end(settings, built_template):
    """PDF 投入 → 正規化 → 切り出し → OCR → session.json 相当の状態まで。"""
    scan_pdf = settings.data_dir / "scan.pdf"
    image = _scanned_image(settings, built_template)
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(pymupdf.Rect(0, 0, 595, 842), stream=pdfutil.encode_png(image))
    doc.save(str(scan_pdf))
    doc.close()

    backend = StubOCR()
    session = scanner.process_scan(
        settings, "s1", [scan_pdf], built_template, [built_template], backend
    )

    assert session.status == "ready"
    assert len(session.pages) == 1
    assert session.pages[0].qr_detected is True
    assert len(session.crops) == 1
    assert session.crops[0].ocr_text == "If I had known the fact"
    assert len(session.crops[0].ocr_words) == 6
    assert session.transcriptions[0].transcription == "If I had known the fact"
    assert session.transcriptions[0].is_blank is False
    assert backend.calls and backend.calls[0][1] == "en"  # 設問の language が渡る


def test_process_scan_quarantines_failed_pages(settings, built_template):
    """マーカーが無いページは failed/ に退避し、他ページの処理は継続する（SPEC §10.1）。"""
    blank = settings.data_dir / "blank_page.png"
    blank.write_bytes(pdfutil.encode_png(np.full((3508, 2480, 3), 255, np.uint8)))
    good = settings.data_dir / "good_page.png"
    good.write_bytes(pdfutil.encode_png(_scanned_image(settings, built_template)))

    session = scanner.process_scan(
        settings, "s2", [blank, good], built_template, [built_template], StubOCR()
    )

    assert session.status == "ready"
    assert len(session.failed_pages) == 1
    assert (settings.session_dir("s2") / "failed" / session.failed_pages[0].filename).exists()
    assert len(session.pages) == 1  # 正常ページは処理されている


def test_build_transcriptions_joins_regions_in_order(built_template):
    from app.models import CropInfo

    crops = [
        CropInfo(
            question_id="1-(1)", index=1, page_no=1, region_rect=(0.1, 0.5, 0.9, 0.6),
            crop_origin_canvas=(0, 0), crop_w=10, crop_h=10, margin_px=8, ocr_text="second",
        ),
        CropInfo(
            question_id="1-(1)", index=0, page_no=1, region_rect=(0.1, 0.1, 0.9, 0.2),
            crop_origin_canvas=(0, 0), crop_w=10, crop_h=10, margin_px=8, ocr_text="first",
        ),
    ]
    transcriptions = scanner.build_transcriptions(built_template, crops)
    assert transcriptions[0].transcription == "first\nsecond"


def test_make_session_id_format(built_template):
    session_id = scanner.make_session_id(built_template.template_id)
    assert session_id.endswith(built_template.template_id)
    assert len(session_id.split("-")[0]) == 8  # YYYYMMDD


def test_session_ids_are_unique_within_same_second(monkeypatch):
    from app.models import now_jst

    fixed = now_jst()
    monkeypatch.setattr(scanner, "now_jst", lambda: fixed)
    assert len({scanner.make_session_id("same-template") for _ in range(100)}) == 100

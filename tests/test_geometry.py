"""座標変換とホモグラフィのテスト（SPEC §12）。"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from app import geometry, markers, pdfutil
from app.geometry import (
    AlignmentError,
    alignment_errors,
    build_homography,
    canvas_rect_to_crop,
    canvas_rect_to_norm,
    canvas_rect_to_pdf,
    crop_bounds,
    crop_rect_to_canvas,
    make_canvas_px,
    make_canvas_rect,
    make_crop_rect,
    make_norm_rect,
    norm_rect_to_canvas,
    warp_to_canvas,
)


def test_norm_canvas_roundtrip():
    rect = make_norm_rect(0.12, 0.31, 0.88, 0.44)
    canvas = norm_rect_to_canvas(rect, 2480, 3508)
    assert canvas == pytest.approx((297.6, 1087.48, 2182.4, 1543.52))
    assert canvas_rect_to_norm(canvas, 2480, 3508) == pytest.approx(rect)


def test_make_norm_rect_rejects_out_of_range():
    with pytest.raises(ValueError):
        make_norm_rect(0.1, 0.2, 1.4, 0.5)
    with pytest.raises(ValueError):
        make_norm_rect(0.5, 0.2, 0.3, 0.5)  # x1 <= x0


def test_conversion_rejects_wrong_coordinate_system():
    """正規化座標をピクセル用の関数に渡したら気付けること（系の取り違え検出）。"""
    with pytest.raises(ValueError, match="正規化座標"):
        # ピクセルのつもりで正規化値を渡してしまったケース
        canvas_rect_to_norm(make_canvas_rect(0.1, 0.2, 0.3, 0.4), 2480, 3508)
    with pytest.raises(ValueError, match="ピクセル座標"):
        # 正規化のつもりでピクセル値を渡してしまったケース
        norm_rect_to_canvas((300.0, 1000.0, 2100.0, 1500.0), 2480, 3508)  # type: ignore[arg-type]


def test_crop_bounds_clamps_at_page_edge():
    """ページ端ではマージンがクランプされ、原点は rect-margin にならない。"""
    rect = make_canvas_rect(3.0, 2.0, 500.0, 400.0)
    origin, w, h = crop_bounds(rect, 2480, 3508, margin_px=8)
    assert origin == (0.0, 0.0)  # 8px 引くと負になるので 0 にクランプされる
    assert (w, h) == (508, 408)

    # 記録した原点で逆変換すれば元に戻る（rect-8 で再計算すると 8px ずれる）
    crop_rect = make_crop_rect(3, 2, 500, 400)
    back = crop_rect_to_canvas(crop_rect, origin)
    assert back == (3.0, 2.0, 500.0, 400.0)


def test_crop_bounds_interior_has_full_margin():
    rect = make_canvas_rect(100.0, 200.0, 500.0, 400.0)
    origin, w, h = crop_bounds(rect, 2480, 3508, margin_px=8)
    assert origin == (92.0, 192.0)
    assert (w, h) == (416, 216)


def test_canvas_crop_roundtrip():
    origin = make_canvas_px(92.0, 192.0)
    canvas = make_canvas_rect(120.0, 210.0, 300.0, 260.0)
    crop = canvas_rect_to_crop(canvas, origin, 416, 216)
    assert crop == (28, 18, 208, 68)
    assert crop_rect_to_canvas(crop, origin) == (120.0, 210.0, 300.0, 260.0)


def test_canvas_to_pdf_scaling():
    """300dpi キャンバス → 72dpi PDF ポイント。Y 反転は不要（PyMuPDF は左上原点）。"""
    rect = make_canvas_rect(0.0, 0.0, 2480.0, 3508.0)
    pdf = canvas_rect_to_pdf(rect, 2480, 3508, 595.0, 842.0)
    assert pdf == pytest.approx((0.0, 0.0, 595.0, 842.0))

    half = make_canvas_rect(1240.0, 1754.0, 2480.0, 3508.0)
    pdf_half = canvas_rect_to_pdf(half, 2480, 3508, 595.0, 842.0)
    assert pdf_half == pytest.approx((297.5, 421.0, 595.0, 842.0))


def test_build_affine_requires_three_markers():
    known = {i: make_canvas_px(float(i * 100), 0.0) for i in range(3)}
    detected = {0: make_canvas_px(0.0, 0.0), 1: make_canvas_px(100.0, 0.0)}
    with pytest.raises(AlignmentError):
        geometry.build_affine(detected, known)


def test_build_homography_requires_four_points():
    """ArUco 3 個だけ（QR 隅なし）では 4 点に満たずホモグラフィは組めない。"""
    known = {0: make_canvas_px(0.0, 0.0), 1: make_canvas_px(100.0, 0.0), 3: make_canvas_px(0.0, 100.0)}
    with pytest.raises(AlignmentError):
        build_homography(known, known)  # 3 点のみ
    # QR 隅 4 点を足せば成立する
    corners = [(90.0, 90.0), (95.0, 90.0), (95.0, 95.0), (90.0, 95.0)]
    H = build_homography(known, known, extra_src=corners, extra_dst=corners)
    assert np.allclose(H / H[2, 2], np.eye(3), atol=1e-6)


def test_build_homography_matches_by_id_not_order(built_template):
    """検出順が ID 順でなくても正しく対応付けられること（実測で [3,1,0] 等）。"""
    page = built_template.pages[0]
    known = {m.id: make_canvas_px(m.cx, m.cy) for m in page.markers}
    assert set(known) == {0, 1, 3}
    shuffled = {mid: known[mid] for mid in [3, 0, 1]}  # ID 順と違う順序
    qr = [tuple(p) for p in page.qr_quad]
    H = build_homography(shuffled, known, extra_src=qr, extra_dst=qr)
    assert np.allclose(H / H[2, 2], np.eye(3), atol=1e-6)


def test_full_normalization_pipeline_absolute_error(settings, built_template):
    """合成透視歪み → scanner の 2 段正規化 → 再検出の絶対誤差で精度を検証する。

    往復変換テストは src/dst を取り違えていても閉じてしまい検証にならないため、
    実際にパイプラインを通した後で既知の絶対座標と突き合わせる。
    3 ArUco + QR 4 隅 の 7 点ホモグラフィで透視歪みが補正できることを確認する。
    """
    from app import scanner

    blank = settings.template_dir(built_template.template_id) / "blank.pdf"
    canvas_img = pdfutil.pdf_page_arrays(blank, 300)[0]
    page = built_template.pages[0]
    known = {m.id: make_canvas_px(m.cx, m.cy) for m in page.markers}
    assert len(known) == 3

    h, w = canvas_img.shape[:2]
    distort = cv2.getPerspectiveTransform(
        np.float32([[0, 0], [w, 0], [w, h], [0, h]]),
        np.float32([[60, 130], [w - 20, 40], [w - 90, h - 60], [30, h - 130]]),
    )
    scanned = cv2.warpPerspective(canvas_img, distort, (w, h), borderValue=(255, 255, 255))

    detected = markers.detect_aruco_markers(scanned)
    assert set(detected) == {0, 1, 3}

    loaded = scanner.LoadedPage(source_index=0, image=scanned, origin="synthetic")
    result = scanner.normalize_page(loaded, [built_template], fallback_page_no=1)

    remeasured = markers.detect_aruco_markers(result.image)
    errors = alignment_errors(remeasured, known)
    assert errors["count"] == 3
    assert errors["max"] < 3.0, f"位置合わせのズレが大きすぎます: {errors}"


def test_alignment_errors_detects_mismatch():
    known = {0: make_canvas_px(100.0, 100.0), 1: make_canvas_px(200.0, 100.0)}
    measured = {0: make_canvas_px(100.0, 100.0), 1: make_canvas_px(215.0, 108.0)}
    errors = alignment_errors(measured, known)
    assert errors["max"] == pytest.approx(17.0, abs=0.1)

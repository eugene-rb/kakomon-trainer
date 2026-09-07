"""ArUco / QR の生成と再検出のテスト（SPEC §12）。"""

from __future__ import annotations

import numpy as np
import pytest

from app import markers, pdfutil
from app.markers import QRFormatError


def test_aruco_roundtrip_by_id():
    """3 隅に置いたマーカーを ID でアクセスして中心が期待位置に一致すること。

    detectMarkers の戻りは検出順で ID 順ではない（実測で [3,1,0] の順）。
    辞書で返す実装ならこの順序に影響されない。右下（id 2）は QR に置換したので置かない。
    """
    image = np.full((1000, 800), 255, np.uint8)
    positions = {0: (60, 60), 1: (640, 60), 3: (60, 840)}
    for marker_id, (x, y) in positions.items():
        image[y : y + 100, x : x + 100] = markers.generate_aruco_marker(marker_id, 100)

    detected = markers.detect_aruco_markers(image)
    assert set(detected) == set(positions)
    for marker_id, (x, y) in positions.items():
        cx, cy = detected[marker_id]
        assert cx == pytest.approx(x + 50, abs=1.0)
        assert cy == pytest.approx(y + 50, abs=1.0)


def test_marker_ids_exclude_bottom_right():
    assert markers.MARKER_IDS == (0, 1, 3)
    with pytest.raises(ValueError):
        markers.generate_aruco_marker(2, 100)


def test_detect_qr_located_returns_quad():
    payload = markers.build_qr_payload("loc-test", 2)
    image = pdfutil.decode_png(markers.generate_qr_png(payload, 400))
    canvas = np.full((600, 600, 3), 255, np.uint8)
    canvas[100:500, 100:500] = image
    found = markers.detect_qr_located(canvas)
    assert found is not None
    text, quad = found
    assert text == payload
    assert quad is not None and len(quad) == 4
    # 4 隅は QR を貼った 100..500 の領域内にある（QR 自体の余白ぶん内側に寄る）
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    assert 90 < min(xs) and max(xs) < 510
    assert 90 < min(ys) and max(ys) < 510
    assert max(xs) - min(xs) > 200 and max(ys) - min(ys) > 200


def test_detect_qr_located_none_when_absent():
    assert markers.detect_qr_located(np.full((200, 200), 255, np.uint8)) is None


def test_aruco_marker_png_has_quiet_zone():
    png, scale = markers.aruco_marker_png(0, 100)
    assert scale > 1.0
    image = pdfutil.decode_png(png)
    # 外周は白（静穏帯）
    assert image[0, 0].mean() > 250
    assert image[-1, -1].mean() > 250
    # 静穏帯込みでも検出できる
    assert 0 in markers.detect_aruco_markers(image)


def test_generate_aruco_rejects_unknown_id():
    with pytest.raises(ValueError):
        markers.generate_aruco_marker(9, 100)


def test_qr_roundtrip():
    payload = markers.build_qr_payload("kyodai-eigo-2024-01", 1)
    assert payload == "MG1|kyodai-eigo-2024-01|1"

    png = markers.generate_qr_png(payload, 400)
    image = pdfutil.decode_png(png)
    # 検出しやすいよう白地に配置する
    canvas = np.full((600, 600, 3), 255, np.uint8)
    canvas[100:500, 100:500] = image
    assert markers.detect_qr(canvas) == payload
    assert markers.detect_qr_payload(canvas) == ("kyodai-eigo-2024-01", 1)


def test_parse_qr_payload():
    assert markers.parse_qr_payload("MG1|abc|3") == ("abc", 3)


def test_parse_qr_payload_rejects_unknown_version():
    with pytest.raises(QRFormatError, match="未対応のフォーマット"):
        markers.parse_qr_payload("MG2|abc|1")


def test_parse_qr_payload_rejects_malformed():
    for bad in ["", "abc", "MG1|abc", "MG1|abc|x", "MG1||1"]:
        with pytest.raises(QRFormatError):
            markers.parse_qr_payload(bad)


def test_build_qr_payload_rejects_pipe_in_id():
    with pytest.raises(ValueError):
        markers.build_qr_payload("bad|id", 1)


def test_detect_qr_returns_none_when_absent():
    assert markers.detect_qr(np.full((200, 200), 255, np.uint8)) is None

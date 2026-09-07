"""ArUco マーカーと QR コードの生成・検出（SPEC §5.2, §5.3）。

配置仕様:
    - ArUco: ``DICT_4X4_50``、四隅のうち **3 個**（0=左上, 1=右上, 3=左下）。右下（id 2）は
      置かず、その位置に QR を置く。1 辺 12mm、ページ端から 8mm の余白位置。
    - QR: 各ページ **右下隅**（端から 8mm）に 1 個、1 辺 15mm。
      内容は ``MG1|<template_id>|<page_no>`` の 1 行文字列。
      検出時の 4 隅は位置合わせの対応点としても使う（3 ArUco + 4 QR隅 = 7 点）。

このモジュールも ``geometry`` 以外の ``app.*`` に依存しない。
"""

from __future__ import annotations

import io
import re

import cv2
import numpy as np
import qrcode
from PIL import Image

from app.geometry import CanvasPx, make_canvas_px

# --------------------------------------------------------------------------
# 配置定数（SPEC §5.2, §5.3）
# --------------------------------------------------------------------------

ARUCO_DICT_ID = cv2.aruco.DICT_4X4_50

#: マーカー ID。値は「ページ上のどの隅か」を表す（SPEC §5.2）。右下(2)は欠番＝QR に置換。
MARKER_TOP_LEFT = 0
MARKER_TOP_RIGHT = 1
MARKER_BOTTOM_LEFT = 3
MARKER_IDS: tuple[int, int, int] = (
    MARKER_TOP_LEFT,
    MARKER_TOP_RIGHT,
    MARKER_BOTTOM_LEFT,
)

MARKER_SIZE_MM = 12.0
MARKER_MARGIN_MM = 8.0

QR_SIZE_MM = 15.0
#: QR をページ端から離す余白（右下隅配置）。マーカーと同じ 8mm。
QR_MARGIN_MM = 8.0

#: ArUco の黒枠の外側に確保する白の静穏帯（マーカー 1 辺に対する比率）。
#: 検出には黒枠の外側に白地が必要なため、画像として貼るときに付与する。
#: 中心位置は変わらないので、記録するマーカー中心の意味は変わらない。
QUIET_ZONE_RATIO = 0.25

#: QR フォーマットバージョン識別子（SPEC §5.3。将来の互換性のため必ず検証する）
QR_VERSION = "MG1"

_QR_PATTERN = re.compile(r"^([A-Za-z0-9]+)\|([^|]+)\|(\d+)$")


class QRFormatError(ValueError):
    """QR の内容が想定フォーマットでない、または未対応バージョン（SPEC §10.2）。"""


# --------------------------------------------------------------------------
# ArUco
# --------------------------------------------------------------------------


def _dictionary():
    return cv2.aruco.getPredefinedDictionary(ARUCO_DICT_ID)


def generate_aruco_marker(marker_id: int, side_px: int) -> np.ndarray:
    """ArUco マーカー画像（グレースケール、静穏帯なし）を生成する。

    Returns:
        shape ``(side_px, side_px)`` の uint8 配列。
    """
    if marker_id not in MARKER_IDS:
        raise ValueError(f"未定義のマーカー ID です: {marker_id}（有効値: {MARKER_IDS}）")
    return cv2.aruco.generateImageMarker(_dictionary(), int(marker_id), side_px)


def aruco_marker_png(marker_id: int, side_px: int) -> tuple[bytes, float]:
    """静穏帯（白枠）付きの ArUco マーカーを PNG バイト列で返す。

    Returns:
        ``(png_bytes, scale)``。``scale`` は「静穏帯込みの画像 1 辺 ÷ マーカー 1 辺」。
        PDF に貼るときは ``マーカー実寸 * scale`` の大きさで配置すれば、
        黒枠部分がちょうど指定実寸になる。
    """
    marker = generate_aruco_marker(marker_id, side_px)
    pad = int(round(side_px * QUIET_ZONE_RATIO))
    canvas = np.full((side_px + pad * 2, side_px + pad * 2), 255, dtype=np.uint8)
    canvas[pad : pad + side_px, pad : pad + side_px] = marker
    ok, buf = cv2.imencode(".png", canvas)
    if not ok:
        raise RuntimeError("ArUco マーカーの PNG エンコードに失敗しました")
    scale = (side_px + pad * 2) / side_px
    return buf.tobytes(), scale


def detect_aruco_markers(image: np.ndarray) -> dict[int, CanvasPx]:
    """画像から ArUco マーカーを検出し、``ID -> 中心座標`` の辞書を返す。

    Args:
        image: グレースケールまたは BGR 画像。

    Returns:
        検出できたマーカーの ``{id: (cx, cy)}``。座標は入力画像のピクセル座標。

    Note:
        戻り値を **辞書** にしているのは意図的である。``detectMarkers()`` は
        検出順に結果を返し ID 順には並ばない（4 隅に 0,1,2,3 を置いた実測で
        ``[2, 3, 1, 0]`` の順に返ることを確認済み）。リストのまま index で
        対応付けると、例外を出さずに誤ったホモグラフィが作られてしまう。
    """
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    detector = cv2.aruco.ArucoDetector(_dictionary(), cv2.aruco.DetectorParameters())
    corners, ids, _rejected = detector.detectMarkers(gray)
    result: dict[int, CanvasPx] = {}
    if ids is None:
        return result
    for corner, marker_id in zip(corners, ids.reshape(-1)):
        # corner の shape は (1, 4, 2)。4 隅の平均が中心。
        cx, cy = corner[0].mean(axis=0)
        result[int(marker_id)] = make_canvas_px(float(cx), float(cy))
    return result


# --------------------------------------------------------------------------
# QR
# --------------------------------------------------------------------------


def build_qr_payload(template_id: str, page_no: int) -> str:
    """QR に埋め込む 1 行文字列を組み立てる（SPEC §5.3）。"""
    if "|" in template_id:
        raise ValueError(f"テンプレート ID に | は使えません: {template_id}")
    if page_no < 1:
        raise ValueError(f"ページ番号は 1 以上である必要があります: {page_no}")
    return f"{QR_VERSION}|{template_id}|{page_no}"


def parse_qr_payload(payload: str) -> tuple[str, int]:
    """QR の内容を ``(template_id, page_no)`` に分解する。

    Raises:
        QRFormatError: フォーマット不一致、または ``MG1`` 以外のバージョン識別子。
    """
    m = _QR_PATTERN.match(payload.strip())
    if not m:
        raise QRFormatError(f"QR の内容が想定フォーマットではありません: {payload!r}")
    version, template_id, page_no = m.group(1), m.group(2), int(m.group(3))
    if version != QR_VERSION:
        raise QRFormatError(
            f"未対応のフォーマットです（このシステムは {QR_VERSION} のみ対応）: {version}"
        )
    return template_id, page_no


def generate_qr_png(payload: str, side_px: int) -> bytes:
    """QR コードを PNG バイト列で生成する。"""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=4,
        border=2,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("L")
    img = img.resize((side_px, side_px), Image.Resampling.NEAREST)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _quad_from_points(points) -> list[tuple[float, float]] | None:
    """cv2 QR 検出の points 配列を 4 点タプル列にする。形が想定外なら None。"""
    if points is None:
        return None
    arr = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    if arr.shape[0] < 4:
        return None
    return [(float(x), float(y)) for x, y in arr[:4]]


def detect_qr_located(image: np.ndarray) -> tuple[str, list[tuple[float, float]] | None] | None:
    """画像から QR を検出し ``(内容, 4隅)`` を返す。読み取れなければ None。

    4 隅は入力画像のピクセル座標（cv2 の順: 左上→右上→右下→左下）。検出はできたが
    座標が取れなかった場合は 4 隅を ``None`` にして内容だけ返す。

    SPEC §5.4 手順6 のとおり、**正規化後の画像** に対して呼ぶこと
    （歪み補正後のほうが読み取り率が高い）。
    """
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    detector = cv2.QRCodeDetector()
    try:
        text, points, _ = detector.detectAndDecode(gray)
    except cv2.error:
        text, points = "", None
    if text:
        return text, _quad_from_points(points)
    # 単一検出で失敗した場合は複数検出も試す（複数の矩形が誤検出される用紙があるため）
    try:
        ok, texts, multi_points, _ = detector.detectAndDecodeMulti(gray)
    except cv2.error:
        return None
    if ok:
        for i, t in enumerate(texts):
            if t:
                quad = None
                if multi_points is not None and i < len(multi_points):
                    quad = _quad_from_points(multi_points[i])
                return t, quad
    return None


def detect_qr(image: np.ndarray) -> str | None:
    """画像から QR を検出して内容を返す。読み取れなければ None。"""
    found = detect_qr_located(image)
    return found[0] if found is not None else None


def detect_qr_payload(image: np.ndarray) -> tuple[str, int] | None:
    """QR を検出してパースまで行う。読めない場合は None、フォーマット不正は例外。"""
    text = detect_qr(image)
    if text is None:
        return None
    return parse_qr_payload(text)

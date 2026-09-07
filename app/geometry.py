"""座標系・ホモグラフィ・正規化（SPEC §5）。

このシステムには **4 系統** の座標が登場する。仕様書 §5 は 3 系統を挙げているが、
赤入れ PDF の描画時には PDF ユーザー空間（72dpi ポイント）への変換が必ず発生するため、
それも独立した系として明示的に扱う。

===========  ==========================  ==================================
系            範囲                        主な用途
===========  ==========================  ==================================
Norm          [0.0, 1.0]                  template.json の regions[].rect
CanvasPx      [0, canvas_w] x [0, canvas_h]  300dpi 正規化画像上の全処理
CropPx        [0, crop_w] x [0, crop_h]   OCR の word bbox、quote 突き合わせ
PdfPt         72dpi ユーザー空間          PyMuPDF での描画
===========  ==========================  ==================================

型は ``NewType`` で分離しているが、``NewType`` は実行時には恒等関数であり
それ自体は何も守らない。この種の座標バグは「例外を出さずに、それっぽいが
微妙にズレた出力が出る」というサイレント破壊が本質的な危険なので、
**生成の唯一の入口となるファクトリ関数で値域と順序を検証**し、変換関数の
入口にも軽量な assert を置いて系の取り違えを早期に落とす。

このモジュールは ``app.*`` を一切 import しない leaf モジュールとする
（``models`` との相互 import を構造的に排除するため）。
"""

from __future__ import annotations

from typing import NewType, Sequence

import cv2
import numpy as np

# --------------------------------------------------------------------------
# 座標型
# --------------------------------------------------------------------------

#: 正規化座標 [0,1]（キャンバスに対する比率）
NormPoint = NewType("NormPoint", tuple[float, float])
NormRect = NewType("NormRect", tuple[float, float, float, float])

#: キャンバス実ピクセル座標（blank.pdf を 300dpi でレンダリングした画像の座標系）
CanvasPx = NewType("CanvasPx", tuple[float, float])
CanvasRectPx = NewType("CanvasRectPx", tuple[float, float, float, float])

#: 切り出し画像内ピクセル座標（マージン込みの crops/*.png の座標系）
CropPx = NewType("CropPx", tuple[int, int])
CropRectPx = NewType("CropRectPx", tuple[int, int, int, int])

#: PDF ユーザー空間（72dpi ポイント、PyMuPDF の慣習に合わせ左上原点・Y 下向き）
PdfPointPt = NewType("PdfPointPt", tuple[float, float])
PdfRectPt = NewType("PdfRectPt", tuple[float, float, float, float])

#: 正規化座標とピクセル座標を取り違えたときに検出するための閾値。
#: 正規化値は必ず 1.0 以下、ピクセル値は実用上必ずこれより大きい。
_PX_SANITY_MIN = 1.5

PT_PER_INCH = 72.0
MM_PER_INCH = 25.4


# --------------------------------------------------------------------------
# 単位変換
# --------------------------------------------------------------------------


def mm_to_pt(mm: float) -> float:
    """ミリメートル → PDF ポイント(1/72 inch)。"""
    return mm / MM_PER_INCH * PT_PER_INCH


def mm_to_px(mm: float, dpi: int) -> float:
    """ミリメートル → 指定 dpi でのピクセル。"""
    return mm / MM_PER_INCH * dpi


def pt_to_px(pt: float, dpi: int) -> float:
    """PDF ポイント → 指定 dpi でのピクセル。"""
    return pt / PT_PER_INCH * dpi


def px_to_pt(px: float, dpi: int) -> float:
    """指定 dpi でのピクセル → PDF ポイント。"""
    return px / dpi * PT_PER_INCH


# --------------------------------------------------------------------------
# ファクトリ（各型の唯一の生成入口。直接コンストラクタを呼ばないこと）
# --------------------------------------------------------------------------


def make_norm_rect(x0: float, y0: float, x1: float, y1: float) -> NormRect:
    """正規化矩形を生成する。[0,1] 範囲・x1>x0・y1>y0 を検証する。"""
    rect = (float(x0), float(y0), float(x1), float(y1))
    if not all(0.0 <= v <= 1.0 for v in rect):
        raise ValueError(f"正規化矩形は [0,1] の範囲である必要があります: {rect}")
    if not (rect[2] > rect[0] and rect[3] > rect[1]):
        raise ValueError(f"正規化矩形は x1>x0 かつ y1>y0 である必要があります: {rect}")
    return NormRect(rect)


def make_norm_point(x: float, y: float) -> NormPoint:
    """正規化点を生成する。"""
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        raise ValueError(f"正規化点は [0,1] の範囲である必要があります: {(x, y)}")
    return NormPoint((float(x), float(y)))


def make_canvas_px(x: float, y: float) -> CanvasPx:
    """キャンバス実ピクセル点を生成する。"""
    return CanvasPx((float(x), float(y)))


def make_canvas_rect(x0: float, y0: float, x1: float, y1: float) -> CanvasRectPx:
    """キャンバス実ピクセル矩形を生成する。x1>x0・y1>y0 を検証する。"""
    rect = (float(x0), float(y0), float(x1), float(y1))
    if not (rect[2] > rect[0] and rect[3] > rect[1]):
        raise ValueError(f"矩形は x1>x0 かつ y1>y0 である必要があります: {rect}")
    return CanvasRectPx(rect)


def make_crop_rect(x0: int, y0: int, x1: int, y1: int) -> CropRectPx:
    """切り出し画像内の矩形を生成する（整数ピクセル）。"""
    rect = (int(x0), int(y0), int(x1), int(y1))
    if not (rect[2] > rect[0] and rect[3] > rect[1]):
        raise ValueError(f"矩形は x1>x0 かつ y1>y0 である必要があります: {rect}")
    return CropRectPx(rect)


def _assert_looks_normalized(rect: Sequence[float], where: str) -> None:
    if not all(0.0 <= v <= 1.0 for v in rect):
        raise ValueError(
            f"{where}: 正規化座標 [0,1] を期待しましたが範囲外の値です（"
            f"ピクセル座標を渡していませんか）: {tuple(rect)}"
        )


def _assert_looks_pixels(rect: Sequence[float], where: str) -> None:
    # 全成分が 1.0 以下なら、ほぼ確実に正規化値を誤って渡している。
    if all(abs(v) <= _PX_SANITY_MIN for v in rect) and any(v != 0 for v in rect):
        raise ValueError(
            f"{where}: ピクセル座標を期待しましたが全成分が {_PX_SANITY_MIN} 以下です（"
            f"正規化座標を渡していませんか）: {tuple(rect)}"
        )


# --------------------------------------------------------------------------
# Norm <-> Canvas
# --------------------------------------------------------------------------


def norm_rect_to_canvas(rect: NormRect, canvas_w: int, canvas_h: int) -> CanvasRectPx:
    """正規化矩形 → キャンバス実ピクセル矩形（SPEC §5.1: x_px = x_norm * canvas_w）。"""
    _assert_looks_normalized(rect, "norm_rect_to_canvas")
    x0, y0, x1, y1 = rect
    return make_canvas_rect(x0 * canvas_w, y0 * canvas_h, x1 * canvas_w, y1 * canvas_h)


def canvas_rect_to_norm(rect: CanvasRectPx, canvas_w: int, canvas_h: int) -> NormRect:
    """キャンバス実ピクセル矩形 → 正規化矩形。範囲外は [0,1] にクランプする。"""
    _assert_looks_pixels(rect, "canvas_rect_to_norm")
    x0, y0, x1, y1 = rect
    clamp = lambda v: min(1.0, max(0.0, v))  # noqa: E731
    return make_norm_rect(
        clamp(x0 / canvas_w), clamp(y0 / canvas_h), clamp(x1 / canvas_w), clamp(y1 / canvas_h)
    )


def norm_point_to_canvas(pt: NormPoint, canvas_w: int, canvas_h: int) -> CanvasPx:
    """正規化点 → キャンバス実ピクセル点。"""
    return make_canvas_px(pt[0] * canvas_w, pt[1] * canvas_h)


# --------------------------------------------------------------------------
# Canvas <-> Crop
# --------------------------------------------------------------------------


def crop_bounds(
    rect: CanvasRectPx, canvas_w: int, canvas_h: int, margin_px: int
) -> tuple[CanvasPx, int, int]:
    """切り出し範囲を求める（SPEC §9.3: 上下左右にマージンを付与）。

    Returns:
        (origin, crop_w, crop_h)。``origin`` はクランプ **後** の左上隅（CanvasPx）。
        ページ端ではマージンが削られるため、``rect - margin`` で再計算してはならない。
        呼び出し側はこの origin を CropInfo に保存し、逆変換にはそれを使うこと。
    """
    _assert_looks_pixels(rect, "crop_bounds")
    x0, y0, x1, y1 = rect
    ox = max(0, int(round(x0 - margin_px)))
    oy = max(0, int(round(y0 - margin_px)))
    ex = min(canvas_w, int(round(x1 + margin_px)))
    ey = min(canvas_h, int(round(y1 + margin_px)))
    if ex <= ox or ey <= oy:
        raise ValueError(f"切り出し範囲が空になりました: rect={rect}, canvas=({canvas_w},{canvas_h})")
    return make_canvas_px(ox, oy), ex - ox, ey - oy


def crop_rect_to_canvas(rect: CropRectPx, crop_origin: CanvasPx) -> CanvasRectPx:
    """切り出し画像内の矩形 → キャンバス実ピクセル矩形（SPEC §9.8 手順5）。

    ``crop_origin`` は ``crop_bounds`` が返した実測原点（CropInfo に保存済みのもの）。
    """
    ox, oy = crop_origin
    x0, y0, x1, y1 = rect
    return make_canvas_rect(x0 + ox, y0 + oy, x1 + ox, y1 + oy)


def canvas_rect_to_crop(
    rect: CanvasRectPx, crop_origin: CanvasPx, crop_w: int, crop_h: int
) -> CropRectPx:
    """キャンバス実ピクセル矩形 → 切り出し画像内の矩形（切り出し範囲でクランプ）。"""
    _assert_looks_pixels(rect, "canvas_rect_to_crop")
    ox, oy = crop_origin
    x0, y0, x1, y1 = rect
    return make_crop_rect(
        max(0, int(round(x0 - ox))),
        max(0, int(round(y0 - oy))),
        min(crop_w, int(round(x1 - ox))),
        min(crop_h, int(round(y1 - oy))),
    )


# --------------------------------------------------------------------------
# Canvas -> PdfPt
# --------------------------------------------------------------------------


def canvas_rect_to_pdf(
    rect: CanvasRectPx, canvas_w: int, canvas_h: int, page_w_pt: float, page_h_pt: float
) -> PdfRectPt:
    """キャンバス実ピクセル矩形 → PDF ポイント矩形。

    赤入れ PDF は正規化画像をページ全面に貼って作る（SPEC §5.5）ため、
    キャンバスとページは 1:1 対応し、線形スケールだけで変換できる。
    PyMuPDF のページ座標は左上原点・Y 下向きでキャンバスと同じ向きなので Y 反転は不要。
    """
    _assert_looks_pixels(rect, "canvas_rect_to_pdf")
    sx = page_w_pt / canvas_w
    sy = page_h_pt / canvas_h
    x0, y0, x1, y1 = rect
    return PdfRectPt((x0 * sx, y0 * sy, x1 * sx, y1 * sy))


def canvas_point_to_pdf(
    pt: CanvasPx, canvas_w: int, canvas_h: int, page_w_pt: float, page_h_pt: float
) -> PdfPointPt:
    """キャンバス実ピクセル点 → PDF ポイント点。"""
    return PdfPointPt((pt[0] * page_w_pt / canvas_w, pt[1] * page_h_pt / canvas_h))


# --------------------------------------------------------------------------
# ホモグラフィと正規化（SPEC §5.4）
# --------------------------------------------------------------------------

#: 位置合わせに必要な最小マーカー数（3 ArUco。QR が読めればさらに 4 隅が加わる）
MIN_MARKERS = 3
#: ホモグラフィ推定に必要な最小対応点数
MIN_HOMOGRAPHY_POINTS = 4


class AlignmentError(RuntimeError):
    """位置合わせに失敗した（マーカー不足・ホモグラフィ計算不能）。"""


def _id_matched_points(
    detected: dict[int, CanvasPx], known: dict[int, CanvasPx]
) -> tuple[np.ndarray, np.ndarray]:
    """ID で対応付けた ``(src, dst)`` 点列を返す。

    対応付けは **必ず ID で行う**。``cv2.aruco`` の ``detectMarkers()`` は検出順で
    結果を返し ID 順に並ばない（実測で ``[2,3,1,0]``）。index を zip すると
    例外を出さずにデタラメな変換が得られてしまう。
    """
    common = sorted(set(detected) & set(known))
    src = np.array([detected[i] for i in common], dtype=np.float64).reshape(-1, 2)
    dst = np.array([known[i] for i in common], dtype=np.float64).reshape(-1, 2)
    return src, dst


def build_affine(detected: dict[int, CanvasPx], known: dict[int, CanvasPx]) -> np.ndarray:
    """検出マーカー中心 → 既知マーカー中心 のアフィン変換 A（2x3）を求める。

    3 個の ArUco だけで組める粗い正規化用。透視歪みは補正できないが、QR を読める程度に
    傾き・スケール・平行移動を揃えるには十分。
    """
    src, dst = _id_matched_points(detected, known)
    if len(src) < MIN_MARKERS:
        raise AlignmentError(
            f"位置合わせに必要なマーカーが足りません（検出 {sorted(detected)} / "
            f"必要 {MIN_MARKERS} 個以上、既知 {sorted(known)}）"
        )
    if len(src) == 3:
        # 3 点はアフィンの厳密解（fiducial 同士なので外れ値なし）
        return cv2.getAffineTransform(src.astype(np.float32), dst.astype(np.float32))
    A, _mask = cv2.estimateAffine2D(src, dst, method=cv2.RANSAC)
    if A is None:
        raise AlignmentError("アフィン変換を計算できませんでした（マーカー配置が退化しています）")
    return A


def build_homography(
    detected: dict[int, CanvasPx],
    known: dict[int, CanvasPx],
    *,
    extra_src: Sequence[tuple[float, float]] | None = None,
    extra_dst: Sequence[tuple[float, float]] | None = None,
) -> np.ndarray:
    """検出点 → 既知点 のホモグラフィ H（3x3）を求める。

    Args:
        detected: スキャン画像上で検出した ArUco マーカー中心。ID -> 点（スキャン画像px）。
        known: template.json に記録された既知のマーカー中心。ID -> 点（CanvasPx）。
        extra_src / extra_dst: ID を持たない追加の対応点（QR の 4 隅など）。
            スキャン画像px 側 / キャンバスpx 側で同数・同順に渡す。

    3 ArUco + 4 QR隅 = 7 点なら過剰決定になり、RANSAC/最小二乗が ArUco 中心にも
    実残差を残す（``alignment_errors`` の品質指標が有効なまま）。

    Returns:
        3x3 のホモグラフィ行列。スキャン画像座標 → キャンバス座標へ写す。
    """
    src, dst = _id_matched_points(detected, known)
    if extra_src is not None and extra_dst is not None and len(extra_src) == len(extra_dst) > 0:
        src = np.vstack([src, np.array(extra_src, dtype=np.float64).reshape(-1, 2)])
        dst = np.vstack([dst, np.array(extra_dst, dtype=np.float64).reshape(-1, 2)])
    if len(src) < MIN_HOMOGRAPHY_POINTS:
        raise AlignmentError(
            f"ホモグラフィに必要な対応点が足りません（{len(src)} 点 / "
            f"必要 {MIN_HOMOGRAPHY_POINTS} 点。検出 {sorted(detected)}）"
        )
    # 対応点はすべて既知 fiducial（ArUco 中心・QR 隅）で外れ値は無い。RANSAC で
    # やや不正確な QR 隅を捨てると 3 点に痩せて不安定になるため、全点最小二乗で解く。
    H, _mask = cv2.findHomography(src, dst, method=0)
    if H is None:
        raise AlignmentError("ホモグラフィを計算できませんでした（点配置が退化しています）")
    return H


def invert_affine(A: np.ndarray) -> np.ndarray:
    """2x3 アフィン行列の逆変換を返す。"""
    return cv2.invertAffineTransform(A)


def transform_points_affine(
    A: np.ndarray, points: Sequence[tuple[float, float]]
) -> list[tuple[float, float]]:
    """2x3 アフィン行列 A で点列を写す。"""
    if not points:
        return []
    arr = np.array(points, dtype=np.float64).reshape(-1, 1, 2)
    out = cv2.transform(arr, A).reshape(-1, 2)
    return [(float(x), float(y)) for x, y in out]


def warp_to_canvas(image: np.ndarray, H: np.ndarray, canvas_w: int, canvas_h: int) -> np.ndarray:
    """ホモグラフィでスキャン画像をキャンバス座標系に正規化する（SPEC §5.4 手順4）。"""
    return cv2.warpPerspective(
        image, H, (canvas_w, canvas_h), flags=cv2.INTER_LINEAR, borderValue=(255, 255, 255)
    )


def warp_affine_to_canvas(image: np.ndarray, A: np.ndarray, canvas_w: int, canvas_h: int) -> np.ndarray:
    """アフィン変換でスキャン画像を粗くキャンバス座標系へ正規化する（2 段正規化の 1 段目）。"""
    return cv2.warpAffine(
        image, A, (canvas_w, canvas_h), flags=cv2.INTER_LINEAR, borderValue=(255, 255, 255)
    )


def transform_points(H: np.ndarray, points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """ホモグラフィ H で点列を写す。入出力とも同じ座標系の慣習に従う。"""
    if not points:
        return []
    arr = np.array(points, dtype=np.float64).reshape(-1, 1, 2)
    out = cv2.perspectiveTransform(arr, H).reshape(-1, 2)
    return [(float(x), float(y)) for x, y in out]


def alignment_errors(
    measured: dict[int, CanvasPx], known: dict[int, CanvasPx]
) -> dict[str, float]:
    """正規化後に再検出したマーカー中心と既知中心のズレを絶対値で評価する。

    往復変換（norm→canvas→norm）のテストは、対応付けを取り違えていても
    「一貫して間違っていれば」閉じてしまうため検証にならない。実際にパイプラインを
    通した結果を既知の絶対座標と突き合わせるのが唯一の有効な検証になる（SPEC M1）。

    Returns:
        ``{"max": 最大ズレpx, "rms": RMS px, "count": 比較したマーカー数}``
    """
    common = sorted(set(measured) & set(known))
    if not common:
        return {"max": float("inf"), "rms": float("inf"), "count": 0}
    diffs = []
    for i in common:
        mx, my = measured[i]
        kx, ky = known[i]
        diffs.append(((mx - kx) ** 2 + (my - ky) ** 2) ** 0.5)
    arr = np.array(diffs, dtype=np.float64)
    return {
        "max": float(arr.max()),
        "rms": float(np.sqrt((arr**2).mean())),
        "count": float(len(common)),
    }

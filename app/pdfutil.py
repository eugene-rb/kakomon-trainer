"""PDF のラスタライズ周りの共通処理。

PyMuPDF のページ座標は **左上原点・Y 下向き** で、キャンバス座標と同じ向きである
（実測確認済み: 72dpi 空間の (10,10) に描いた矩形が 300dpi ラスタで (41,41)px に出る）。
そのため Canvas <-> PdfPt の変換に Y 反転は不要（``geometry.canvas_rect_to_pdf``）。
"""

from __future__ import annotations

import io
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image

#: PDF とみなす拡張子
PDF_SUFFIXES = {".pdf"}
#: 画像とみなす拡張子（SPEC §9.2: 入力は PDF または PNG/JPEG）
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}


def open_pdf(path: str | Path) -> pymupdf.Document:
    """PDF を開く。"""
    return pymupdf.open(str(path))


def validate_pdf_bytes(data: bytes) -> None:
    """登録前にメモリ上で検証する。壊れた入力のファイルハンドルを残さない。"""
    try:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            if doc.needs_pass:
                raise ValueError("パスワードを解除したPDFを選んでください。")
            if not doc.page_count:
                raise ValueError("ページのあるPDFを選んでください。")
    except ValueError:
        raise
    except Exception as e:
        raise ValueError("PDFを読み込めませんでした。ファイルを開けるか確認して選び直してください。") from e


def render_page_to_array(page: pymupdf.Page, dpi: int) -> np.ndarray:
    """PDF ページを指定 dpi の BGR 配列にラスタライズする。"""
    pm = page.get_pixmap(dpi=dpi)
    arr = np.frombuffer(pm.samples, dtype=np.uint8).reshape(pm.height, pm.width, pm.n)
    if pm.n == 4:  # RGBA
        return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
    if pm.n == 3:  # RGB
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)


def render_pdf_page_png(path: str | Path, page_no: int, dpi: int) -> bytes:
    """PDF の 1 ページ（1 始まり）を PNG バイト列でレンダリングする。"""
    with open_pdf(path) as doc:
        if not (1 <= page_no <= doc.page_count):
            raise ValueError(f"ページ番号が範囲外です: {page_no}（1..{doc.page_count}）")
        pm = doc[page_no - 1].get_pixmap(dpi=dpi)
        return pm.tobytes("png")


def pdf_page_arrays(path: str | Path, dpi: int) -> list[np.ndarray]:
    """PDF の全ページを BGR 配列のリストにする。"""
    out: list[np.ndarray] = []
    with open_pdf(path) as doc:
        for page in doc:
            out.append(render_page_to_array(page, dpi))
    return out


def page_size_pt(path: str | Path, page_no: int) -> tuple[float, float]:
    """PDF ページの MediaBox サイズを (幅pt, 高さpt) で返す。"""
    with open_pdf(path) as doc:
        rect = doc[page_no - 1].rect
        return float(rect.width), float(rect.height)


def load_image_file(path: str | Path) -> np.ndarray:
    """画像ファイルを BGR 配列で読み込む（日本語パス対応）。"""
    data = Path(path).read_bytes()
    arr = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        raise ValueError(f"画像を読み込めませんでした: {path}")
    return arr


def encode_png(image: np.ndarray) -> bytes:
    """BGR / グレースケール配列を PNG バイト列にする。"""
    ok, buf = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("PNG エンコードに失敗しました")
    return buf.tobytes()


def decode_png(data: bytes) -> np.ndarray:
    """PNG バイト列を BGR 配列にする。"""
    arr = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        raise ValueError("PNG をデコードできませんでした")
    return arr


def png_to_pil(data: bytes) -> Image.Image:
    """PNG バイト列を PIL 画像にする。"""
    return Image.open(io.BytesIO(data))

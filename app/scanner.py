"""フェーズ2前半: スキャン読込 → 正規化 → QR 判定 → 領域切り出し → OCR（SPEC §5.4, §9.1〜§9.3）。

正規化と QR 判定には「鶏と卵」の関係がある。QR は正規化後の画像から読むほうが確実だが
（SPEC §5.4 手順6）、正規化にはテンプレートの既知マーカー中心が要る。そこで:

    1. 生のスキャン画像から QR が読めればそれを使う（速い経路）
    2. 読めなければ候補テンプレートで一度正規化してから QR を読む
    3. どちらでも読めなければ、指定されたテンプレートを使って暫定処理し、
       UI で手動選択させる（SPEC §10.2）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from app import geometry, markers, pdfutil
from app.config import Settings
from app.geometry import AlignmentError
from app.markers import QRFormatError
from app.models import (
    CropInfo,
    FailedPage,
    PageRecord,
    QuestionTranscription,
    SessionState,
    Template,
    now_jst,
)
from app.ocr import OCRBackend, OCRError, words_to_records

logger = logging.getLogger(__name__)

NORMALIZED_DIR = "normalized"
CROPS_DIR = "crops"
FAILED_DIR = "failed"


@dataclass
class LoadedPage:
    """入力ファイルから取り出した 1 ページ分の生画像。"""

    source_index: int
    image: np.ndarray
    origin: str


@dataclass
class NormalizedPage:
    """正規化に成功したページ。"""

    source_index: int
    image: np.ndarray
    template: Template
    page_no: int
    qr_detected: bool
    alignment_error_px: float | None
    marker_ids: list[int]


def make_session_id(template_id: str) -> str:
    """日時とランダム識別子を含め、同時投入でセッションが上書きされるのを防ぐ。"""
    from uuid import uuid4

    return f"{now_jst().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:12]}-{template_id}"


def load_input_pages(paths: list[Path], dpi: int) -> list[LoadedPage]:
    """PDF / 画像ファイルの並びを 1 ページずつの画像列に展開する（SPEC §9.2）。"""
    pages: list[LoadedPage] = []
    index = 0
    for path in paths:
        suffix = path.suffix.lower()
        if suffix in pdfutil.PDF_SUFFIXES:
            for image in pdfutil.pdf_page_arrays(path, dpi):
                pages.append(LoadedPage(source_index=index, image=image, origin=path.name))
                index += 1
        elif suffix in pdfutil.IMAGE_SUFFIXES:
            pages.append(
                LoadedPage(source_index=index, image=pdfutil.load_image_file(path), origin=path.name)
            )
            index += 1
        else:
            logger.warning("対応していない入力ファイルです。無視します: %s", path.name)
    return pages


def _known_centers(template: Template, page_no: int) -> dict[int, geometry.CanvasPx] | None:
    page = template.page(page_no)
    if page is None:
        return None
    return {mid: geometry.make_canvas_px(*c) for mid, c in page.marker_map().items()}


def _rough_normalize(
    image: np.ndarray, detected: dict[int, geometry.CanvasPx], template: Template, page_no: int
) -> tuple[np.ndarray, np.ndarray] | None:
    """3 ArUco のアフィン変換で粗く正規化する。``(粗正規化画像, アフィン行列 A)`` or None。"""
    page = template.page(page_no)
    known = _known_centers(template, page_no)
    if page is None or known is None:
        return None
    try:
        A = geometry.build_affine(detected, known)
    except AlignmentError:
        return None
    return geometry.warp_affine_to_canvas(image, A, page.canvas_w, page.canvas_h), A


def _refine_normalize(
    raw_image: np.ndarray,
    detected: dict[int, geometry.CanvasPx],
    affine: np.ndarray,
    rough_qr_quad: list[tuple[float, float]] | None,
    template: Template,
    page_no: int,
) -> np.ndarray | None:
    """3 ArUco 中心 ＋ QR の 4 隅（7 点）のホモグラフィで最終正規化する。

    ``rough_qr_quad`` は粗正規化画像で検出した QR の 4 隅（粗キャンバス座標）。これを逆アフィンで
    生画像座標へ戻して対応点にする。QR 隅が無い / テンプレートに qr_quad 未記録なら None
    （呼び出し側は粗正規化結果をそのまま使う）。
    """
    page = template.page(page_no)
    if (
        page is None
        or len(page.qr_quad) != 4
        or rough_qr_quad is None
        or len(rough_qr_quad) != 4
    ):
        return None
    known = _known_centers(template, page_no)
    if known is None:
        return None
    qr_src_raw = geometry.transform_points_affine(geometry.invert_affine(affine), rough_qr_quad)
    try:
        H = geometry.build_homography(
            detected, known, extra_src=qr_src_raw, extra_dst=page.qr_quad
        )
    except AlignmentError:
        return None
    return geometry.warp_to_canvas(raw_image, H, page.canvas_w, page.canvas_h)


def _rough_qr(rough_img: np.ndarray) -> tuple[tuple[str, int] | None, list[tuple[float, float]] | None]:
    """粗正規化画像から QR を読む。``(ペイロード, 4隅)``。フォーマット不正は AlignmentError。"""
    located = markers.detect_qr_located(rough_img)
    if located is None:
        return None, None
    try:
        payload = markers.parse_qr_payload(located[0])
    except QRFormatError as e:
        raise AlignmentError(str(e)) from e
    quad = located[1] if located[1] and len(located[1]) == 4 else None
    return payload, quad


def normalize_page(
    loaded: LoadedPage, candidates: list[Template], fallback_page_no: int
) -> NormalizedPage:
    """1 ページを正規化し、テンプレートとページ番号を確定する（SPEC §5.4）。

    2 段構成:
        1. 3 ArUco のアフィン変換で粗く正規化 → その画像で QR を読む。
        2. QR の 4 隅を対応点に加えた 7 点ホモグラフィで生画像を一発で最終正規化する。
        QR が読めない場合は 1 段目のアフィン結果を暫定採用（SPEC §10.2）。

    Raises:
        AlignmentError: ArUco が 3 個未満で位置合わせできない場合（SPEC §10.1）。
    """
    detected = markers.detect_aruco_markers(loaded.image)
    if len(detected) < geometry.MIN_MARKERS:
        raise AlignmentError(
            f"位置合わせに失敗しました（検出できたマーカー: {sorted(detected)}、"
            f"{geometry.MIN_MARKERS} 個必要）"
        )

    # 生画像から QR が読めれば候補の並べ替えに使う（読めなくても続行）
    resolved: tuple[str, int] | None = None
    try:
        resolved = markers.detect_qr_payload(loaded.image)
    except QRFormatError as e:
        raise AlignmentError(str(e)) from e

    ordered = list(candidates)
    if resolved is not None:
        ordered.sort(key=lambda t: t.template_id != resolved[0])

    for template in ordered:
        if not template.pages:
            continue
        probe_page = (
            resolved[1]
            if resolved and resolved[0] == template.template_id
            else template.pages[0].page_no
        )
        rough = _rough_normalize(loaded.image, detected, template, probe_page)
        if rough is None:
            rough = _rough_normalize(loaded.image, detected, template, template.pages[0].page_no)
        if rough is None:
            continue
        rough_img, affine = rough

        payload, quad = _rough_qr(rough_img)
        if payload is None or payload[0] != template.template_id:
            continue

        page_no = payload[1]
        # 実ページで粗正規化し直す（ページごとに用紙サイズが違う場合の保険）
        repaired = _rough_normalize(loaded.image, detected, template, page_no)
        if repaired is not None:
            rough_img, affine = repaired
            _, quad = _rough_qr(rough_img)

        final = _refine_normalize(loaded.image, detected, affine, quad, template, page_no)
        if final is None:
            final = rough_img
        return NormalizedPage(
            source_index=loaded.source_index,
            image=final,
            template=template,
            page_no=page_no,
            qr_detected=True,
            alignment_error_px=_measure_alignment(final, template, page_no),
            marker_ids=sorted(detected),
        )

    # QR が読めない場合は先頭候補で暫定処理（SPEC §10.2: UI で手動選択させる）
    template = ordered[0] if ordered else None
    if template is None or not template.pages:
        raise AlignmentError("対象テンプレートが存在しません。")
    page_no = fallback_page_no if template.page(fallback_page_no) else template.pages[0].page_no
    rough = _rough_normalize(loaded.image, detected, template, page_no)
    if rough is None:
        raise AlignmentError("正規化を計算できませんでした。")
    return NormalizedPage(
        source_index=loaded.source_index,
        image=rough[0],
        template=template,
        page_no=page_no,
        qr_detected=False,
        alignment_error_px=_measure_alignment(rough[0], template, page_no),
        marker_ids=sorted(detected),
    )


def _measure_alignment(normalized: np.ndarray, template: Template, page_no: int) -> float | None:
    """正規化後の画像から ArUco と QR 隅を再検出し、既知位置との絶対ズレ(px)を測る。

    往復変換ではなく絶対座標との比較にしているのは、対応付けを取り違えていても
    往復テストは閉じてしまい検証にならないため（品質指標として session.json に残す）。

    ArUco 3 点はアフィン fallback 時に厳密一致するため単独では指標にならない。QR 隅の
    残差も併せて取り、右下（QR 側）の当たり具合まで見えるようにする。
    """
    page = template.page(page_no)
    if page is None:
        return None
    known = {mid: geometry.make_canvas_px(*c) for mid, c in page.marker_map().items()}
    errors: list[float] = []
    measured = markers.detect_aruco_markers(normalized)
    if measured:
        errors.append(geometry.alignment_errors(measured, known)["max"])
    if len(page.qr_quad) == 4:
        located = markers.detect_qr_located(normalized)
        if located is not None and located[1] is not None and len(located[1]) == 4:
            errors.append(
                max(
                    ((mx - kx) ** 2 + (my - ky) ** 2) ** 0.5
                    for (mx, my), (kx, ky) in zip(located[1], page.qr_quad)
                )
            )
    return max(errors) if errors else None


def crop_regions(
    session_dir: Path,
    normalized_pages: list[NormalizedPage],
    template: Template,
    margin_px: int,
) -> list[CropInfo]:
    """各設問の領域を切り出して保存する（SPEC §9.3）。

    切り出し原点はクランプ後の実測値を ``CropInfo`` に記録する。ページ端では
    マージンが削られるため、``rect - margin`` からの再計算では位置がずれる。
    """
    by_page = {p.page_no: p for p in normalized_pages}
    crops_dir = session_dir / CROPS_DIR
    crops_dir.mkdir(parents=True, exist_ok=True)

    infos: list[CropInfo] = []
    for question in template.questions:
        for index, region in enumerate(question.regions):
            page = by_page.get(region.page_no)
            if page is None:
                logger.warning(
                    "設問 %s の領域 %d: ページ %d が投入されていません",
                    question.id,
                    index,
                    region.page_no,
                )
                continue
            page_info = template.page(region.page_no)
            if page_info is None:
                continue
            canvas_rect = geometry.norm_rect_to_canvas(
                geometry.make_norm_rect(*region.rect), page_info.canvas_w, page_info.canvas_h
            )
            origin, crop_w, crop_h = geometry.crop_bounds(
                canvas_rect, page_info.canvas_w, page_info.canvas_h, margin_px
            )
            ox, oy = int(origin[0]), int(origin[1])
            patch = page.image[oy : oy + crop_h, ox : ox + crop_w]
            info = CropInfo(
                question_id=question.id,
                index=index,
                page_no=region.page_no,
                region_rect=region.rect,
                crop_origin_canvas=(float(ox), float(oy)),
                crop_w=crop_w,
                crop_h=crop_h,
                margin_px=margin_px,
            )
            (crops_dir / info.filename).write_bytes(pdfutil.encode_png(patch))
            infos.append(info)
    return infos


def run_ocr(
    session_dir: Path, crops: list[CropInfo], template: Template, backend: OCRBackend
) -> list[str]:
    """各切り出し画像を OCR して CropInfo を更新する。戻り値は警告のリスト。"""
    warnings: list[str] = []
    crops_dir = session_dir / CROPS_DIR
    for crop in crops:
        question = template.question(crop.question_id)
        language = question.language if question else "ja"
        png = (crops_dir / crop.filename).read_bytes()
        try:
            result = backend.recognize(png, language)
        except OCRError as e:
            crop.ocr_error = str(e)
            warnings.append(f"{crop.question_id}: OCR に失敗しました（{e}）")
            logger.warning("OCR 失敗 %s: %s", crop.filename, e)
            continue
        crop.ocr_text = result.text
        crop.ocr_words = words_to_records(result.words)
    return warnings


def build_transcriptions(template: Template, crops: list[CropInfo]) -> list[QuestionTranscription]:
    """設問ごとに OCR テキストを記入順で連結する（SPEC §6.1 regions の順序）。"""
    transcriptions: list[QuestionTranscription] = []
    for question in template.questions:
        parts = [c.ocr_text for c in sorted(
            (c for c in crops if c.question_id == question.id), key=lambda c: c.index
        )]
        text = "\n".join(p for p in parts if p).strip()
        transcriptions.append(
            QuestionTranscription(
                question_id=question.id, transcription=text, is_blank=not text
            )
        )
    return transcriptions


def process_scan(
    settings: Settings,
    session_id: str,
    input_paths: list[Path],
    template: Template,
    candidates: list[Template],
    backend: OCRBackend,
) -> SessionState:
    """スキャン投入から OCR までを実行する（SPEC §9.1 の前半）。

    1 ページの位置合わせ失敗で全体を止めず、失敗ページは ``failed/`` に退避して継続する
    （SPEC §10.1）。
    """
    session_dir = settings.session_dir(session_id)
    (session_dir / NORMALIZED_DIR).mkdir(parents=True, exist_ok=True)

    session = SessionState(
        session_id=session_id,
        template_id=template.template_id,
        ocr_backend=backend.name,
        status="processing",
        progress="スキャン画像を読み込んでいます",
    )

    loaded_pages = load_input_pages(input_paths, settings.scan_dpi)
    if not loaded_pages:
        session.status = "failed"
        session.error = "処理できる入力ページがありませんでした。"
        return session

    normalized_pages: list[NormalizedPage] = []
    for order, loaded in enumerate(loaded_pages):
        try:
            page = normalize_page(loaded, candidates or [template], fallback_page_no=order + 1)
        except AlignmentError as e:
            failed_dir = session_dir / FAILED_DIR
            failed_dir.mkdir(parents=True, exist_ok=True)
            filename = f"page_{loaded.source_index + 1:02d}.png"
            (failed_dir / filename).write_bytes(pdfutil.encode_png(loaded.image))
            session.failed_pages.append(
                FailedPage(
                    source_index=loaded.source_index,
                    filename=filename,
                    reason=str(e),
                    detected_marker_ids=sorted(markers.detect_aruco_markers(loaded.image)),
                )
            )
            session.warnings.append(f"{loaded.origin} の {loaded.source_index + 1} ページ目: {e}")
            logger.warning("位置合わせ失敗 (source_index=%d): %s", loaded.source_index, e)
            continue
        normalized_pages.append(page)

    # QR の page_no で並べ替える（ページ順が乱れていてもよい。SPEC §9.2）
    normalized_pages.sort(key=lambda p: (p.template.template_id, p.page_no))

    target_pages = [p for p in normalized_pages if p.template.template_id == template.template_id]
    for other in (p for p in normalized_pages if p.template.template_id != template.template_id):
        session.warnings.append(
            f"{other.page_no} ページ目は別テンプレート（{other.template.template_id}）と"
            f"判定されたためこのセッションでは処理しません。"
        )

    for page in target_pages:
        filename = f"page_{page.page_no:02d}.png"
        (session_dir / NORMALIZED_DIR / filename).write_bytes(pdfutil.encode_png(page.image))
        session.pages.append(
            PageRecord(
                page_no=page.page_no,
                source_index=page.source_index,
                normalized_filename=filename,
                template_id=page.template.template_id,
                qr_detected=page.qr_detected,
                marker_ids=page.marker_ids,
                alignment_error_px=page.alignment_error_px,
            )
        )
        if not page.qr_detected:
            session.warnings.append(
                f"{page.page_no} ページ目: QR を読み取れませんでした。"
                f"テンプレートとページ番号を確認してください。"
            )

    if not target_pages:
        session.status = "failed"
        session.error = "位置合わせに成功したページがありませんでした。"
        return session

    session.progress = "領域を切り出しています"
    session.crops = crop_regions(session_dir, target_pages, template, settings.crop_margin_px)

    session.progress = "OCR で転記しています"
    session.warnings.extend(run_ocr(session_dir, session.crops, template, backend))
    session.transcriptions = build_transcriptions(template, session.crops)

    session.status = "ready"
    session.progress = "転記の確認を待っています"
    return session

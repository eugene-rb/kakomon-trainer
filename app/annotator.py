"""赤入れ PDF の生成（SPEC §5.5, §9.8）。

正規化済み画像を貼った **新規 PDF** として生成する。元スキャン PDF に描き込まない。
正規化画像はキャンバス座標系と 1:1 対応するため、アノテーション座標の変換が
線形スケールだけで済み、位置ズレのバグが原理的に発生しない。

フォントについて（実測で確認）:
    PyMuPDF の CJK 組み込みフォントは ``japan`` のみで、太字版（``japanB`` 等）は
    外部フォントファイルなしには使えない。SPEC §9.8 は得点表示を太字と指定しているが、
    得点は ``11 / 15`` のような ASCII のみなので、base-14 の Helvetica-Bold (``hebo``) を
    使うことで「太字」と「外部フォントに依存しない」を両立している。
    日本語コメントは ``japan``（丸囲み数字 ①②③ が描画できることを確認済み）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import pymupdf

from app import geometry, pdfutil
from app.config import Settings
from app.geometry import mm_to_pt
from app.models import CropInfo, Issue, Result, SessionState, Template
from app.ocr.base import Word, records_to_words
from app.textnorm import normalize_for_match

__all__ = ["build_graded_pdf", "normalize_for_match"]

logger = logging.getLogger(__name__)

#: SPEC §9.8: すべて赤 #D40000、線幅 1.5pt
RED = (0xD4 / 255, 0.0, 0.0)
LINE_WIDTH = 1.5

FONT_CJK = "japan"
FONT_BOLD_ASCII = "hebo"

SCORE_FONT_SIZE = 11
TOTAL_FONT_SIZE = 16
COMMENT_FONT_SIZE = 8.5
MARKER_FONT_SIZE = 9

#: SPEC §9.8: 右余白がこれ未満ならコメントを追加ページに回す
MIN_COMMENT_MARGIN_MM = 30.0

#: 引用の突き合わせで許す最大単語数
_MAX_QUOTE_WORDS = 40

_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def circled_number(n: int) -> str:
    """丸囲み番号。20 を超えたら ``(n)`` にフォールバックする。"""
    return _CIRCLED[n - 1] if 1 <= n <= len(_CIRCLED) else f"({n})"


# --------------------------------------------------------------------------
# quote → bbox の突き合わせ（SPEC §9.8 手順2）
# --------------------------------------------------------------------------


@dataclass
class WordRef:
    """OCR の単語 1 個と、それが属する切り出し画像の情報。"""

    word: Word
    crop: CropInfo

    @property
    def normalized(self) -> str:
        return normalize_for_match(self.word.text)


def collect_words(session: SessionState, question_id: str) -> list[WordRef]:
    """設問に属する単語を記入順（regions の順）で集める。"""
    refs: list[WordRef] = []
    for crop in session.crops_for(question_id):
        for word in records_to_words(crop.ocr_words):
            refs.append(WordRef(word=word, crop=crop))
    return refs


def find_quote_words(words: list[WordRef], quote: str) -> list[WordRef]:
    """``quote`` に一致する連続語列を探す（SPEC §9.8 手順2）。

    完全一致（連結して等しい）を優先し、見つからなければ「連結文字列が quote を含む
    最小の窓」を採用する。候補が複数ある場合は最初の 1 つ。
    見つからなければ空リスト（呼び出し側でフォールバック）。
    """
    target = normalize_for_match(quote)
    if not target or not words:
        return []

    normalized = [w.normalized for w in words]

    # 1. 連結して完全一致
    for i in range(len(words)):
        joined = ""
        for j in range(i, min(i + _MAX_QUOTE_WORDS, len(words))):
            joined += normalized[j]
            if joined == target:
                return words[i : j + 1]
            if len(joined) > len(target) * 2:
                break

    # 2. 連結が quote を含む最小の窓（OCR の分かち書きと引用の境界がずれる場合の救済）
    best: tuple[int, int] | None = None
    for i in range(len(words)):
        joined = ""
        for j in range(i, min(i + _MAX_QUOTE_WORDS, len(words))):
            joined += normalized[j]
            if target in joined:
                if best is None or (j - i) < (best[1] - best[0]):
                    best = (i, j)
                break
            if len(joined) > len(target) * 3:
                break
    return list(words[best[0] : best[1] + 1]) if best else []


def union_bbox_canvas(words: list[WordRef]) -> geometry.CanvasRectPx:
    """一致した語列の bbox を統合してキャンバス座標の矩形にする（SPEC §9.8 手順3, 5）。

    切り出し画像内座標 → キャンバス座標の変換には、``CropInfo`` に記録された
    **実測の切り出し原点** を使う。``rect - margin`` からの再計算はページ端で破綻する。
    """
    rects: list[tuple[float, float, float, float]] = []
    for ref in words:
        x0, y0, x1, y1 = ref.word.bbox
        crop_rect = geometry.make_crop_rect(int(x0), int(y0), max(int(x1), int(x0) + 1), max(int(y1), int(y0) + 1))
        origin = geometry.make_canvas_px(*ref.crop.crop_origin_canvas)
        rects.append(tuple(geometry.crop_rect_to_canvas(crop_rect, origin)))  # type: ignore[arg-type]
    return geometry.make_canvas_rect(
        min(r[0] for r in rects),
        min(r[1] for r in rects),
        max(r[2] for r in rects),
        max(r[3] for r in rects),
    )


# --------------------------------------------------------------------------
# PDF 生成
# --------------------------------------------------------------------------


#: 貼り込む答案画像の JPEG 品質。300dpi の答案画像を PNG のまま埋めると
#: 1 ページ 25MB を超えて実用にならないため、可逆である必要のない
#: 「スキャン画像の下地」だけ JPEG に再エンコードする（赤入れはベクタなので劣化しない）。
PAGE_IMAGE_JPEG_QUALITY = 85


def _page_image_stream(image_path: Path) -> bytes:
    """正規化済みページ画像を、PDF 埋め込み用に JPEG へ再エンコードする。"""
    image = pdfutil.decode_png(image_path.read_bytes())
    ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), PAGE_IMAGE_JPEG_QUALITY])
    if not ok:  # pragma: no cover - エンコード失敗時は元の PNG をそのまま使う
        return image_path.read_bytes()
    return buf.tobytes()


@dataclass
class _PageContext:
    page: pymupdf.Page
    page_no: int
    canvas_w: int
    canvas_h: int
    width_pt: float
    height_pt: float

    def canvas_to_pdf(self, rect: geometry.CanvasRectPx) -> pymupdf.Rect:
        x0, y0, x1, y1 = geometry.canvas_rect_to_pdf(
            rect, self.canvas_w, self.canvas_h, self.width_pt, self.height_pt
        )
        return pymupdf.Rect(x0, y0, x1, y1)


def _draw_underline(ctx: _PageContext, rect: pymupdf.Rect) -> None:
    shape = ctx.page.new_shape()
    y = rect.y1 + 1.5
    shape.draw_line(pymupdf.Point(rect.x0, y), pymupdf.Point(rect.x1, y))
    shape.finish(color=RED, width=LINE_WIDTH)
    shape.commit()


def _draw_box(ctx: _PageContext, rect: pymupdf.Rect) -> None:
    shape = ctx.page.new_shape()
    shape.draw_rect(rect)
    shape.finish(color=RED, width=LINE_WIDTH)
    shape.commit()


def _draw_marker_number(ctx: _PageContext, x: float, y: float, number: int) -> None:
    ctx.page.insert_text(
        pymupdf.Point(x, y), circled_number(number), fontname=FONT_CJK,
        fontsize=MARKER_FONT_SIZE, color=RED,
    )


def _right_margin_pt(template: Template, page_no: int, page_w_pt: float) -> float:
    """そのページの右余白幅（pt）。設問領域の右端から紙の右端まで。"""
    rights = [
        region.rect[2]
        for question in template.questions
        for region in question.regions
        if region.page_no == page_no
    ]
    if not rights:
        return page_w_pt * 0.15
    return page_w_pt * (1.0 - max(rights))


def build_graded_pdf(
    settings: Settings,
    session: SessionState,
    template: Template,
    result: Result,
    output_path: Path,
) -> Path:
    """赤入れ PDF を生成する（SPEC §5.5, §9.8）。"""
    session_dir = settings.session_dir(session.session_id)
    source_pdf = settings.template_dir(template.template_id) / template.source_pdf

    doc = pymupdf.open()
    contexts: dict[int, _PageContext] = {}

    for record in sorted(session.pages, key=lambda p: p.page_no):
        page_info = template.page(record.page_no)
        if page_info is None:
            continue
        try:
            width_pt, height_pt = pdfutil.page_size_pt(source_pdf, record.page_no)
        except Exception:
            # source.pdf が読めない場合はキャンバス比率から 300dpi 相当で復元する
            width_pt = geometry.px_to_pt(page_info.canvas_w, template.dpi)
            height_pt = geometry.px_to_pt(page_info.canvas_h, template.dpi)

        page = doc.new_page(width=width_pt, height=height_pt)
        image_path = session_dir / "normalized" / record.normalized_filename
        if image_path.exists():
            page.insert_image(
                pymupdf.Rect(0, 0, width_pt, height_pt),
                stream=_page_image_stream(image_path),
            )
        contexts[record.page_no] = _PageContext(
            page=page,
            page_no=record.page_no,
            canvas_w=page_info.canvas_w,
            canvas_h=page_info.canvas_h,
            width_pt=width_pt,
            height_pt=height_pt,
        )

    if not contexts:
        raise ValueError("正規化済みページがないため赤入れ PDF を生成できません。")

    # ページごとの通し番号とコメント
    counters: dict[int, int] = {no: 0 for no in contexts}
    comments: dict[int, list[tuple[int, Issue, str]]] = {no: [] for no in contexts}

    for graded in result.questions:
        question = template.question(graded.id)
        if question is None or not question.regions:
            continue
        primary = question.regions[0]
        ctx = contexts.get(primary.page_no)
        if ctx is None:
            continue

        region_rect = ctx.canvas_to_pdf(
            geometry.norm_rect_to_canvas(
                geometry.make_norm_rect(*primary.rect), ctx.canvas_w, ctx.canvas_h
            )
        )
        _draw_question_score(ctx, region_rect, graded.score, graded.max_score)

        words = collect_words(session, graded.id)
        #: 引用なし指摘が同一設問に複数付くとき（作図・グラフの採点で頻出）、
        #: 枠は 1 回だけ描き、番号は右へずらして重ならないようにする。
        region_boxed = False
        region_marker_seq = 0
        for issue in graded.issues:
            matched = find_quote_words(words, issue.quote) if issue.quote else []
            target_page_no = matched[0].crop.page_no if matched else primary.page_no
            target_ctx = contexts.get(target_page_no, ctx)
            counters[target_ctx.page_no] += 1
            number = counters[target_ctx.page_no]

            if matched:
                rect = target_ctx.canvas_to_pdf(union_bbox_canvas(matched))
                _draw_underline(target_ctx, rect)
                _draw_marker_number(target_ctx, rect.x0, rect.y0 - 2, number)
            else:
                # SPEC §9.8 手順4: 設問領域を赤枠で囲み、枠の左上に番号
                if not region_boxed:
                    _draw_box(target_ctx, region_rect)
                    region_boxed = True
                _draw_marker_number(
                    target_ctx,
                    region_rect.x0 + 2 + region_marker_seq * (MARKER_FONT_SIZE + 2),
                    region_rect.y0 - 2,
                    number,
                )
                region_marker_seq += 1
            comments[target_ctx.page_no].append((number, issue, graded.id))

    _draw_total_score(contexts, result)
    _draw_comments(doc, contexts, comments, template)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    doc.close()
    return output_path


def _draw_question_score(ctx: _PageContext, region: pymupdf.Rect, score: int | None, max_score: int) -> None:
    """設問領域の右上に ``11 / 15`` を表示する（SPEC §9.8）。"""
    text = f"{score if score is not None else '—'} / {max_score}"
    width = pymupdf.get_text_length(text, fontname=FONT_BOLD_ASCII, fontsize=SCORE_FONT_SIZE)
    x = min(region.x1 - width, ctx.width_pt - width - 4)
    y = max(region.y0 - 3, SCORE_FONT_SIZE)
    ctx.page.insert_text(
        pymupdf.Point(x, y), text, fontname=FONT_BOLD_ASCII, fontsize=SCORE_FONT_SIZE, color=RED
    )


def _draw_total_score(contexts: dict[int, _PageContext], result: Result) -> None:
    """1 ページ目の上部余白に総得点を表示する（SPEC §9.8）。"""
    first = contexts[min(contexts)]
    text = f"{result.total_score} / {result.total_max_score}"
    width = pymupdf.get_text_length(text, fontname=FONT_BOLD_ASCII, fontsize=TOTAL_FONT_SIZE)
    first.page.insert_text(
        pymupdf.Point((first.width_pt - width) / 2, mm_to_pt(6.5)),
        text,
        fontname=FONT_BOLD_ASCII,
        fontsize=TOTAL_FONT_SIZE,
        color=RED,
    )


def _draw_comments(
    doc: pymupdf.Document,
    contexts: dict[int, _PageContext],
    comments: dict[int, list[tuple[int, Issue, str]]],
    template: Template,
) -> None:
    """コメントを右余白に列挙し、余白が足りなければ追加ページに回す（SPEC §9.8）。"""
    overflow: list[tuple[int, int, Issue, str]] = []

    for page_no, entries in comments.items():
        if not entries:
            continue
        ctx = contexts[page_no]
        margin_pt = _right_margin_pt(template, page_no, ctx.width_pt)
        if margin_pt < mm_to_pt(MIN_COMMENT_MARGIN_MM):
            overflow.extend((page_no, n, issue, qid) for n, issue, qid in entries)
            continue

        box = pymupdf.Rect(
            ctx.width_pt - margin_pt + 2, mm_to_pt(14), ctx.width_pt - 2, ctx.height_pt - mm_to_pt(8)
        )
        text = "\n".join(_format_comment(n, issue) for n, issue, _ in entries)
        leftover = ctx.page.insert_textbox(
            box, text, fontname=FONT_CJK, fontsize=COMMENT_FONT_SIZE, color=RED, align=0
        )
        if leftover < 0:
            # 収まりきらなかった分は追加ページへ
            overflow.extend((page_no, n, issue, qid) for n, issue, qid in entries)

    if not overflow:
        return

    first = contexts[min(contexts)]
    page = doc.new_page(width=first.width_pt, height=first.height_pt)
    page.insert_text(
        pymupdf.Point(mm_to_pt(15), mm_to_pt(15)), "指摘一覧", fontname=FONT_CJK, fontsize=13, color=RED
    )
    lines = [
        f"[p.{page_no} {qid}] {_format_comment(number, issue)}"
        for page_no, number, issue, qid in overflow
    ]
    page.insert_textbox(
        pymupdf.Rect(mm_to_pt(15), mm_to_pt(20), first.width_pt - mm_to_pt(15), first.height_pt - mm_to_pt(15)),
        "\n".join(lines),
        fontname=FONT_CJK,
        fontsize=9.5,
        color=RED,
    )


def _format_comment(number: int, issue: Issue) -> str:
    deduction = f"（-{issue.deduction}）" if issue.deduction else ""
    return f"{circled_number(number)} {issue.tag}{deduction} {issue.comment}"

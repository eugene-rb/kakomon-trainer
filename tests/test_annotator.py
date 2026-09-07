"""quote → bbox の突き合わせと赤入れ PDF 生成のテスト（SPEC §12）。"""

from __future__ import annotations

import pymupdf
import pytest

from app.annotator import (
    build_graded_pdf,
    circled_number,
    collect_words,
    find_quote_words,
    normalize_for_match,
    union_bbox_canvas,
)
from app.models import (
    CropInfo,
    Issue,
    OCRWordRecord,
    PageRecord,
    QuestionTranscription,
    Result,
    ResultQuestion,
    SessionState,
)
from app.ocr.base import Word
from app.storage import save_session


def _words(*texts: str) -> list:
    """等間隔に並んだ単語列を作る。"""
    crop = CropInfo(
        question_id="1-(1)",
        index=0,
        page_no=1,
        region_rect=(0.12, 0.17, 0.88, 0.31),
        crop_origin_canvas=(100.0, 200.0),
        crop_w=800,
        crop_h=300,
        margin_px=8,
        ocr_words=[
            OCRWordRecord(text=text, bbox=(i * 60, 20, i * 60 + 50, 50))
            for i, text in enumerate(texts)
        ],
    )
    from app.annotator import WordRef
    from app.ocr.base import records_to_words

    return [WordRef(word=w, crop=crop) for w in records_to_words(crop.ocr_words)]


def test_normalize_for_match():
    assert normalize_for_match("  If I HAD known,  ") == "ifihadknown"
    assert normalize_for_match("Ｉ　ｗｏｕｌｄ") == "iwould"  # 全角 → 半角
    assert normalize_for_match("「テスト。」") == "テスト"


def test_find_quote_exact_match():
    words = _words("If", "I", "had", "known", "the", "fact")
    matched = find_quote_words(words, "had known")
    assert [w.word.text for w in matched] == ["had", "known"]


def test_find_quote_ignores_case_and_punctuation():
    words = _words("I", "would", "tell", "him")
    matched = find_quote_words(words, "  WOULD TELL,  ")
    assert [w.word.text for w in matched] == ["would", "tell"]


def test_find_quote_partial_word_boundary():
    """OCR の分かち書きと引用の境界がずれても最小の窓で拾えること。"""
    words = _words("仮定法", "過去完了", "の", "帰結節")
    matched = find_quote_words(words, "過去完了の")
    assert [w.word.text for w in matched] == ["過去完了", "の"]


def test_find_quote_returns_empty_when_absent():
    words = _words("If", "I", "had", "known")
    assert find_quote_words(words, "completely different") == []


def test_find_quote_empty_quote_returns_empty():
    assert find_quote_words(_words("a", "b"), "") == []


def test_union_bbox_uses_recorded_crop_origin():
    """切り出し原点は CropInfo の実測値を使う（rect-margin の再計算ではない）。"""
    words = _words("If", "I", "had")
    rect = union_bbox_canvas(words[:2])
    # bbox は (0,20,50,50) と (60,20,110,50)、原点 (100,200) を足した値になる
    assert rect == (100.0, 220.0, 210.0, 250.0)


def test_circled_number():
    assert circled_number(1) == "①"
    assert circled_number(20) == "⑳"
    assert circled_number(21) == "(21)"


def test_build_graded_pdf_end_to_end(settings, built_template, tmp_path):
    """赤入れ PDF が生成でき、ページサイズが source.pdf に一致すること。"""
    session_dir = settings.session_dir("test-session")
    (session_dir / "normalized").mkdir(parents=True, exist_ok=True)

    page = built_template.pages[0]
    blank = settings.template_dir(built_template.template_id) / "blank.pdf"
    from app import pdfutil

    image = pdfutil.pdf_page_arrays(blank, 300)[0]
    (session_dir / "normalized" / "page_01.png").write_bytes(pdfutil.encode_png(image))

    crop = CropInfo(
        question_id="1-(1)",
        index=0,
        page_no=1,
        region_rect=(0.12, 0.17, 0.88, 0.31),
        crop_origin_canvas=(290.0, 590.0),
        crop_w=1900,
        crop_h=500,
        margin_px=8,
        ocr_text="If I had known the fact, I would tell him about it.",
        ocr_words=[
            OCRWordRecord(text=text, bbox=(i * 120, 40, i * 120 + 100, 90))
            for i, text in enumerate(
                ["If", "I", "had", "known", "the", "fact", "I", "would", "tell", "him"]
            )
        ],
    )
    session = SessionState(
        session_id="test-session",
        template_id=built_template.template_id,
        status="ready",
        pages=[
            PageRecord(
                page_no=1,
                source_index=0,
                normalized_filename="page_01.png",
                template_id=built_template.template_id,
                qr_detected=True,
            )
        ],
        crops=[crop],
        transcriptions=[QuestionTranscription(question_id="1-(1)", transcription=crop.ocr_text)],
    )
    save_session(settings, session)

    result = Result(
        session_id="test-session",
        template_id=built_template.template_id,
        model="test-model",
        total_score=11,
        total_max_score=15,
        questions=[
            ResultQuestion(
                id="1-(1)",
                max_score=15,
                score=11,
                transcription=crop.ocr_text,
                feedback="仮定法過去完了の帰結節が誤り。",
                issues=[
                    Issue(
                        quote="I would tell",
                        kind="文法",
                        tag="時制・仮定法",
                        comment="帰結節も would have told とする",
                        deduction=4,
                    ),
                    # quote が空 → 設問領域へのフォールバック
                    Issue(quote="", kind="内容", tag="訳出漏れ", comment="条件節の訳出漏れ", deduction=0),
                ],
            )
        ],
    )

    output = tmp_path / "graded.pdf"
    build_graded_pdf(settings, session, built_template, result, output)

    assert output.exists()
    with pymupdf.open(str(output)) as doc:
        assert doc.page_count >= 1
        assert doc[0].rect.width == pytest.approx(595.0, abs=1.0)
        assert doc[0].rect.height == pytest.approx(842.0, abs=1.0)
        text = doc[0].get_text()
        assert "11 / 15" in text  # 設問得点
        assert "①" in text  # 丸囲み番号


def test_build_graded_pdf_multiple_quoteless_issues(settings, built_template, tmp_path):
    """作図・グラフの採点で1設問に引用なし指摘が複数付いても、枠1回＋番号を並べて描ける。"""
    session_dir = settings.session_dir("s-graph")
    (session_dir / "normalized").mkdir(parents=True, exist_ok=True)

    from app import pdfutil

    blank = settings.template_dir(built_template.template_id) / "blank.pdf"
    image = pdfutil.pdf_page_arrays(blank, 300)[0]
    (session_dir / "normalized" / "page_01.png").write_bytes(pdfutil.encode_png(image))

    built_template.questions[0].answer_format = "graph"
    session = SessionState(
        session_id="s-graph",
        template_id=built_template.template_id,
        status="ready",
        pages=[
            PageRecord(
                page_no=1, source_index=0, normalized_filename="page_01.png",
                template_id=built_template.template_id, qr_detected=True,
            )
        ],
    )
    save_session(settings, session)

    result = Result(
        session_id="s-graph",
        template_id=built_template.template_id,
        total_score=6,
        total_max_score=15,
        questions=[
            ResultQuestion(
                id="1-(1)", max_score=15, score=6, feedback="作図に不備。",
                issues=[
                    Issue(quote="", kind="形式", tag="図示の誤り", comment="軸ラベルがない", deduction=3),
                    Issue(quote="", kind="形式", tag="図示の誤り", comment="漸近線の位置がずれている", deduction=3),
                    Issue(quote="", kind="形式", tag="図示の誤り", comment="極値の x 座標が違う", deduction=3),
                ],
            )
        ],
    )

    output = tmp_path / "graded.pdf"
    build_graded_pdf(settings, session, built_template, result, output)

    with pymupdf.open(str(output)) as doc:
        text = "".join(page.get_text() for page in doc)
        assert "①" in text and "②" in text and "③" in text
        assert "軸ラベルがない" in text and "漸近線" in text and "極値" in text


def test_collect_words_orders_by_region_index():
    session = SessionState(session_id="s", template_id="t")
    for index, text in enumerate(["second", "first"]):
        session.crops.append(
            CropInfo(
                question_id="q",
                index=1 - index,
                page_no=1,
                region_rect=(0.1, 0.1, 0.9, 0.2),
                crop_origin_canvas=(0.0, 0.0),
                crop_w=100,
                crop_h=50,
                margin_px=8,
                ocr_words=[OCRWordRecord(text=text, bbox=(0, 0, 10, 10))],
            )
        )
    assert [w.word.text for w in collect_words(session, "q")] == ["first", "second"]

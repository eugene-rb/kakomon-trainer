import pytest

from app.registration import plan_registration


def test_pairs_multiple_sheets_without_crossing_subjects():
    groups = plan_registration([
        "kyodai_english_2024_answers.pdf", "kyodai_math_2024_sheet.pdf",
        "kyodai_english_2024_sheet.pdf", "kyodai_english_2024_rubric.md",
        "kyodai_math_2024_answers.txt",
    ])
    english = next(group for group in groups if group.title == "kyodai_english_2024")
    assert english.sheet == "kyodai_english_2024_sheet.pdf"
    assert english.references == ["kyodai_english_2024_answers.pdf", "kyodai_english_2024_rubric.md"]
    math = next(group for group in groups if group.title == "kyodai_math_2024")
    assert math.references == ["kyodai_math_2024_answers.txt"]


def test_case_insensitive_matching_and_standalone_pdf():
    groups = plan_registration(["Exam_SHEET.PDF", "exam_ANSWERS.pdf", "standalone.pdf"])
    assert groups[0].references == ["exam_ANSWERS.pdf"]
    assert groups[1].sheet == "standalone.pdf"
    assert groups[1].references == []


@pytest.mark.parametrize("names", [
    ["exam_answers.pdf"],
    ["exam_sheet.pdf", "other_answers.pdf"],
    ["exam_sheet.pdf", "exam.pdf"],
    ["exam_sheet.pdf", "EXAM_SHEET.PDF"],
    ["exam_sheet.pdf", "../exam_answers.pdf"],
    ["ｅｘａｍ_sheet.pdf"],
    ["京大_英語_2024_sheet.pdf"],
    ["exam sheet.pdf"],
    ["exam_sheet.png"],
    ["exam_rubric.exe"],
    ["exam-a_sheet.pdf", "exam_a_answers.pdf"],
])
def test_ambiguous_or_invalid_names_are_rejected(names):
    with pytest.raises(ValueError):
        plan_registration(names)

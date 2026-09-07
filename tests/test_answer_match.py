"""答えのみ設問の決定的照合のテスト（純関数、ネットワーク不要）。"""

from __future__ import annotations

import pytest

from app.answer_match import judge, parse_number, split_keys


# --- 数値パーサ ---


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("42", 42.0),
        ("42.0", 42.0),
        ("  3.0 ", 3.0),
        ("-5", -5.0),
        ("−5", -5.0),  # U+2212 マイナス
        ("‐5", -5.0),  # U+2010 ハイフン
        (".5", 0.5),
        ("1/2", 0.5),
        ("3/4", 0.75),
        ("1,234", 1234.0),
        ("25%", 0.25),
        ("1.2e3", 1200.0),
        ("1.2×10^3", 1200.0),
        ("1.2x10^3", 1200.0),
        ("1.2×10³", 1200.0),  # 上付き指数
        ("6.0×10⁻²³", 6.0e-23),
        ("10^3", 1000.0),
        ("12 m/s", 12.0),  # 末尾単位
        ("3.0 mol", 3.0),
        ("98J", 98.0),
        ("１２", 12.0),  # 全角数字
    ],
)
def test_parse_number(text, expected):
    got = parse_number(text)
    assert got is not None
    assert got == pytest.approx(expected)


@pytest.mark.parametrize("text", ["", "光合成", "abc", "ア", "H2O"])
def test_parse_number_non_numeric(text):
    assert parse_number(text) is None


# --- キー分割 ---


def test_split_keys():
    assert split_keys("42") == ["42"]
    assert split_keys("光合成\n光合成する") == ["光合成", "光合成する"]
    assert split_keys("a | b|c") == ["a", "b", "c"]
    assert split_keys("1/2") == ["1/2"]  # スラッシュは区切りにしない（分数と衝突するため）
    assert split_keys("  \n  ") == []


# --- judge: 数値パス（correct / incorrect のみ、uncertain を返さない）---


def test_judge_numeric_exact():
    assert judge("42", "42.0", tolerance=0.0).verdict == "correct"


def test_judge_numeric_mismatch_is_incorrect_not_uncertain():
    assert judge("42", "41", tolerance=0.0).verdict == "incorrect"


def test_judge_numeric_within_tolerance():
    assert judge("9.8", "9.81", tolerance=0.05).verdict == "correct"
    assert judge("9.8", "9.9", tolerance=0.05).verdict == "incorrect"


def test_judge_numeric_fraction_vs_decimal():
    assert judge("1/2", "0.5", tolerance=0.0).verdict == "correct"


def test_judge_multiple_numeric_keys():
    assert judge("3\n-3", "-3", tolerance=0.0).verdict == "correct"


def test_judge_same_unit_value_mismatch_is_incorrect():
    assert judge("12 m/s", "15 m/s", tolerance=0.0).verdict == "incorrect"


def test_judge_same_unit_match_is_correct():
    assert judge("3.0 mol", "3 mol", tolerance=0.0).verdict == "correct"


def test_judge_algebraic_near_miss_is_not_a_false_positive():
    # "2x+1" と "2x+5" は先頭数値だけ見ると両方 2.0。数値パスで correct にしてはいけない。
    assert judge("2x+1", "2x+5", tolerance=0.0).verdict != "correct"


def test_judge_unit_mismatch_is_not_correct():
    assert judge("12 m", "12 kg", tolerance=0.0).verdict != "correct"


def test_judge_bare_number_vs_number_with_unit_is_not_auto_correct():
    # 正答が "12"（単位なし）で答案が "12 m/s" は、単位が食い違うので自動正解にしない
    assert judge("12", "12 m/s", tolerance=0.0).verdict != "correct"


# --- judge: 文字列パス ---


def test_judge_string_exact_after_normalization():
    assert judge("光合成", " 光合成。 ", tolerance=0.0).verdict == "correct"
    assert judge("ＣＯ", "co", tolerance=0.0).verdict == "correct"  # 全半角


def test_judge_string_near_miss_is_uncertain():
    result = judge("光合成", "光合成をする", tolerance=0.0)
    assert result.verdict == "uncertain"
    assert result.expected == "光合成"


def test_judge_string_clearly_different_is_incorrect():
    assert judge("東京", "大阪", tolerance=0.0).verdict == "incorrect"


def test_judge_no_answer_key_is_uncertain():
    assert judge("", "42", tolerance=0.0).verdict == "uncertain"


def test_judge_blank_answer_is_incorrect():
    assert judge("42", "   ", tolerance=0.0).verdict == "incorrect"

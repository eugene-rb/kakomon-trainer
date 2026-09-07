"""答えのみ設問の決定的照合（模範解答 ↔ 答案）。

LLM を使わずに ○×を判定できるケースをここで確定させる。判定できない文字列ケースだけ
``verdict="uncertain"`` を返し、呼び出し側（``app.grader``）が Haiku フォールバックに回す。

- **数値パス**は絶対に uncertain を返さない（許容誤差を決めた時点で ○×は決まる）。
- **文字列パス**は正規化一致なら correct、そうでなければ ``allow_fallback`` に応じて
  uncertain（フォールバック有効）または incorrect（無効）。
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from app.textnorm import normalize_for_match

Verdict = Literal["correct", "incorrect", "uncertain"]

#: 浮動小数点誤差を吸収する最小許容差
_EPSILON = 1e-9

#: 文字列がこの類似度以上なら「明確な誤答」ではなく uncertain（人手 or フォールバック行き）
_SIMILAR_RATIO = 0.6

#: 上付き数字・符号 → 通常文字
_SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻",
                             "0123456789+-")
#: 各種マイナス様記号 → ASCII ハイフンマイナス（NFKC は U+2212 等を変換しない）
_MINUS = str.maketrans("−‐‑‒–—―", "-------")

#: 複数正答の区切り。改行とパイプのみ（"/" は分数・単位と衝突するため使わない）
_KEY_SEP = re.compile(r"[\n\r|｜]+")

_SUPERSCRIPT_RUN = re.compile(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+")
#: a×10^b / a×10b / ×10^b （× は x * ・ ･ も可、^ は省略可、指数は必須）
_SCI_MUL = re.compile(r"^([+\-]?(?:\d+\.?\d*|\.\d+))?\s*[×xX*・･]\s*10\s*\^?\s*([+\-]?\d+)$")
#: 10^b 単体
_SCI_POW = re.compile(r"^10\s*\^\s*([+\-]?\d+)$")
#: 先頭の数値（末尾に単位が付いた "12m/s" などから数値部を取る）
_LEADING = re.compile(r"^[+\-]?(?:\d+\.?\d*|\.\d+)")


@dataclass
class MatchResult:
    """1 設問分の照合結果。"""

    verdict: Verdict
    #: 表示用の正答（先頭キー）
    expected: str
    #: 前後空白を除いた答案
    given: str
    #: 誤答コメント・警告文の材料
    detail: str = ""


def split_keys(answer_key: str) -> list[str]:
    """模範解答文字列を個別の正答候補に分割する。"""
    return [k.strip() for k in _KEY_SEP.split(answer_key or "") if k.strip()]


def _leading_full(s: str) -> tuple[float, str] | None:
    """先頭の数値と、その後ろに残った文字列（単位など）を返す。"""
    m = _LEADING.match(s)
    if not m:
        return None
    try:
        return float(m.group(0)), s[m.end():]
    except ValueError:  # pragma: no cover - 正規表現が保証する
        return None


def _parse_core(s: str) -> tuple[float, str] | None:
    """正規化済み文字列を ``(値, 残り)`` に分解する。残りは単位や余分な字句。"""
    m = _SCI_MUL.match(s)
    if m:
        try:
            return float(m.group(1) or "1") * (10.0 ** int(m.group(2))), ""
        except ValueError:  # pragma: no cover
            return None
    m = _SCI_POW.match(s)
    if m:
        return 10.0 ** int(m.group(1)), ""
    try:
        return float(s), ""  # 1.2e3 / -5 / .5 / 3.0 を処理（残りなし）
    except ValueError:
        pass
    if s.count("/") == 1:
        a, b = s.split("/")
        pa, pb = _leading_full(a), _leading_full(b)
        # 純粋な分数（両辺とも数値のみ、分母 0 でない）だけを分数として扱う
        if pa and pb and pa[1] == "" and pb[1] == "" and pb[0] != 0:
            return pa[0] / pb[0], ""
    return _leading_full(s)


def _parse_measure(text: str) -> tuple[float, str] | None:
    """``(数値, 残り（単位など。正規化済み）)`` を返す。数値と解釈できなければ None。"""
    # 上付き指数は NFKC が潰してしまうため、先に "^N" へ開く
    s = _SUPERSCRIPT_RUN.sub(lambda m: "^" + m.group(0).translate(_SUPERSCRIPT), text)
    s = unicodedata.normalize("NFKC", s).translate(_MINUS)
    s = s.strip().lower().replace(",", "").replace(" ", "")
    if not s:
        return None

    percent = s.endswith("%")
    if percent:
        s = s[:-1]

    parsed = _parse_core(s)
    if parsed is None:
        return None
    value, remainder = parsed
    return (value / 100.0 if percent else value), remainder


def parse_number(text: str) -> float | None:
    """数値として解釈できれば float を、できなければ None を返す。

    対応: 整数・小数・負数・分数(a/b)・指数(1.2e3 / 1.2×10^3 / 上付き 1.2×10³)・
    パーセント(25% → 0.25)・末尾単位付き(12 m/s → 12)。
    """
    parsed = _parse_measure(text)
    return parsed[0] if parsed is not None else None


def judge(answer_key: str, given: str, *, tolerance: float) -> MatchResult:
    """答案を模範解答と照合する。

    Args:
        tolerance: 数値解答の絶対許容誤差（呼び出し側で設問値と設定既定を解決済み）。

    数値パスは、末尾の残り（単位・字句）が両辺で一致するときだけ働く。
    一致すれば許容誤差で ``correct`` / ``incorrect`` を決める。残りが食い違う場合
    （"2x+1" vs "2x+5"、"12 m" vs "12 kg" 等）は文字列パスに委ねる。
    文字列パスは、完全一致=correct、明確に別物=incorrect、
    近いが一致しない（誤字・助詞の付加・部分一致など）=uncertain を返す。
    ``uncertain`` の扱い（フォールバック照合 or 人手確認）は呼び出し側が決める。
    """
    keys = split_keys(answer_key)
    expected = keys[0] if keys else ""
    given = given.strip()

    if not given:
        return MatchResult("incorrect", expected, given, "無解答")
    if not keys:
        return MatchResult("uncertain", "", given, "正答が設定されていません")

    given_m = _parse_measure(given)
    key_ms = [_parse_measure(k) for k in keys]
    if given_m is not None and all(m is not None for m in key_ms):
        given_val, given_unit = given_m
        tol = max(tolerance, _EPSILON)
        same_unit_seen = False
        for key, key_m in zip(keys, key_ms):
            assert key_m is not None
            key_val, key_unit = key_m
            if key_unit != given_unit:
                continue  # 単位・字句が違う → 数値パスでは判定しない
            same_unit_seen = True
            if abs(given_val - key_val) <= tol:
                return MatchResult("correct", key, given, "")
        if same_unit_seen:
            # 単位が一致するキーがあるのに値が合わない → 明確な誤答
            return MatchResult("incorrect", expected, given, f"正答 {expected} と数値が一致しません")
        # 単位が食い違うだけ → 文字列パスへ落とす

    norm_given = normalize_for_match(given)
    best_ratio = 0.0
    for key in keys:
        norm_key = normalize_for_match(key)
        if not norm_key or not norm_given:
            continue
        if norm_given == norm_key:
            return MatchResult("correct", key, given, "")
        if norm_key in norm_given or norm_given in norm_key:
            best_ratio = max(best_ratio, 0.9)
        best_ratio = max(best_ratio, difflib.SequenceMatcher(None, norm_given, norm_key).ratio())

    if best_ratio >= _SIMILAR_RATIO:
        return MatchResult("uncertain", expected, given, "決定的照合では判定できませんでした")
    return MatchResult("incorrect", expected, given, f"正答 {expected} と一致しません")

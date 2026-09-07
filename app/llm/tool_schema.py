"""採点ツール ``submit_grade`` の定義（SPEC §9.5）。

タグ・種別の enum は ``app.models`` の ``ISSUE_TAGS`` / ``ISSUE_KINDS`` から生成する。
片方だけ更新して Pydantic の型とツール定義がずれる事故を防ぐため、情報源は 1 か所に保つ。
"""

from __future__ import annotations

from typing import Any

from app.models import ISSUE_KINDS, ISSUE_TAGS

TOOL_NAME = "submit_grade"

#: SPEC §9.5 のスキーマ。``additionalProperties: false`` はスキーマ外の項目を
#: 返させないための追加（仕様書のスキーマと意味的に等価）。
GRADE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "description": "獲得点数"},
        "feedback": {"type": "string", "description": "総評。2〜3文"},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "quote": {
                        "type": "string",
                        "description": (
                            "答案本文からの原文ママの引用。誤りの該当箇所のみを最小限で抜き出す。"
                            "改変・要約禁止。該当箇所が存在しない指摘（訳出漏れ等）の場合は空文字列。"
                        ),
                    },
                    "kind": {"type": "string", "enum": list(ISSUE_KINDS)},
                    "tag": {"type": "string", "enum": list(ISSUE_TAGS)},
                    "comment": {
                        "type": "string",
                        "description": "何が誤りで、どう直すべきか。1〜2文",
                    },
                    "deduction": {"type": "integer", "description": "この指摘による減点"},
                },
                "required": ["quote", "kind", "tag", "comment", "deduction"],
                "additionalProperties": False,
            },
        },
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["score", "feedback", "issues", "confidence"],
    "additionalProperties": False,
}

TOOL_DESCRIPTION = "採点結果を提出する"


def anthropic_tool() -> dict[str, Any]:
    """Anthropic Messages API 用のツール定義（``input_schema`` キー）。"""
    return {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "input_schema": GRADE_INPUT_SCHEMA,
    }


def anthropic_tool_choice() -> dict[str, Any]:
    """Anthropic でツール呼び出しを強制する指定。"""
    return {"type": "tool", "name": TOOL_NAME}


def openai_tool() -> dict[str, Any]:
    """OpenAI 互換 chat completions 用のツール定義（``parameters`` キー）。"""
    return {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": TOOL_DESCRIPTION,
            "parameters": GRADE_INPUT_SCHEMA,
        },
    }


def openai_tool_choice() -> dict[str, Any]:
    """OpenAI 互換でツール呼び出しを強制する指定。"""
    return {"type": "function", "function": {"name": TOOL_NAME}}


# --------------------------------------------------------------------------
# 答えのみ設問のフォールバック照合ツール（app/llm/answer_check.py）
#
# 決定的照合で白黒つかない文字列ケースだけを Haiku に投げる。減点積み上げも
# タグ付けも不要で、「実質同じ答えか」の二値と短い理由だけを返させる。
# --------------------------------------------------------------------------

ANSWER_CHECK_TOOL_NAME = "submit_answer_check"

ANSWER_CHECK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "correct": {
            "type": "boolean",
            "description": "答案が模範解答と実質的に同じ（表記ゆれ・同義を含む）なら true",
        },
        "note": {"type": "string", "description": "判定の根拠。1文"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["correct", "note", "confidence"],
    "additionalProperties": False,
}

ANSWER_CHECK_TOOL_DESCRIPTION = "答えのみ設問の正誤判定を提出する"


def anthropic_answer_check_tool() -> dict[str, Any]:
    return {
        "name": ANSWER_CHECK_TOOL_NAME,
        "description": ANSWER_CHECK_TOOL_DESCRIPTION,
        "input_schema": ANSWER_CHECK_SCHEMA,
    }


def anthropic_answer_check_tool_choice() -> dict[str, Any]:
    return {"type": "tool", "name": ANSWER_CHECK_TOOL_NAME}

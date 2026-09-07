"""LLM 採点バックエンドの抽象基底。

SPEC §3 / §9.5 は Anthropic SDK 固定だが、複数プロバイダ（anthropic / openai / kimi など）に
対応するため、OCR バックエンド（SPEC §8）と同じパターンで抽象化している。
採点ツール定義（``submit_grade``）はプロバイダ非依存の 1 つを共有し、
各バックエンドがプロバイダ固有の「強制ツール呼び出し」形式へ変換する。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.refs import RefFile


class LLMError(RuntimeError):
    """LLM 呼び出しの失敗。設問単位のリトライ対象（SPEC §10.3）。"""


class LLMResponseError(LLMError):
    """レスポンスがツール呼び出しを含まない等、想定した形で返ってこなかった。"""


@dataclass
class GradingRequest:
    """1 設問分の採点リクエスト（プロバイダ非依存）。

    バックエンドはこれを各社の wire フォーマットに変換する。
    ``refs`` の PDF の扱いだけがプロバイダごとに異なる（§ 各バックエンドの docstring 参照）。
    """

    system_prompt: str
    #: 参照資料（模範解答・採点基準）。SPEC §9.5 の 1.
    refs: list[RefFile] = field(default_factory=list)
    #: 切り出し画像の PNG バイト列。SPEC §9.5 の 2.
    crop_images: list[bytes] = field(default_factory=list)
    #: 設問情報と転記テキスト。SPEC §9.5 の 3.
    question_text: str = ""


@dataclass
class GradeResult:
    """``submit_grade`` ツールの入力として返ってきた採点結果（未検証の生データ）。"""

    score: int
    feedback: str
    issues: list[dict]
    confidence: str
    #: デバッグ・ログ用の生の入力
    raw: dict = field(default_factory=dict)


class LLMGradingBackend(ABC):
    """LLM 採点バックエンドの共通インターフェース。"""

    #: プロバイダ名（result.json / ログに記録する）
    provider: str = "unknown"
    #: 使用モデル名
    model: str = ""

    @abstractmethod
    def grade(self, request: GradingRequest) -> GradeResult:
        """設問 1 問を採点する。SPEC §9.5: 設問ごとに 1 リクエスト。"""
        ...

"""全データ構造の集約定義（SPEC §6）。

このモジュールは「保存形式」を定義する層である。座標値は仕様書の JSON 形式に
厳密一致させるため素の tuple / float のまま保持し、座標系の型（NormRect など）
への変換は呼び出し側が ``geometry`` の各ファクトリを通して明示的に行う。
そうすることで ``models`` と ``geometry`` の相互 import を構造的に排除している。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Literal, get_args

from pydantic import BaseModel, Field, field_validator

# --------------------------------------------------------------------------
# 共通
# --------------------------------------------------------------------------

JST = timezone(timedelta(hours=9))

#: v2: blank.pdf レイアウト変更（ArUco 3 個 + 右下 QR、本文を若干縮小）。
#: v1 の template.json は物理的に別レイアウトの用紙を指すため読み込みを拒否する。
SCHEMA_VERSION = 2

#: ファイル名に使えない文字（SPEC §6.1: questions[].id の制約）
_FORBIDDEN_ID_CHARS = re.compile(r'[/\\:*?"<>|]')


def now_jst() -> datetime:
    """現在時刻を JST の aware datetime で返す（ログ・ファイル名の時刻源はここに統一）。"""
    return datetime.now(JST)


# --------------------------------------------------------------------------
# 誤りタグ / 種別（SPEC §6.3）
#
# ここが唯一の情報源。Pydantic モデルの型と、LLM に渡すツール定義
# (app/llm/tool_schema.py) の enum の両方がここを参照するため、
# 片方だけ更新して不整合が起きることがない。
# --------------------------------------------------------------------------

IssueTag = Literal[
    "語彙・語法",
    "時制・仮定法",
    "態",
    "準動詞",
    "関係詞",
    "接続・構文",
    "冠詞・数",
    "前置詞",
    "語順",
    "スペル",
    "内容理解の誤り",
    "訳出漏れ",
    "日本語表現",
    "設問要求の取り違え",
    "論理展開",
    "誤答",
    "図示の誤り",
    "その他",
]

IssueKind = Literal["文法", "語彙", "内容", "形式", "その他"]

#: 採点形式。設問タイプごとに採点経路と観点（app/prompts/criteria/<値>.md）が変わる。
#: 旧 "written" は "essay" に読み替える（Question._coerce_answer_format）。
AnswerFormat = Literal[
    "answer_only",       # 答えのみ。決定的照合（app.answer_match）＋曖昧時のみ Haiku
    "essay",             # 記述・論述（国語ほか汎用）
    "translation_ja",    # 和訳（外国語・古文・漢文 → 日本語）
    "translation_en",    # 英訳（日本語 → 英語）
    "composition_en",    # 自由英作文
    "math_proof",        # 証明・数式記述（数学）
    "sci_derivation",    # 計算・導出（物理・化学）
    "sci_explanation",   # 論述・理由説明（物理・化学）
    "graph",             # グラフ・作図（答案画像を一次資料に採点）
    "chem_structure",    # 構造式（答案画像を一次資料に採点）
]

#: 採点経路。deterministic=機械照合のみ / llm_text=転記テキスト中心 / llm_visual=答案画像中心
GradingPath = Literal["deterministic", "llm_text", "llm_visual"]

#: answer_format -> (エディタ表示ラベル, 採点経路, 解答言語の固定値 or None)。
#: ここが唯一の情報源。grader / main / editor / review はこの表を介して分岐する。
ANSWER_FORMAT_META: dict[str, tuple[str, str, str | None]] = {
    "answer_only":     ("答えのみ（自動照合）",             "deterministic", None),
    "essay":           ("記述・論述（国語ほか汎用）",       "llm_text",      None),
    "translation_ja":  ("和訳",                             "llm_text",      "ja"),
    "translation_en":  ("英訳",                             "llm_text",      "en"),
    "composition_en":  ("自由英作文",                       "llm_text",      "en"),
    "math_proof":      ("証明・数式記述（数学）",           "llm_text",      None),
    "sci_derivation":  ("計算・導出（物理・化学）",         "llm_text",      None),
    "sci_explanation": ("論述・理由説明（物理・化学）",     "llm_text",      None),
    "graph":           ("グラフ・作図",                     "llm_visual",    None),
    "chem_structure":  ("構造式",                           "llm_visual",    None),
}

#: 旧値 → 新値の読み替え表
_LEGACY_ANSWER_FORMATS = {"written": "essay", "": "essay"}


def grading_path(answer_format: str) -> str:
    """採点形式の採点経路を返す。未知の値は llm_text 扱い（採点を止めない）。"""
    meta = ANSWER_FORMAT_META.get(answer_format)
    return meta[1] if meta else "llm_text"


def format_label(answer_format: str) -> str:
    """採点形式の表示ラベルを返す（未知ならその値をそのまま）。"""
    meta = ANSWER_FORMAT_META.get(answer_format)
    return meta[0] if meta else answer_format


def resolved_language(answer_format: str, language: str) -> str:
    """採点形式が解答言語を固定する場合はそれを優先する（和訳=ja、英訳/英作文=en）。"""
    meta = ANSWER_FORMAT_META.get(answer_format)
    return meta[2] if meta and meta[2] else language


#: 設問を採点した主体。deterministic=機械照合のみ、llm=フォールバック LLM を使用
GraderKind = Literal["llm", "deterministic"]

Confidence = Literal["high", "medium", "low"]

Language = Literal["ja", "en"]

ISSUE_TAGS: tuple[str, ...] = get_args(IssueTag)
ISSUE_KINDS: tuple[str, ...] = get_args(IssueKind)


# --------------------------------------------------------------------------
# template.json（SPEC §6.1）
# --------------------------------------------------------------------------


class MarkerRecord(BaseModel):
    """ページ 1 枚分の ArUco マーカー 1 個の実測中心。

    ``cx`` / ``cy`` はキャンバス実ピクセル座標（300dpi レンダリング画像上）。
    計算値ではなく blank.pdf を実際にレンダリングして検出した実測値を入れる（SPEC §7.2）。
    """

    id: int = Field(ge=0)
    cx: float
    cy: float


class PageInfo(BaseModel):
    """テンプレート 1 ページ分のキャンバス定義。"""

    page_no: int = Field(ge=1)
    canvas_w: int = Field(gt=0)
    canvas_h: int = Field(gt=0)
    markers: list[MarkerRecord] = Field(default_factory=list)
    #: 右下 QR の検出 4 隅（キャンバス実ピクセル、cv2 順: 左上→右上→右下→左下）。
    #: 計算値ではなく blank.pdf を実測した値。位置合わせの追加対応点に使う。空 or ちょうど 4 点。
    qr_quad: list[tuple[float, float]] = Field(default_factory=list)

    @field_validator("qr_quad")
    @classmethod
    def _validate_qr_quad(cls, v: list[tuple[float, float]]):
        if v and len(v) != 4:
            raise ValueError(f"qr_quad は空 またはちょうど 4 点である必要があります: {len(v)} 点")
        return v

    def marker_map(self) -> dict[int, tuple[float, float]]:
        """ID -> 中心(キャンバス実ピクセル) の辞書。ホモグラフィ構築時の既知点として使う。"""
        return {m.id: (m.cx, m.cy) for m in self.markers}


class RegionRef(BaseModel):
    """1 設問の解答が書かれる矩形 1 個。

    ``rect`` はキャンバスに対する正規化値 ``[x0, y0, x1, y1]``（左上原点、SPEC §5.1）。
    geometry の ``NormRect`` へは ``geometry.make_norm_rect(*region.rect)`` で変換する。
    """

    page_no: int = Field(ge=1)
    rect: tuple[float, float, float, float]

    @field_validator("rect")
    @classmethod
    def _validate_rect(cls, v: tuple[float, float, float, float]):
        x0, y0, x1, y1 = v
        if not all(0.0 <= t <= 1.0 for t in v):
            raise ValueError(f"領域座標は正規化値 [0,1] で指定してください: {v}")
        if not (x1 > x0 and y1 > y0):
            raise ValueError(f"領域座標は x1>x0 かつ y1>y0 である必要があります: {v}")
        return v


class Question(BaseModel):
    """1 設問の定義。"""

    id: str = Field(min_length=1)
    #: 大問・小問番号などの自由記述メモ（採点経路には影響しない。GET /api/stats の by_type で集計）。
    type: str = ""
    max_score: int = Field(ge=0)
    #: 順序が意味を持つ。記入順（上から下・左から右）に並べる。OCR 結果はこの順で連結する。
    regions: list[RegionRef] = Field(default_factory=list)
    #: テンプレートディレクトリからの相対パス。ファイルまたはディレクトリ。
    refs: list[str] = Field(default_factory=list)
    note: str = ""
    #: 解答言語。ただし answer_format が言語を固定する場合はそちらが優先（resolved_language）。
    language: Language = "ja"
    #: 採点形式。既定は汎用の記述式（旧 template.json の "written" は "essay" に読み替える）。
    answer_format: AnswerFormat = "essay"
    #: answer_format=answer_only のときの模範解答。複数正答は改行または「/」区切り。
    answer_key: str = ""
    #: 数値解答の絶対許容誤差。0 のとき設定既定（ANSWER_ONLY_NUMERIC_TOLERANCE）を使う。
    answer_tolerance: float = Field(default=0.0, ge=0.0)

    @field_validator("answer_format", mode="before")
    @classmethod
    def _coerce_answer_format(cls, v: object) -> object:
        """旧値・空値を現行の分類に読み替える。未知の非空文字列は Literal 検証に委ねる。"""
        if v is None:
            return "essay"
        if isinstance(v, str):
            return _LEGACY_ANSWER_FORMATS.get(v, v)
        return v

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if _FORBIDDEN_ID_CHARS.search(v):
            raise ValueError(
                f'設問 ID にファイル名として使えない文字が含まれています（/ \\ : * ? " < > | は禁止）: {v}'
            )
        if v != v.strip():
            raise ValueError(f"設問 ID の前後に空白を含めないでください: {v!r}")
        return v


class Template(BaseModel):
    """template.json 全体。"""

    schema_version: int = SCHEMA_VERSION
    template_id: str = Field(min_length=1)
    title: str = ""
    created_at: datetime = Field(default_factory=now_jst)
    source_pdf: str = "source.pdf"
    blank_pdf: str = "blank.pdf"
    dpi: int = 300
    pages: list[PageInfo] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    #: 命名規則で対応付けた参照資料。エディタで新しい設問を作るときに引き継ぐ。
    default_refs: list[str] = Field(default_factory=list)

    @field_validator("template_id")
    @classmethod
    def _validate_template_id(cls, v: str) -> str:
        if _FORBIDDEN_ID_CHARS.search(v) or v in (".", ".."):
            raise ValueError(f"テンプレート ID に使えない文字が含まれています: {v}")
        return v

    def page(self, page_no: int) -> PageInfo | None:
        for p in self.pages:
            if p.page_no == page_no:
                return p
        return None

    def question(self, question_id: str) -> Question | None:
        for q in self.questions:
            if q.id == question_id:
                return q
        return None

    @property
    def total_max_score(self) -> int:
        return sum(q.max_score for q in self.questions)


# --------------------------------------------------------------------------
# session.json（SPEC 外の追加。転記状態の永続化とクロップ原点の記録に必須）
# --------------------------------------------------------------------------


class OCRWordRecord(BaseModel):
    """OCR が返した単語 1 個。``bbox`` は切り出し画像内ピクセル座標 (x0, y0, x1, y1)。

    ``app.ocr.base.Word`` の永続化用ミラー。相互変換は ``app.ocr.base`` 側に置く。
    """

    text: str
    bbox: tuple[int, int, int, int]


class CropInfo(BaseModel):
    """切り出し画像 1 枚と、その転記状態。

    ``crop_origin_canvas`` は切り出し時に実際に使った左上原点（キャンバス実ピクセル座標）。
    ページ端ではマージンがクランプされるため ``rect - margin`` からの再計算では
    正しい値にならない。赤入れ時の逆変換はこの実測値を使うこと（SPEC §9.8 手順5 の補正）。
    """

    question_id: str
    index: int = Field(ge=0)
    page_no: int = Field(ge=1)
    region_rect: tuple[float, float, float, float]
    crop_origin_canvas: tuple[float, float]
    crop_w: int = Field(gt=0)
    crop_h: int = Field(gt=0)
    margin_px: int = Field(ge=0)
    ocr_text: str = ""
    ocr_words: list[OCRWordRecord] = Field(default_factory=list)
    ocr_error: str | None = None

    @property
    def filename(self) -> str:
        return f"{self.question_id}_{self.index}.png"


class QuestionTranscription(BaseModel):
    """設問単位の転記テキスト（複数 region の OCR を記入順に連結したもの）。"""

    question_id: str
    transcription: str = ""
    transcription_edited: bool = False
    is_blank: bool = False


class PageRecord(BaseModel):
    """スキャン 1 ページの処理結果。"""

    page_no: int
    source_index: int = Field(ge=0, description="投入ファイル内での 0 始まりの並び")
    normalized_filename: str
    template_id: str
    qr_detected: bool = False
    qr_payload: str | None = None
    marker_ids: list[int] = Field(default_factory=list)
    #: 正規化後に再検出したマーカー中心と template.json 記録値との最大ズレ(px)。品質指標。
    alignment_error_px: float | None = None


class FailedPage(BaseModel):
    """位置合わせに失敗したページ（SPEC §10.1）。"""

    source_index: int
    filename: str
    reason: str
    detected_marker_ids: list[int] = Field(default_factory=list)


SessionStatus = Literal["processing", "ready", "grading", "graded", "failed"]


class SessionState(BaseModel):
    """data/scans/<session_id>/session.json。採点セッションの全状態。"""

    schema_version: int = SCHEMA_VERSION
    session_id: str
    template_id: str
    status: SessionStatus = "processing"
    created_at: datetime = Field(default_factory=now_jst)
    ocr_backend: str = ""
    pages: list[PageRecord] = Field(default_factory=list)
    crops: list[CropInfo] = Field(default_factory=list)
    transcriptions: list[QuestionTranscription] = Field(default_factory=list)
    failed_pages: list[FailedPage] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    #: 処理中の進捗メッセージ（UI のポーリング表示用、SPEC §9.10）
    progress: str = ""
    error: str | None = None

    def transcription(self, question_id: str) -> QuestionTranscription | None:
        for t in self.transcriptions:
            if t.question_id == question_id:
                return t
        return None

    def crops_for(self, question_id: str) -> list[CropInfo]:
        return sorted(
            (c for c in self.crops if c.question_id == question_id),
            key=lambda c: c.index,
        )


# --------------------------------------------------------------------------
# result.json（SPEC §6.2）
# --------------------------------------------------------------------------


class Issue(BaseModel):
    """採点者が指摘した誤り 1 件。"""

    #: 答案本文からの原文ママ引用。該当箇所がない指摘（訳出漏れ等）は空文字列。
    quote: str = ""
    kind: IssueKind = "その他"
    tag: IssueTag = "その他"
    comment: str = ""
    deduction: int = 0


class ResultQuestion(BaseModel):
    """設問 1 問分の採点結果。"""

    id: str
    max_score: int
    #: API が 3 回失敗した設問は None（SPEC §10.3）
    score: int | None = None
    transcription: str = ""
    transcription_edited: bool = False
    feedback: str = ""
    issues: list[Issue] = Field(default_factory=list)
    model_answer_note: str = ""
    confidence: Confidence = "high"
    #: 未記入としてスキップした設問
    skipped_blank: bool = False
    #: この設問を採点した主体。記述式=llm、答えのみ=deterministic（曖昧時のみ llm）
    grader: GraderKind = "llm"
    #: 実際に採点に使ったモデル名。決定的照合のみなら "answer-match"
    model: str = ""


class Result(BaseModel):
    """result.json 全体。"""

    schema_version: int = SCHEMA_VERSION
    session_id: str
    template_id: str
    graded_at: datetime = Field(default_factory=now_jst)
    model: str = ""
    provider: str = ""
    total_score: int = 0
    total_max_score: int = 0
    questions: list[ResultQuestion] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# API リクエスト/レスポンス補助モデル
# --------------------------------------------------------------------------


class TemplateSummary(BaseModel):
    """テンプレート一覧の 1 行。"""

    template_id: str
    title: str
    created_at: datetime
    page_count: int
    question_count: int
    total_max_score: int
    scan_ready: bool = False


class TranscriptionUpdate(BaseModel):
    """PUT /api/sessions/{id}/transcriptions のリクエスト要素。"""

    question_id: str
    transcription: str
    is_blank: bool = False


class TranscriptionUpdateRequest(BaseModel):
    items: list[TranscriptionUpdate]


class RefNode(BaseModel):
    """参照資料ツリーの 1 ノード。"""

    name: str
    path: str
    is_dir: bool
    children: list["RefNode"] = Field(default_factory=list)


class TagStat(BaseModel):
    tag: str
    count: int
    total_deduction: int


class StatsResponse(BaseModel):
    """GET /api/stats のレスポンス（SPEC §9.9）。"""

    since: str | None = None
    question_count: int = 0
    average_rate: float = 0.0
    tags: list[TagStat] = Field(default_factory=list)
    by_type: dict[str, float] = Field(default_factory=dict)

"""Anthropic Messages API による採点（SPEC §9.5）。

参照資料の PDF は **ネイティブの document ブロック** で添付できるのが Anthropic の利点で、
仕様書の記載どおりに実装している。

注意（実測で確認した現行 API の仕様）:
    仕様書 §9.5 は ``temperature: 0`` を要求しているが、現行の Claude モデル
    （Sonnet 5 / Opus 5 など）では ``temperature`` は **API から削除**されており、
    送ると 400 になる（インストール済み SDK の ``messages.create`` シグネチャにも存在しない）。
    そのため Anthropic では temperature を送らない。採点の再現性は
    「設問ごとに 1 リクエスト」「同一プロンプト」「DOUBLE_GRADING による点差検出」で担保する。
    OpenAI 互換バックエンド側では temperature=0 を送っている。

    拡張思考は **切らない**。Opus 5 は既定で思考が有効で、``thinking: disabled`` にすると
    ツール呼び出しを ``tool_use`` ブロックではなく可視テキストに書いてしまう既知の失敗
    （エラーは出ず、採点だけが静かに落ちる）がある。強制ツール呼び出しと併用できない
    モデルに当たった場合は、思考を保ったまま ``tool_choice`` を ``auto`` に落とす。
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from app.llm.base import GradeResult, GradingRequest, LLMError, LLMGradingBackend, LLMResponseError
from app.llm.tool_schema import TOOL_NAME, anthropic_tool, anthropic_tool_choice

logger = logging.getLogger(__name__)

#: 1 リクエストに添付する PDF の上限（API 制限 32MB に対する安全側の目安）
_MAX_PDF_BYTES = 25 * 1024 * 1024

#: プロンプトキャッシュの区切り。採点は「設問ごとに 1 リクエスト」で、同じ設問を
#: 生徒の人数だけ繰り返す。system と参照資料はその間ずっと不変なので、既定の 5 分では
#: 1 人分の採点をまたげないことがある。1 時間なら 1 クラス分の再送を拾える。
_CACHE_CONTROL: dict[str, Any] = {"type": "ephemeral", "ttl": "1h"}

#: 強制ツール呼び出しを取り下げたときに、代わりに system へ足す指示。
_TOOL_INSTRUCTION = f"採点結果は必ず {TOOL_NAME} ツールを呼び出して提出すること。本文に直接書いてはならない。"


class AnthropicGradingBackend(LLMGradingBackend):
    """Anthropic Claude による採点バックエンド。"""

    provider = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str,
        max_tokens: int = 8192,
        timeout: float = 300.0,
        prompt_cache: bool = True,
    ) -> None:
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover
            raise LLMError("anthropic がインストールされていません。") from e
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
        self.model = model
        self._max_tokens = max_tokens
        self._prompt_cache = prompt_cache
        #: 強制ツール呼び出しが拒否されたモデルに当たったら False に倒す
        self._force_tool_choice = True

    def _degrade(self, error: Exception) -> str:
        """400 の原因になりうる任意指定を 1 つ落とす。落とせるものが無ければ空文字。

        採点を止めるより、機能を 1 段落として続けるほうが利用者の損失が小さい。
        落とした事実はログに残す。
        """
        message = str(error).lower()
        if self._prompt_cache and "cache" in message:
            self._prompt_cache = False
            return "プロンプトキャッシュを無効化"
        if self._force_tool_choice and ("tool_choice" in message or "thinking" in message):
            # 思考を切ると Opus 5 はツール呼び出しを本文に書いてしまう。強制のほうを外す。
            self._force_tool_choice = False
            return "強制ツール呼び出しを取り下げ"
        return ""

    def _build_kwargs(self, request: GradingRequest) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self._max_tokens,
            "system": _build_system(
                request.system_prompt,
                prompt_cache=self._prompt_cache,
                force_tool_choice=self._force_tool_choice,
            ),
            "messages": [
                {"role": "user", "content": _build_content(request, prompt_cache=self._prompt_cache)}
            ],
            "tools": [anthropic_tool()],
        }
        if self._force_tool_choice:
            kwargs["tool_choice"] = anthropic_tool_choice()
        return kwargs

    def grade(self, request: GradingRequest) -> GradeResult:
        # 落とせる指定が尽きるまで段階的に外す（複数が同時に拒否されることがある）。
        while True:
            try:
                response = self._client.messages.create(**self._build_kwargs(request))
                break
            except self._anthropic.BadRequestError as e:
                degraded = self._degrade(e)
                if not degraded:
                    raise LLMError(f"Anthropic がリクエストを拒否しました: {e}") from e
                logger.warning("%sして再試行します: %s", degraded, e)
            except Exception as e:
                raise LLMError(f"Anthropic の呼び出しに失敗しました: {e}") from e

        if getattr(response, "stop_reason", None) == "refusal":
            raise LLMResponseError("Anthropic が安全性の理由で応答を拒否しました。")

        for block in response.content:
            if getattr(block, "type", "") == "tool_use" and block.name == TOOL_NAME:
                data = dict(block.input)
                return GradeResult(
                    score=int(data.get("score", 0)),
                    feedback=str(data.get("feedback", "")),
                    issues=list(data.get("issues", []) or []),
                    confidence=str(data.get("confidence", "low")),
                    raw=data,
                )
        raise LLMResponseError(
            f"採点ツール呼び出しが返りませんでした（stop_reason={getattr(response, 'stop_reason', None)}）"
        )


def _build_system(
    system_prompt: str, *, prompt_cache: bool, force_tool_choice: bool = True
) -> list[dict[str, Any]]:
    """system をブロック形式で組み立てる（キャッシュ区切りを置けるようにするため）。"""
    blocks: list[dict[str, Any]] = [{"type": "text", "text": system_prompt}]
    if not force_tool_choice:
        blocks.append({"type": "text", "text": _TOOL_INSTRUCTION})
    if prompt_cache:
        blocks[-1]["cache_control"] = dict(_CACHE_CONTROL)
    return blocks


def _build_content(request: GradingRequest, *, prompt_cache: bool = True) -> list[dict[str, Any]]:
    """SPEC §9.5 のリクエスト構成でコンテンツブロックを組み立てる。

    1. 参照資料（PDF は document ブロック、md/txt は text ブロック）
    2. 切り出し画像
    3. 設問情報と転記テキスト
    """
    content: list[dict[str, Any]] = []

    for ref in request.refs:
        if ref.kind == "pdf":
            data = ref.read_bytes()
            if len(data) > _MAX_PDF_BYTES:
                # 黙って省略すると、模範解答なしで採点した結果が正常に見えてしまう。
                raise LLMError(
                    f"参照 PDF が大きすぎて添付できません（{len(data) / 1048576:.1f}MB > "
                    f"{_MAX_PDF_BYTES // 1048576}MB）: {ref.name}。"
                    "分割するか、テキスト部分を .md に変換して添付してください。"
                )
            content.append(
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        # base64 に改行を含めないこと（API 制約）
                        "data": base64.standard_b64encode(data).decode("ascii"),
                    },
                }
            )
        else:
            content.append(
                {"type": "text", "text": f"<参照資料: {ref.name}>\n{ref.read_text()}"}
            )

    # tools -> system -> 参照資料 までが、答案が変わっても不変のプレフィックス。
    # ここに区切りを置くと、生徒ごとの再送と DOUBLE_GRADING の 2 回目がキャッシュ読み出しになる。
    if prompt_cache and content:
        content[-1]["cache_control"] = dict(_CACHE_CONTROL)

    for png in request.crop_images:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.standard_b64encode(png).decode("ascii"),
                },
            }
        )

    content.append({"type": "text", "text": request.question_text})
    return content

"""設定（.env 読み込み）と、パス・認証情報の解決（SPEC §10.4）。

起動時検証の方針（SPEC §10.3 からの意図的な調整）:
    - **OCR の認証情報は起動時ハードゲート**。欠落していればプロセスを終了する。
    - **LLM 採点プロバイダのキーは起動時にはブロックしない**（警告のみ）。
      実際に採点を実行する時点で検出し、HTTP 503 と日本語メッセージで返す。
      理由: 採点キー未設定でも、テンプレート作成〜OCR 転記までは正常に使えるため、
      そこまで起動不能にするのは実用上の損失が大きい。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.runtime import config_dir, default_data_root, is_frozen, resource_dir

logger = logging.getLogger(__name__)

#: プロジェクトルート（app/ の 1 つ上）。CWD に依存せずパスを解決するための基準。
PROJECT_ROOT = resource_dir()
CONFIG_ROOT = config_dir()

OCRBackendName = Literal["google_vision", "azure_di", "claude_vision"]
GradingProviderName = Literal["anthropic", "openai", "kimi", "openai_compatible"]


class ConfigError(RuntimeError):
    """設定不備。起動時ハードゲートで送出する。"""


class MissingCredentialError(RuntimeError):
    """API キー等が未設定。採点実行時に検出して 503 に変換する。"""


class Settings(BaseSettings):
    """.env から読み込む設定値。"""

    model_config = SettingsConfigDict(
        env_file=str(CONFIG_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- OCR ---
    ocr_backend: OCRBackendName = "google_vision"
    google_application_credentials: str = ""
    azure_di_endpoint: str = ""
    azure_di_key: str = ""

    # --- LLM 採点 ---
    grading_provider: GradingProviderName = "anthropic"
    #: プロバイダ別モデル未設定時のフォールバック
    grading_model: str = ""
    grading_max_tokens: int = 8192

    anthropic_api_key: str = ""
    #: 記述式の採点は視覚と長い推論の複合タスクなので Opus を既定にする。
    #: .env の ANTHROPIC_MODEL を空にしたときここへ落ちるため、.env.example と揃えておく。
    anthropic_model: str = "claude-opus-5"

    openai_api_key: str = ""
    openai_model: str = ""
    openai_base_url: str = ""

    kimi_api_key: str = ""
    kimi_model: str = ""
    kimi_base_url: str = "https://api.moonshot.ai/v1"

    #: grading_provider=openai_compatible のときに使う汎用設定
    llm_api_key: str = ""
    llm_model: str = ""
    llm_base_url: str = ""

    #: 採点の system と参照資料をプロンプトキャッシュに載せるか（Anthropic のみ）。
    #: 同じ設問を生徒の人数だけ採点するため、既定で有効にしておく価値が大きい。
    grading_prompt_cache: bool = True

    # --- 採点の安定化（SPEC §9.7）---
    double_grading: bool = False
    double_grading_threshold: int = 3

    # --- 答えのみ設問の採点 ---
    #: 決定的照合で白黒つかない文字列ケースを Haiku にフォールバックさせるか
    answer_only_llm_fallback: bool = True
    #: フォールバックに使うモデル（Anthropic の ANTHROPIC_API_KEY を流用する）
    answer_only_model: str = "claude-haiku-4-5"
    #: 数値解答の既定の絶対許容誤差（設問側の answer_tolerance が 0 のとき使う）
    answer_only_numeric_tolerance: float = 0.0

    # --- スキャン ---
    scan_dpi: int = 300
    #: 切り出し時に付与する上下左右のマージン（SPEC §9.3）
    crop_margin_px: int = 8

    # --- サーバー ---
    host: str = "127.0.0.1"
    port: int = 8765

    #: データ格納先（プロジェクトルートからの相対パス可）。テストでは一時ディレクトリを指す。
    data_root: str = str(default_data_root())

    # Public GitHub repository in the form "owner/repository". Empty disables updates.
    update_repository: str = ""

    # ------------------------------------------------------------------
    # パス
    # ------------------------------------------------------------------

    @property
    def data_dir(self) -> Path:
        return self.resolve_path(self.data_root)

    @property
    def templates_dir(self) -> Path:
        return self.data_dir / "templates"

    @property
    def scans_dir(self) -> Path:
        return self.data_dir / "scans"

    @property
    def results_dir(self) -> Path:
        return self.data_dir / "results"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def prompts_dir(self) -> Path:
        return PROJECT_ROOT / "app" / "prompts"

    def template_dir(self, template_id: str) -> Path:
        return self.templates_dir / template_id

    def session_dir(self, session_id: str) -> Path:
        return self.scans_dir / session_id

    def result_dir(self, session_id: str) -> Path:
        return self.results_dir / session_id

    def resolve_path(self, path: str | Path) -> Path:
        """相対パスをプロジェクトルート基準の絶対パスに解決する（CWD 非依存）。"""
        p = Path(path)
        if p.is_absolute():
            return p
        # Credentials configured by an installed user live beside their .env,
        # while prompts/static assets remain bundled under PROJECT_ROOT.
        return ((CONFIG_ROOT if is_frozen() else PROJECT_ROOT) / p).resolve()

    def ensure_dirs(self) -> None:
        for d in (self.templates_dir, self.scans_dir, self.results_dir, self.logs_dir / "review"):
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 認証情報
    # ------------------------------------------------------------------

    def apply_google_credentials(self) -> None:
        """GOOGLE_APPLICATION_CREDENTIALS を絶対パスに直して OS 環境変数へ流し込む。

        ``google-cloud-vision`` のクライアントは OS 環境変数からデフォルト認証を
        探しに行くため、クライアント構築前に必ずこれを呼ぶ必要がある。
        """
        if not self.google_application_credentials:
            return
        cred = self.resolve_path(self.google_application_credentials)
        if not cred.exists():
            raise ConfigError(
                f"GOOGLE_APPLICATION_CREDENTIALS のファイルが見つかりません: {cred}\n"
                f".env の設定値: {self.google_application_credentials}"
            )
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(cred)

    def validate_ocr_or_die(self) -> None:
        """OCR バックエンドの認証情報を起動時に検証する（欠落なら例外）。"""
        if self.ocr_backend == "google_vision":
            if not self.google_application_credentials:
                raise ConfigError(
                    "OCR_BACKEND=google_vision ですが GOOGLE_APPLICATION_CREDENTIALS が "
                    "未設定です。.env にサービスアカウント JSON のパスを設定してください。"
                )
            self.apply_google_credentials()
        elif self.ocr_backend == "azure_di":
            missing = [
                name
                for name, value in (
                    ("AZURE_DI_ENDPOINT", self.azure_di_endpoint),
                    ("AZURE_DI_KEY", self.azure_di_key),
                )
                if not value
            ]
            if missing:
                raise ConfigError(
                    f"OCR_BACKEND=azure_di ですが {', '.join(missing)} が未設定です。"
                )
        elif self.ocr_backend == "claude_vision":
            if not self.anthropic_api_key:
                raise ConfigError(
                    "OCR_BACKEND=claude_vision ですが ANTHROPIC_API_KEY が未設定です。"
                )

    # ------------------------------------------------------------------
    # 採点プロバイダ
    # ------------------------------------------------------------------

    def grading_credentials(self) -> tuple[str, str, str | None]:
        """採点プロバイダの ``(api_key, model, base_url)`` を解決する。

        モデル名はプロバイダ別変数を優先し、未設定なら共通の ``GRADING_MODEL`` を使う。
        プロバイダだけ切り替えたときに古いモデル名が残って不可解な 400 になる事故を防ぐため。

        Raises:
            MissingCredentialError: API キーまたはモデル名が解決できない場合。
        """
        provider = self.grading_provider
        if provider == "anthropic":
            key, model, base_url, key_name = (
                self.anthropic_api_key,
                self.anthropic_model or self.grading_model,
                None,
                "ANTHROPIC_API_KEY",
            )
        elif provider == "openai":
            key, model, base_url, key_name = (
                self.openai_api_key,
                self.openai_model or self.grading_model,
                self.openai_base_url or None,
                "OPENAI_API_KEY",
            )
        elif provider == "kimi":
            key, model, base_url, key_name = (
                self.kimi_api_key,
                self.kimi_model or self.grading_model,
                self.kimi_base_url,
                "KIMI_API_KEY",
            )
        elif provider == "openai_compatible":
            key, model, base_url, key_name = (
                self.llm_api_key,
                self.llm_model or self.grading_model,
                self.llm_base_url,
                "LLM_API_KEY",
            )
        else:  # pragma: no cover - Literal で弾かれる
            raise MissingCredentialError(f"未知の採点プロバイダです: {provider}")

        if not key:
            raise MissingCredentialError(
                f"GRADING_PROVIDER={provider} ですが {key_name} が未設定です。"
                f".env に設定してからサーバーを再起動してください。"
            )
        if not model:
            upper = provider.upper()
            raise MissingCredentialError(
                f"GRADING_PROVIDER={provider} のモデル名が未設定です。"
                f".env の {upper}_MODEL（または GRADING_MODEL）を設定してください。"
            )
        if provider in ("kimi", "openai_compatible") and not base_url:
            raise MissingCredentialError(
                f"GRADING_PROVIDER={provider} のベース URL が未設定です。"
                f".env の {'KIMI_BASE_URL' if provider == 'kimi' else 'LLM_BASE_URL'} を設定してください。"
            )
        return key, model, base_url

    def warn_missing_grading_credentials(self) -> str | None:
        """採点キーが未設定なら警告文を返す（起動をブロックはしない）。"""
        try:
            self.grading_credentials()
        except MissingCredentialError as e:
            return str(e)
        return None


_settings: Settings | None = None


def get_settings() -> Settings:
    """設定のシングルトンを返す。"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings_cache() -> None:
    """テスト用: 設定キャッシュを破棄する。"""
    global _settings
    _settings = None

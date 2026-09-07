"""OCR バックエンドのファクトリ（SPEC §8）。"""

from __future__ import annotations

from app.config import Settings
from app.ocr.base import OCRBackend, OCRError, OCRResult, Word, records_to_words, words_to_records

__all__ = [
    "OCRBackend",
    "OCRError",
    "OCRResult",
    "Word",
    "get_ocr_backend",
    "records_to_words",
    "words_to_records",
]


def get_ocr_backend(settings: Settings) -> OCRBackend:
    """設定に応じた OCR バックエンドを構築する。"""
    backend = settings.ocr_backend
    if backend == "google_vision":
        from app.ocr.google_vision import GoogleVisionBackend

        settings.apply_google_credentials()
        return GoogleVisionBackend()
    if backend == "azure_di":
        from app.ocr.azure_di import AzureDocumentIntelligenceBackend

        return AzureDocumentIntelligenceBackend(
            endpoint=settings.azure_di_endpoint, key=settings.azure_di_key
        )
    if backend == "claude_vision":
        from app.ocr.claude_vision import ClaudeVisionBackend

        return ClaudeVisionBackend(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model or settings.grading_model,
        )
    raise OCRError(f"未知の OCR バックエンドです: {backend}")

"""テスト共通のフィクスチャ。

外部 API を叩くテストは ``@pytest.mark.integration`` を付け、既定では skip する
（SPEC §12）。合成データで完結するテストだけが既定で走る。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from app.config import Settings
from app.models import Question, RegionRef, Template
from app.template_builder import build_blank_pdf


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "integration: 外部 API を呼ぶテスト（既定では skip）")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("-m"):
        return
    skip = pytest.mark.skip(reason="外部 API を呼ぶため既定では skip（-m integration で実行）")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """データ領域を一時ディレクトリに向けた設定。"""
    s = Settings(data_root=str(tmp_path / "data"), ocr_backend="google_vision")
    s.ensure_dirs()
    return s


@pytest.fixture
def source_pdf(tmp_path: Path) -> Path:
    """合成の白紙解答用紙（A4、罫線つき）。"""
    path = tmp_path / "source.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text(pymupdf.Point(80, 120), "Question 1", fontsize=12)
    page.draw_rect(pymupdf.Rect(70, 140, 525, 260))
    page.draw_rect(pymupdf.Rect(70, 300, 525, 420))
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def built_template(settings: Settings, source_pdf: Path) -> Template:
    """blank.pdf まで生成済みのテンプレート（設問 1 問）。"""
    template_id = "test-template"
    directory = settings.template_dir(template_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "refs").mkdir(exist_ok=True)
    target_source = directory / "source.pdf"
    target_source.write_bytes(source_pdf.read_bytes())

    build = build_blank_pdf(target_source, directory / "blank.pdf", template_id, dpi=300)
    template = Template(
        template_id=template_id,
        title="テスト用テンプレート",
        pages=build.pages,
        questions=[
            Question(
                id="1-(1)",
                type="和文英訳",
                max_score=15,
                regions=[RegionRef(page_no=1, rect=(0.12, 0.17, 0.88, 0.31))],
                language="en",
            )
        ],
    )
    from app.storage import save_template

    save_template(settings, template)
    return template

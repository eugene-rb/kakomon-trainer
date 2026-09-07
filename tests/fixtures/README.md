# テスト用フィクスチャ

SPEC §12 は「手書き答案のサンプル画像をここに置き、M3 以降の回帰テストに使う」ことを求めている。
合成データで作れないのは **実際の手書き** だけなので、ここには実物を置く。

## 置くもの

1. `blank.pdf` を印刷して手書きで記入し、300dpi 以上でスキャンしたファイル
   （`filled_<テンプレートID>.pdf` のような名前）
2. そのテンプレートの `template.json`（`template_<テンプレートID>.json`）

## 使い方

置いたあと、実物での位置合わせ精度を確認する:

```powershell
python scripts/check_alignment.py <template_id> tests/fixtures/filled_<template_id>.pdf
```

OCR を含む回帰テストを書く場合は `@pytest.mark.integration` を付けること
（外部 API を叩くテストは既定で skip される）。

現状、合成データで完結する検証は `tests/` の各テストが自動でカバーしている。
実物スキャンでしか確認できないのは「印刷 → スキャン経路での位置合わせ精度」と
「実際の手書き文字に対する OCR 精度」の 2 点。

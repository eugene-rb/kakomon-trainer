"""位置合わせ精度を確認する CLI（SPEC M1 の検証項目）。

自動テスト（tests/test_geometry.py）と **同じ geometry / markers の関数** を通すので、
合成データでの結果と実物スキャンでの結果が同じコードパスで比較できる。

使い方:
    # 合成データで確認（印刷不要。パイプラインの健全性チェック）
    python scripts/check_alignment.py --synthetic <template_id>

    # 実際に印刷・記入・スキャンしたファイルで確認
    python scripts/check_alignment.py <template_id> <scanned.pdf|png> [...]

出力: ページごとに、正規化後のマーカー中心と template.json の記録値との
      ズレ（最大 px / RMS px）。300dpi で 3px 未満なら実用上問題ない。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np

from app import geometry, markers, pdfutil, scanner
from app.config import get_settings
from app.geometry import AlignmentError, make_canvas_px
from app.storage import load_template

#: 300dpi でこれを超えたら位置合わせに問題があるとみなす目安
WARN_THRESHOLD_PX = 3.0


def _synthetic_scan(canvas: np.ndarray) -> np.ndarray:
    """合成の歪み（回転・並進・キーストーン）をかけて擬似スキャン画像を作る。"""
    h, w = canvas.shape[:2]
    matrix = cv2.getPerspectiveTransform(
        np.float32([[0, 0], [w, 0], [w, h], [0, h]]),
        np.float32([[60, 130], [w - 20, 40], [w - 90, h - 60], [30, h - 130]]),
    )
    return cv2.warpPerspective(canvas, matrix, (w, h), borderValue=(255, 255, 255))


def main() -> int:
    parser = argparse.ArgumentParser(description="位置合わせ精度の確認")
    parser.add_argument("template_id", help="テンプレート ID")
    parser.add_argument("scans", nargs="*", type=Path, help="スキャンした PDF / 画像")
    parser.add_argument(
        "--synthetic", action="store_true", help="blank.pdf を合成的に歪ませて確認する（印刷不要）"
    )
    args = parser.parse_args()

    settings = get_settings()
    template = load_template(settings, args.template_id)
    blank = settings.template_dir(args.template_id) / template.blank_pdf

    if args.synthetic:
        pages = [
            scanner.LoadedPage(source_index=i, image=_synthetic_scan(img), origin="synthetic")
            for i, img in enumerate(pdfutil.pdf_page_arrays(blank, template.dpi))
        ]
        print(f"合成データで確認します（{len(pages)} ページ）")
    else:
        if not args.scans:
            parser.error("スキャンファイルを指定するか --synthetic を付けてください。")
        pages = scanner.load_input_pages(args.scans, settings.scan_dpi)
        print(f"スキャン {len(pages)} ページを確認します")

    worst = 0.0
    failures = 0
    for loaded in pages:
        try:
            page = scanner.normalize_page(loaded, [template], fallback_page_no=loaded.source_index + 1)
        except AlignmentError as e:
            failures += 1
            detected = sorted(markers.detect_aruco_markers(loaded.image))
            print(f"  [{loaded.source_index + 1}] 失敗: {e}（検出マーカー {detected}）")
            continue

        page_info = template.page(page.page_no)
        known = {m.id: make_canvas_px(m.cx, m.cy) for m in page_info.markers} if page_info else {}
        errors = geometry.alignment_errors(markers.detect_aruco_markers(page.image), known)
        worst = max(worst, errors["max"])
        qr = "QR OK" if page.qr_detected else "QR 読取失敗"
        print(
            f"  [{loaded.source_index + 1}] ページ {page.page_no}: "
            f"最大ズレ {errors['max']:.2f}px / RMS {errors['rms']:.2f}px "
            f"（マーカー {int(errors['count'])} 個, {qr}）"
        )

    print()
    print(f"最大ズレ: {worst:.2f}px（{template.dpi}dpi 基準。{WARN_THRESHOLD_PX}px 未満が目安）")
    if failures:
        print(f"位置合わせ失敗: {failures} ページ")
        print("  確認: スキャン解像度が 300dpi 以上か / 四隅が切れていないか / 影・折れがないか")
    if worst >= WARN_THRESHOLD_PX or failures:
        return 1
    print("問題ありません。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

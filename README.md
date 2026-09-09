# 過去問トレーナー — WPF / .NET 9

紙の答案を取り込み、転記確認・自動採点・赤入れPDF・復習ログの作成まで行うWindowsデスクトップアプリです。画面と処理をC#へ移植しました。ブラウザ、WebView、Python、ローカルHTTPサーバーは使用しません。

Windows 11のFluentデザインを採用し、左側ナビゲーション、角丸のコントロール、Mica背景、Windowsのアクセント色に対応しています。テーマは既定でWindowsに追従し、「設定」からライト／ダークを選択できます。

## 起動

ビルド済みの場合は `artifacts/desktop/KakomonTrainer.exe` を実行してください。配布ZIPは解凍し、同じフォルダーにあるDLL・Assets・promptsを残したままEXEを実行します。自己完結型のため、配布先に.NETを別途インストールする必要はありません。

開発環境では次のコマンドで発行・起動できます。

```powershell
./Start-KakomonTrainer.ps1
```

Windows 10 2004以降 / Windows 11のx64環境が対象です。ソースからのビルドには.NET 9以降のSDKを使用します。ターゲットは `net9.0-windows10.0.19041.0` です。

```powershell
dotnet build KakomonTrainer.sln -c Release
dotnet publish desktop/KakomonTrainer -c Release -r win-x64 --self-contained true -o artifacts/desktop
```

## 使い方

1. **用紙・答案**で「用紙と解説を登録」を押します。用紙はPDF、解説・採点基準はPDF / Markdown / テキストです。`exam_sheet.pdf`、`exam_answers.pdf`、`exam_rubric.md` のように共通名で対応付けます。用紙単独のPDFも登録できます。対応表の確認後、ArUcoマーカー3個とQR付きの印刷用PDFを生成します。
2. **用紙・領域の編集**で設問を追加し、用紙画像をドラッグして解答領域を指定します。設問ごとに複数ページ・複数領域を指定できます。配点、採点形式、正答、数値許容誤差、言語、指示、参照資料を保存します。
3. 「印刷用PDF」を独自ウィンドウで開き、ツールバーから印刷します。記入後、四隅を切らずに300dpi以上でスキャンしてください。
4. 用紙を選び「答案を取り込む」でPDFまたは画像を選びます。QRでページ順と用紙を確認し、回転・台形歪みを補正します。**答案1組ずつ**取り込みます。OCRのチェックを外せば、外部APIなしで手入力の転記ができます。
5. **転記の確認**で切り出し画像と文字を見比べ、読み取りミスを修正します。未記入の設問はチェックを付けます。「保存して採点」で採点します。
6. **採点結果・復習**で得点・講評・誤りを確認します。未確定の設問は合計に含めず明示します。得点は手動修正して保存できます。赤入れPDFはアプリ内で表示・印刷でき、ファイルにも書き出せます。講評はPDFの末尾に収録し、単語座標のあるOCRでは該当引用に下線を入れます。

転記を変更して保存すると、以前の採点結果は再採点まで非表示になります。未保存の用紙・転記・点数の変更は画面移動・終了時に確認します。長時間処理中は操作をロックし、「処理を中止」でキャンセルできます。

## 設定とデータ

アプリの「設定」から保存先、OCRプロバイダ、APIキー、モデル名、APIベースURL、更新リポジトリを指定します。

- OCR: Google Cloud Vision / Azure Document Intelligence / Claude Vision
- 記述・画像採点: Anthropic / OpenAI / Kimi / OpenAI互換API
- 答えのみ: 正規化・分数・百分率・指数・単位・許容誤差による照合。曖昧な表記は設定に応じてClaudeへ確認し、未確定なら手動で点数を確定できます。
- 二重採点: 記述式を独立に2回採点し、設定した点差以上なら要確認にします。
- APIキー未設定でも起動・用紙編集・手動転記・数値照合は利用できます。

設定は `%LOCALAPPDATA%/KakomonTrainer/desktop-settings.bin` にWindows DPAPIで暗号化して保存します。開発チェックアウトでは既存の `.env` と `data/` を初期値に使います。既存の設定値を画面から上書きできます。

配布版の既定データ保存先は `%LOCALAPPDATA%/KakomonTrainer/data` です。旧版のデータを使う場合は「設定」で既存の `data` フォルダーを指定してください。**スキーマv2のテンプレート・答案・採点結果を読み込み、未知のJSONフィールドも保持します。** v1の用紙は物理レイアウトが異なるため再登録が必要です。

新しい答案には取り込み時の用紙定義のスナップショットも保存します。既存答案のある用紙は削除できません。復習ログは `logs/review/<session_id>.md` と `.jsonl` に出力し、再採点時は同じ答案のログを更新します。集計は現在有効な採点結果を使用します。

## 検証と配布

```powershell
# 外部APIなしの結合チェックと自己完結型ZIP作成
./scripts/build_windows.ps1 -PortableOnly

# Inno Setup 6 がある場合はインストーラーも作成
./scripts/build_windows.ps1 -Version 2.0.0
```

結合チェックは専用の一時フォルダーにPDFを生成し、登録、マーカー実測、180度回転・ページ入れ替え・台形補正、領域切り出し、JSON互換性、数値照合、採点、赤入れPDFの再レンダリング、転記変更後の再採点、履歴更新、キャンセルを検証します。既存データや実APIは使いません。

GitHub ActionsはWindows上でチェック・発行を実行します。`v*` タグで `KakomonTrainer-Setup.exe` と `KakomonTrainer-win-x64.zip` をリリースします。設定に `owner/repository` を指定すると、アプリから更新を確認できます。

リリースには `SHA256SUMS.txt` も添付します。ダウンロードが途中で切れたファイルは「このアプリはお使いのPCで実行できません」と表示されて起動しないため、実行前にハッシュの一致を確認してください。

```powershell
Get-FileHash .\KakomonTrainer-Setup.exe -Algorithm SHA256
```

## 構成

| パス | 内容 |
| --- | --- |
| `KakomonTrainer.sln` | Visual Studio用ソリューション |
| `desktop/KakomonTrainer/MainWindow.xaml` | WPFの用紙・編集・確認・結果・設定画面 |
| `desktop/KakomonTrainer/PdfService.cs` | PDFiumのCPUレンダリング、印刷用PDF・赤入れPDF |
| `desktop/KakomonTrainer/ScanService.cs` | OpenCVによるQR・マーカー検出と位置補正 |
| `desktop/KakomonTrainer/AiService.cs` | OCRと採点API |
| `desktop/KakomonTrainer/GradingService.cs` | 数値照合、採点状態管理、復習ログ |
| `desktop/KakomonTrainer/DataStore.cs` | 既存JSON互換、入力検証、保存 |
| `desktop/KakomonTrainer.Checks` | ネイティブ処理の結合チェック |

旧Python実装とそのテストは比較・移行用に残しています。新アプリと配布物からは使用しません。旧版の説明は [docs/legacy-python.md](docs/legacy-python.md) に保存しています。共通の採点プロンプトは `app/prompts/` から発行時にコピーします。

同梱のBIZ UDゴシックは [Google Fonts / Morisawa BIZ UD Gothic](https://github.com/googlefonts/morisawa-biz-ud-gothic) のフォントです。SIL Open Font License 1.1の全文を `desktop/KakomonTrainer/Assets/Fonts/OFL.txt` に同梱しています。

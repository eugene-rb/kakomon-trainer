# 模試自動採点システム

紙の模試・問題集の解答用紙をスキャンし、OCR と LLM で自動採点・添削する。
出力は **赤入れ済み PDF** と **復習用ログ**（JSONL + Markdown）。
シングルユーザー・ローカル実行（`127.0.0.1` バインド固定）。

## セットアップ

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# OCR に Azure Document Intelligence を使う場合のみ
# pip install -r requirements-optional.txt

copy .env.example .env    # 値を埋める（下記）
```

### `.env` の必須項目

| 項目 | 説明 |
|---|---|
| `GOOGLE_APPLICATION_CREDENTIALS` | GCP サービスアカウント JSON のパス。既定は `./secrets/gcp-vision-credentials.json` |
| `GRADING_PROVIDER` | `anthropic` / `openai` / `kimi` / `openai_compatible` |
| `<PROVIDER>_API_KEY` | 採点に使うプロバイダの API キー |
| `<PROVIDER>_MODEL` | 使用モデル名（プロバイダ別変数が共通の `GRADING_MODEL` より優先される） |

**OCR の認証情報は起動時に必須**（無ければ起動を中止する）。
**採点用の API キーは起動をブロックしない** — 未設定なら起動時に警告を出し、
記述式設問の採点実行時に HTTP 503 と日本語メッセージで返す。キーが無くても
テンプレート作成〜OCR 転記までは使える。**「答えのみ」設問だけのテンプレートは
採点キー無しでも採点できる**（決定的な照合で完結するため）。

### 起動

```powershell
python -m app.main
# または: uvicorn app.main:app --host 127.0.0.1 --port 8765
```

ブラウザで http://127.0.0.1:8765/ を開く。

## 使い方

### ファイル名で用紙と解答解説をまとめて登録

詳細な動作・命名規則・API・受け入れ条件は、[解答用紙登録・解答解説自動対応付け仕様書](docs/answer-sheet-registration.md)を参照してください。

ホームの「解答用紙を登録」で、用紙と解説をまとめて選択・ドロップできます。
ファイル名は半角英数字と `_` / `-` / `.` を使い、共通名の末尾に役割を付けます。

| ファイル名の例 | 役割 |
|---|---|
| `kyodai_english_2024_sheet.pdf` | 白紙の解答用紙 |
| `kyodai_english_2024_answers.pdf` | 解答解説 |
| `kyodai_english_2024_rubric.md` | 採点基準 |

共通名が一致する資料だけを対応付けます。大文字・小文字は区別しません。
解説と採点基準は PDF / Markdown / テキストに対応します。用紙だけの登録や、
役割の末尾を付けない単独PDF（`exam.pdf`）の登録もできます。
複数組を選ぶと対応関係が表示され、登録前に確認できます。対応先のない資料や
重複した用紙は登録前にエラーになります。一部のPDFの登録が失敗した場合は、
成功した用紙を残して失敗分だけ再試行できます。

対応付けた資料は、領域エディタで設問を追加するときに自動選択されます。
設問ごとの参照資料チェックは変更できます。設問番号・ページごとの自動切り分けは
行わず、用紙全体の解説として対応付けます。同じ名前の用紙を再登録すると別の
管理用IDを自動生成し、既存の用紙は上書きしません。

### 採点までの流れ

1. **テンプレート作成** — 白紙の解答用紙 PDF をアップロードすると、左上・右上・左下の
   ArUco マーカー 3 個と右下のページ識別用 QR（この 4 隅も位置合わせに使う）を焼き込んだ
   `blank.pdf` が生成される。本文はマーカー・QR と被らないよう一回り縮小配置される。
   **これを印刷して使う。**
2. **領域指定** — エディタで設問ごとに解答欄をドラッグ指定し、配点・解答言語・
   参照資料（模範解答／採点基準）を設定して保存。**採点形式**を設問タイプごとに選ぶ：
   *答えのみ*（正答を入力しておき、数値の許容誤差・表記ゆれを吸収した決定的照合で自動採点。
   曖昧なケースだけ Haiku にフォールバック）／*記述・論述*／*和訳*・*英訳*・*自由英作文*／
   *証明・数式記述*／*計算・導出*・*論述・理由説明*／*グラフ・作図*・*構造式*。
   形式ごとに専用の採点観点（`app/prompts/criteria/`）が共通プロンプトへ連結される。
   和訳・英訳・自由英作文は解答言語が自動で固定される。
3. **答案をスキャン** — 記入済み用紙を 300dpi 以上でスキャンして投入。
   QR でテンプレートとページを自動判別し、ページ順が乱れていても並べ替える。
4. **転記確認** — OCR 結果を目視で修正する。**転記と採点は別ステップ**にしてあるので、
   転記ミスと実力不足が混ざらない。未記入の設問はチェックすると 0 点でスキップされる。
5. **採点実行** — LLM 採点の形式は設問ごとに 1 リクエストで投げる。答えのみは決定的照合で
   その場で採点する。グラフ・作図・構造式は答案画像を一次資料に採点する。
   `graded.pdf` と復習ログが出力される。

## 位置合わせの確認

印刷 → 記入 → スキャンした実物で、正規化のズレを測れる。

```powershell
# 実物のスキャンで確認
python scripts/check_alignment.py <template_id> <scanned.pdf>

# 印刷せずパイプラインの健全性だけ確認（合成データ）
python scripts/check_alignment.py <template_id> --synthetic
```

300dpi で最大ズレ数 px 以内が目安（`alignment_error_px` は ArUco 中心と QR 隅の残差の max。
QR 隅は検出ノイズが数 px 乗る）。大きく超える場合はスキャン解像度・四隅の切れ・影や折れを確認する。

## テスト

```powershell
pytest                    # 外部 API を叩くテストは既定で skip
pytest -m integration     # 実 API を使うテスト（キー設定が必要）
```

画面ロジックの回帰テストは Node.js 24系（24.15 以上）で実行できます。DOM 上で保存・採点の
二重操作、通信失敗からの復帰、未保存の編集、領域エディタの選択を確認します。

```powershell
npm ci --prefix tests/frontend
npm test --prefix tests/frontend
```

転記を変更して保存すると、採点済みセッションは転記確認待ちに戻ります。修正後の
結果・赤入れ PDF は再採点後に表示されます。採点失敗時は転記を保持し、画面から
再試行できます。使用中のテンプレートは、セッションの参照に必要なため削除できません。

## 構成

| パス | 役割 |
|---|---|
| `app/geometry.py` | 4 系統の座標（正規化 / キャンバス実px / 切り出し内px / PDFpt）の型と変換、ホモグラフィ |
| `app/markers.py` | ArUco 3 個・右下 QR の生成と検出（QR は 4 隅の座標も返す） |
| `app/template_builder.py` | `blank.pdf` 生成（本文縮小配置）とマーカー中心・QR 隅の実測 |
| `app/scanner.py` | 正規化 → QR 判定 → 領域切り出し → OCR |
| `app/ocr/` | OCR バックエンド抽象化（google_vision / azure_di / claude_vision） |
| `app/llm/` | 採点バックエンド抽象化（anthropic / openai 互換）と共通ツール定義。`answer_check.py` は答えのみ設問の Haiku フォールバック |
| `app/answer_match.py` | 答えのみ設問の決定的照合（数値の許容誤差・分数・指数・単位除去・文字列正規化） |
| `app/textnorm.py` | 突き合わせ用の文字列正規化（annotator と answer_match で共有） |
| `app/prompts/criteria/` | 採点形式ごとの観点（和訳・英訳・自由英作文・証明・計算・グラフ作図・構造式）。共通プロンプトに連結 |
| `app/grader.py` | 設問ごとの採点、リトライ、スコア整合性の補正、採点形式（answer_only / llm_text / llm_visual）の分岐 |
| `app/annotator.py` | 引用 → 単語 bbox の突き合わせと赤入れ描画 |
| `app/logger.py` | JSONL / Markdown ログと集計 |
| `data/` | テンプレート・スキャン・結果・ログ（`.gitignore` 済み） |

### 設計上の要点

- **座標系は 4 つある。** 仕様上は 3 つだが、赤入れ時に PDF ポイント空間への変換が入る。
  各系は `NewType` で分離し、生成は必ず検証付きファクトリ（`make_norm_rect` など）を通す。
- **ArUco の検出順は ID 順ではない。** `detectMarkers()` は検出順に返す（実測で `[3,1,0]`）。
  `detect_aruco_markers()` が `dict[int, CanvasPx]` を返すのは、index で対応付けて
  「エラーは出ないが誤ったホモグラフィ」になる事故を構造的に防ぐため。
- **位置合わせは 2 段。** ArUco は 3 個しかなく透視ホモグラフィ（4 点必要）に足りない。
  まず 3 ArUco のアフィン変換で粗く正規化 → その画像で右下 QR を読み、QR の 4 隅を
  逆アフィンで生画像座標へ戻す → 3 ArUco 中心 + 4 QR 隅 = 7 点で `findHomography`
  （全点最小二乗。QR 隅を RANSAC で捨てると 3 点に痩せて不安定になる）→ 生画像を一発で最終正規化。
  QR が読めなければアフィン結果を暫定採用し `qr_detected=False` を立てる。
- **切り出し原点は記録する。** ページ端ではマージンがクランプされるため、
  `rect - margin` から逆算すると位置がずれる。実測原点を `session.json` に残して使う。
- **Anthropic には `temperature` を送らない。** 現行の Claude モデルではこのパラメータが
  削除されており、送ると 400 になる。OpenAI 互換側では従来どおり `temperature=0` を送る。
- **OpenAI 互換に PDF は直接渡せない。** chat completions のコンテンツパートに PDF が無いため、
  参照資料の PDF はページ画像に変換して送る。Anthropic だけネイティブの document ブロックを使う。
- **答えのみ設問は原則 LLM を呼ばない。** `answer_match.judge()` が数値（許容誤差つき）と
  文字列（正規化一致）で ○×を確定させる。数値は必ず決着する。文字列は「近いが不一致」だけ
  `uncertain` にし、フォールバックが有効なら Haiku、無効なら confidence=low ＋ 警告で人手確認へ回す。
  採点モデルは設問ごとに `ResultQuestion.grader` / `.model` に記録する（記述式=Opus と
  混在しても `Result.model` が嘘にならないように）。
- **採点観点は設問タイプごとに切り替える。** `Question.answer_format`（`app/models.py` の
  `ANSWER_FORMAT_META` が唯一の情報源）で採点経路を決め、`app/prompts/criteria/<値>.md` を
  共通プロンプトへ連結する。旧 `written` は `essay` に読み替え（`SCHEMA_VERSION` 据え置き）。
- **グラフ・作図・構造式は答案画像で採点する。** OCR 転記は当てにせず、`quote=""` の指摘を
  `annotator` の領域枠フォールバックに載せる。答案画像が無い場合は LLM を呼ばず警告で止める。
# Windows 配布版

`v0.1.0` のようなタグを GitHub に push すると、GitHub Actions が単一ファイルの
`KakomonTrainer-Setup.exe` を GitHub Releases に公開します。利用者はこのインストーラーを
実行するだけでよく、Python は不要です。

初回インストール時に `%APPDATA%\KakomonTrainer\.env` が作成されます。API キーと公開先を
ここで指定します。テンプレート、スキャン、結果も `%APPDATA%\KakomonTrainer\data` に保存されるため、
更新・アンインストールで失われません。

配布版は窓を持たないため、起動すると既定のブラウザで UI を開きます。OCR の認証情報が未設定・
不正なときは無言で終了せず、`.env` の場所を示すダイアログを出してから終了します。起動済みの
状態でもう一度起動した場合は、二重起動せず既存の UI をブラウザで開き直します。

```dotenv
UPDATE_REPOSITORY=GitHubユーザー名/リポジトリ名
```

この設定後、`GET /api/update` は GitHub Releases を確認し、`POST /api/update/install` は
最新版のインストーラーをダウンロードして終了後にサイレント更新します。

ローカルでのリリースビルドには Inno Setup 6 が必要です。

```powershell
pip install -r requirements-build.txt
.\scripts\build_windows.ps1 -Version 0.1.0
```

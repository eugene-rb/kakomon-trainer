[CmdletBinding()]
param(
  [string]$Version = "0.1.0",
  [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Version = $Version.TrimStart("v")
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw "Version は 0.1.0 形式で指定してください。" }

# Windows PowerShell 5.1 の Set-Content -Encoding utf8 は BOM を付ける。
# 元ファイルは BOM なしなので、実行環境によって差分が出ないよう常に BOM なしで書き戻す。
function Set-TextFile([string]$Path, [string]$Text) {
  [System.IO.File]::WriteAllText((Join-Path $Root $Path), $Text, (New-Object System.Text.UTF8Encoding $false))
}

# The running application must carry the same version as the GitHub tag.
$versionFile = "app\version.py"
$versionSource = Get-Content $versionFile -Raw
$versionSource = $versionSource -replace '__version__ = "[^"]+"', "__version__ = `"$Version`""
Set-TextFile $versionFile $versionSource

# EXE のプロパティ欄に出るバージョンもタグに合わせる（更新判定は app/version.py を見る）。
$parts = $Version.Split(".")
$tuple = "$($parts[0]), $($parts[1]), $($parts[2]), 0"
$infoFile = "packaging\version_info.txt"
$infoSource = Get-Content $infoFile -Raw
$infoSource = $infoSource -replace 'filevers=\([\d, ]+\)', "filevers=($tuple)"
$infoSource = $infoSource -replace 'prodvers=\([\d, ]+\)', "prodvers=($tuple)"
$infoSource = $infoSource -replace "StringStruct\('FileVersion', '[^']+'\)", "StringStruct('FileVersion', '$Version')"
$infoSource = $infoSource -replace "StringStruct\('ProductVersion', '[^']+'\)", "StringStruct('ProductVersion', '$Version')"
Set-TextFile $infoFile $infoSource

# ネイティブコマンドの失敗は $ErrorActionPreference では止まらないので、明示的に確認する。
if (-not $SkipTests) {
  python -m pytest
  if ($LASTEXITCODE -ne 0) { throw "テストが失敗したためビルドを中止します。" }
}
python -m PyInstaller --noconfirm --clean kakomon_trainer.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller のビルドに失敗しました。" }
$env:APP_VERSION = $Version
$iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
  $candidate = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
  if (Test-Path $candidate) { $iscc = $candidate }
}
if (-not $iscc) { throw "Inno Setup 6 の ISCC.exe が見つかりません。https://jrsoftware.org/isinfo.php からインストールしてください。" }
& $iscc packaging\KakomonTrainer.iss
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Created release\KakomonTrainer-Setup.exe"

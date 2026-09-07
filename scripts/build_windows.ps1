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

# The running application must carry the same version as the GitHub tag.
$versionFile = "app\version.py"
$versionSource = Get-Content $versionFile -Raw
$versionSource = $versionSource -replace '__version__ = "[^"]+"', "__version__ = `"$Version`""
Set-Content -Path $versionFile -Value $versionSource -Encoding utf8

if (-not $SkipTests) { python -m pytest }
python -m PyInstaller --noconfirm --clean kakomon_trainer.spec
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

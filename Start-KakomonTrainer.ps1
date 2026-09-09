$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$executable = Join-Path $projectRoot "artifacts\desktop\KakomonTrainer.exe"
if (-not (Test-Path -LiteralPath $executable)) {
    dotnet publish (Join-Path $projectRoot "desktop\KakomonTrainer") -c Release -r win-x64 --self-contained true -o (Join-Path $projectRoot "artifacts\desktop")
    if ($LASTEXITCODE -ne 0) { throw "WPF build failed." }
}
Start-Process -FilePath $executable -WorkingDirectory $projectRoot

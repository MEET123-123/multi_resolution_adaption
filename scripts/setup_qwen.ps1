param([string]$Python = '.venv\Scripts\python.exe', [string]$Proxy = '')
$ErrorActionPreference='Stop'
$projectRoot=Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
New-Item -ItemType Directory -Force -Path artifacts/env-tmp | Out-Null
$env:TEMP=(Resolve-Path artifacts/env-tmp).Path
$env:TMP=$env:TEMP
if (-not (Test-Path -LiteralPath '.venv-ml/Scripts/python.exe')) {
    & $Python -m venv .venv-ml
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create ML environment' }
}
$mlPython=Join-Path $projectRoot '.venv-ml/Scripts/python.exe'
$proxyArguments=@()
if ($Proxy) { $proxyArguments=@('--proxy',$Proxy) }
& $mlPython -m pip install torch==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu128 --cache-dir artifacts/pip-cache @proxyArguments
if ($LASTEXITCODE -ne 0) { throw 'Cannot install CUDA torch' }
& $mlPython -m pip install -r requirements-ml.txt --cache-dir artifacts/pip-cache @proxyArguments
if ($LASTEXITCODE -ne 0) { throw 'Cannot install Qwen dependencies' }
$env:PYTHONPATH=Join-Path $projectRoot 'src'
& $mlPython -m multires_app.learning.qwen_runner doctor --out artifacts/learning/environment.json

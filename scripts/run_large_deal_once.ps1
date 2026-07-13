$ErrorActionPreference = "Stop"

Set-Location -LiteralPath "E:\CS-APP"
$env:PYTHONIOENCODING = "utf-8"

& "E:\PythonEnvs\.venv\Scripts\python.exe" "E:\CS-APP\src\large_deal_runner.py" --once
exit $LASTEXITCODE

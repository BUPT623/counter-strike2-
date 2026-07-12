$ErrorActionPreference = "Stop"

Set-Location -LiteralPath "E:\CS-APP"
$env:PYTHONIOENCODING = "utf-8"

& "E:\PythonEnvs\.venv\Scripts\python.exe" "E:\CS-APP\src\automation_runner.py" --once
exit $LASTEXITCODE

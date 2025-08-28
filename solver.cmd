@echo off
setlocal
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
set "SOLVER=%~dp0external\solver\solver.py"
if "%~1"=="" (
  echo Usage: %~nx0 path\to\container.json [args...]
  exit /b 1
)
for %%I in ("%~1") do set "CONTAINER=%%~fI"
shift
pushd "%~dp0external\solver"
"%PY%" "%SOLVER%" "%CONTAINER%" %*
set "EC=%ERRORLEVEL%"
popd
exit /b %EC%

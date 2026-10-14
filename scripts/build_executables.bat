@echo off
REM CLI / GUI の standalone executable を PyInstaller で dist\ に生成する（Windows / cmd.exe）。
setlocal

set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%.."
set "REPO_ROOT=%CD%"

set "PYTHON=%REPO_ROOT%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

echo Using Python: %PYTHON%
"%PYTHON%" -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
  echo PyInstaller not found. Install with: "%PYTHON%" -m pip install -e ".[build]"
  popd
  exit /b 1
)

"%PYTHON%" -m PyInstaller --noconfirm --clean packaging\copilot-usage.spec
if errorlevel 1 ( popd & exit /b 1 )
"%PYTHON%" -m PyInstaller --noconfirm --clean packaging\copilot-usage-gui.spec
if errorlevel 1 ( popd & exit /b 1 )

REM 自己完結 CLI バンドルを dist\copilot-usage-cli\ に組み立てる。
set "BUNDLE_DIR=%REPO_ROOT%\dist\copilot-usage-cli"
echo.
echo Assembling CLI bundle: dist\copilot-usage-cli\
if exist "%BUNDLE_DIR%" rmdir /s /q "%BUNDLE_DIR%"
mkdir "%BUNDLE_DIR%"
copy /y "%REPO_ROOT%\dist\copilot-usage.exe" "%BUNDLE_DIR%\copilot-usage.exe" >nul
copy /y "%REPO_ROOT%\scripts\run_pipeline.sh" "%BUNDLE_DIR%\run_pipeline.sh" >nul
copy /y "%REPO_ROOT%\scripts\run_pipeline.ps1" "%BUNDLE_DIR%\run_pipeline.ps1" >nul
copy /y "%REPO_ROOT%\packaging\public\cli_bundle_README.md" "%BUNDLE_DIR%\README.md" >nul

echo.
echo Built executables in dist\:
dir /b "%REPO_ROOT%\dist"
echo.
echo Built CLI bundle in dist\copilot-usage-cli\:
dir /b "%BUNDLE_DIR%"
popd
endlocal

@echo off
REM
REM Start the Whisper STT server with automatic venv activation.
REM
REM Usage:
REM   start-whisper.bat
REM   start-whisper.bat --port 8030
REM   start-whisper.bat --model distil-large-v3
REM
REM This script will:
REM   1. Look for a Python virtual environment in .\venv
REM   2. Activate the venv
REM   3. Run the Whisper server
REM
REM Prerequisites:
REM   - PyTorch installed (with CUDA support recommended for large models)
REM   - pip install -r requirements-whisper.txt
REM

setlocal enabledelayedexpansion

REM Get the directory where this script is located
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

set "VENV_DIR=%SCRIPT_DIR%venv"

REM Check if venv exists
if not exist "%VENV_DIR%" (
    echo ERROR: Virtual environment not found at: %VENV_DIR%
    echo.
    echo Please create a virtual environment first:
    echo   cd %SCRIPT_DIR%
    echo   python -m venv venv
    echo   venv\Scripts\activate
    echo   pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu118
    echo   pip install -r requirements-whisper.txt
    echo.
    exit /b 1
)

REM Check for activation script
if not exist "%VENV_DIR%\Scripts\activate.bat" (
    echo ERROR: Cannot find venv activation script
    echo Expected: %VENV_DIR%\Scripts\activate.bat
    exit /b 1
)

REM Activate the virtual environment
echo Activating virtual environment: %VENV_DIR%
call "%VENV_DIR%\Scripts\activate.bat"

REM Verify we're in the venv
if "%VIRTUAL_ENV%"=="" (
    echo ERROR: Failed to activate virtual environment
    exit /b 1
)

echo Using Python:
where python
echo.

REM Run the Whisper server, passing through any arguments
python run_whisper.py %*

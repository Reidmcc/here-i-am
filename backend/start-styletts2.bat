@echo off
REM
REM Start the StyleTTS 2 TTS server with automatic venv activation.
REM
REM Usage:
REM   start-styletts2.bat
REM   start-styletts2.bat --port 8021
REM
REM This script will:
REM   1. Look for a Python virtual environment in .\venv
REM   2. Activate the venv
REM   3. Run the StyleTTS 2 server
REM
REM Prerequisites:
REM   - PyTorch installed (with CUDA support recommended)
REM   - pip install -r requirements-styletts2.txt
REM   - espeak-ng installed (if using espeak phonemizer)
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
    echo   pip install -r requirements-styletts2.txt
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

REM Run the StyleTTS 2 server, passing through any arguments
python run_styletts2.py %*

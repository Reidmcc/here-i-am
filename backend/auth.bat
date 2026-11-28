@echo off
REM
REM Set the login password and manage machine tokens for the Here I Am
REM server, using the server's own virtual environment.
REM
REM Usage:
REM   auth.bat init --entity <entity>
REM   auth.bat token create --name <machine> --entity <entity>
REM   auth.bat token list
REM   auth.bat status
REM   auth.bat --help
REM
REM The same as "python -m app.auth ..." with .\venv active. See
REM docs\authentication.md.
REM

setlocal

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

set "VENV_DIR=%SCRIPT_DIR%venv"

if not exist "%VENV_DIR%\Scripts\activate.bat" (
    echo ERROR: Virtual environment not found at: %VENV_DIR%
    echo Create it first ^(see start.bat^), then run this again.
    exit /b 1
)

call "%VENV_DIR%\Scripts\activate.bat"

python -m app.auth %*

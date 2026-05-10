@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_CMD="
where python >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    set "PYTHON_CMD=python"
) else (
    where py >nul 2>nul
    if %ERRORLEVEL% EQU 0 (
        set "PYTHON_CMD=py -3"
    )
)

if "%PYTHON_CMD%"=="" (
    echo 未检测到 Python。请先安装 Python 3.11+，并确保 python 或 py 命令可用。
    echo.
    pause
    exit /b 1
)

%PYTHON_CMD% scripts\manage_teacher_trial_data.py %*
set "EXIT_CODE=%ERRORLEVEL%"
echo.
pause
exit /b %EXIT_CODE%

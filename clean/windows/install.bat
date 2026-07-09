@echo off
setlocal enabledelayedexpansion

:: WIT Class Scheduler — Windows installer
:: Downloads the project code and installs its Python dependencies into a
:: private virtual environment. Run this once; afterwards use run.bat.

set "REPO_URL=https://github.com/mukherjeea1atwit/course-scheduler.git"
set "INSTALL_DIR=%USERPROFILE%\WIT-Class-Scheduler"
set "APP_DIR=%INSTALL_DIR%\course-scheduler\clean"

echo ============================================
echo   WIT Class Scheduler - Installer
echo ============================================
echo.

:: ── Check for Python ────────────────────────────────────────────────────────
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found on this computer.
    echo.
    echo Please install Python 3.10 or newer from https://www.python.org/downloads/
    echo During setup, check the box "Add python.exe to PATH", then re-run this installer.
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo Found Python %PYVER%

:: ── Check for git; fall back to a zip download if missing ──────────────────
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"

where git >nul 2>nul
if errorlevel 1 (
    echo Git not found - downloading the project as a zip instead...
    powershell -NoProfile -Command ^
        "Invoke-WebRequest -Uri 'https://github.com/mukherjeea1atwit/course-scheduler/archive/refs/heads/main.zip' -OutFile '%INSTALL_DIR%\repo.zip'; Expand-Archive -Path '%INSTALL_DIR%\repo.zip' -DestinationPath '%INSTALL_DIR%' -Force"
    if errorlevel 1 (
        echo [ERROR] Download failed. Check your internet connection and try again.
        pause
        exit /b 1
    )
    del "%INSTALL_DIR%\repo.zip"
    for /d %%d in ("%INSTALL_DIR%\course-scheduler-*") do ren "%%d" "course-scheduler"
) else (
    if exist "%INSTALL_DIR%\course-scheduler\.git" (
        echo Project already downloaded - updating to the latest version...
        pushd "%INSTALL_DIR%\course-scheduler"
        git pull
        popd
    ) else (
        echo Downloading project code...
        git clone "%REPO_URL%" "%INSTALL_DIR%\course-scheduler"
        if errorlevel 1 (
            echo [ERROR] git clone failed. Check your internet connection and try again.
            pause
            exit /b 1
        )
    )
)

if not exist "%APP_DIR%\server.py" (
    echo [ERROR] Could not find server.py in the downloaded project.
    pause
    exit /b 1
)

:: ── Create virtual environment ──────────────────────────────────────────────
echo.
echo Setting up a private Python environment...
python -m venv "%APP_DIR%\venv"
if errorlevel 1 (
    echo [ERROR] Failed to create the virtual environment.
    pause
    exit /b 1
)

:: ── Install dependencies ─────────────────────────────────────────────────────
echo Installing required packages ^(this may take a minute^)...
"%APP_DIR%\venv\Scripts\python.exe" -m pip install --upgrade pip >nul
"%APP_DIR%\venv\Scripts\python.exe" -m pip install -r "%APP_DIR%\requirements.txt"
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)

:: ── Drop a Start-Scheduler shortcut on the Desktop ──────────────────────────
set "RUN_BAT=%APP_DIR%\windows\run.bat"
set "SHORTCUT=%USERPROFILE%\Desktop\WIT Class Scheduler.lnk"
powershell -NoProfile -Command ^
    "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('%SHORTCUT%'); $s.TargetPath = '%RUN_BAT%'; $s.WorkingDirectory = '%APP_DIR%'; $s.IconLocation = '%SystemRoot%\System32\shell32.dll,220'; $s.Save()"

echo.
echo ============================================
echo   Install complete!
echo ============================================
echo A "WIT Class Scheduler" shortcut was added to your Desktop.
echo Double-click it any time to start the scheduler.
echo.
pause

@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 (
        pause
        exit /b 1
    )
    py -3 app.py
) else (
    python -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 (
        pause
        exit /b 1
    )
    python app.py
)
if errorlevel 1 pause

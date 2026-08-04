@echo off
title SSL Pinning Bypass Web Manager
cd /d "%~dp0"

echo ==================================================
echo   SSL Pinning Bypass Manager - Web Server Setup
echo ==================================================
echo.

:: Check Flask dependency
python -c "import flask" 2>nul
if %errorlevel% neq 0 (
    echo [!] Flask bulunamadi. Yukleniyor...
    python -m pip install flask
    if %errorlevel% neq 0 (
        echo [-] Flask yuklenemedi! Lutfen internet baglantinizi ve python/pip kurulumunuzu kontrol edin.
        pause
        exit /b 1
    )
    echo [+] Flask basariyla yuklendi!
) else (
    echo [+] Flask zaten kurulu.
)

echo.
echo [+] Web sunucusu baslatiliyor (http://127.0.0.1:5000)...
echo [+] Tarayici otomatik olarak acilacaktir.
echo.

:: Open browser in background after 2 seconds
start "" http://127.0.0.1:5000

:: Run the flask server
python app.py
pause

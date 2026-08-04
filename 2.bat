@echo off
echo ========================================
echo Instagram SSL Bypass - Test Minimal
echo ========================================
F:
frida -U -l .\nev1.js -f com.instagram.android
pause

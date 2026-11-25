@echo off
rem OpenHoop - installazione / installation (Windows 10/11). Si puo' rilanciare: completa solo quello che manca.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0strumenti\installa.ps1"
echo.
pause

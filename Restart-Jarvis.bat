@echo off
cd /d "%~dp0"
echo Stopping any running Jarvis instances...
taskkill /f /im pythonw.exe >nul 2>nul
timeout /t 1 /nobreak >nul
echo Starting Jarvis Liquid Spatial UI...
start "" pythonw.exe "%~dp0gui.py"
echo Jarvis started! You can press Ctrl+Alt+J to show/hide the popup.

@echo off
rem Double-click to start the AI graphics server. Runs the PowerShell script without opening it in Notepad.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-server.ps1"
pause

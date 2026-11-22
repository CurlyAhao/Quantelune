@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\workspace-launch.ps1" -Stop
if errorlevel 1 pause

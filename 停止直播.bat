@echo off
chcp 65001 >nul
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0scripts\stop_live.ps1"

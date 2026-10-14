@echo off
chcp 65001 >nul
title HOI4 Province Editor - Web Console
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set "PYEXE="
py -3.13 -c "import sys" >nul 2>nul
if not errorlevel 1 set "PYEXE=py -3.13"
if defined PYEXE goto :run
python -c "import sys" >nul 2>nul
if not errorlevel 1 set "PYEXE=python"
if defined PYEXE goto :run
py -c "import sys" >nul 2>nul
if not errorlevel 1 set "PYEXE=py"
if defined PYEXE goto :run
echo.
echo   [ERROR] Python 3.9+ not found.
echo   Please install Python ^(tick "Add to PATH"^), then run this file again.
echo.
pause
exit /b 1
:run
%PYEXE% "webui\webui_launch.py" %*
echo.
pause

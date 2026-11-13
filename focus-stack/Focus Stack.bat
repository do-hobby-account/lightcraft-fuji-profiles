@echo off
rem Drag the frames of one focus stack (or a folder holding only them) onto this file.
if "%~1"=="" (
  echo Drag the frames of one focus stack onto this file.
  pause
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" "%~dp0focus_stack.py" %*
echo.
pause

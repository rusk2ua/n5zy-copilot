@echo off
REM N5ZY Co-Pilot Launcher for Windows

echo Starting N5ZY VHF Contest Co-Pilot...
echo.

REM Activate virtual environment if it exists
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
)

REM Run the application
python copilot.py

REM Keep window open if there's an error
if errorlevel 1 (
    echo.
    echo Error occurred! Press any key to exit...
    pause >nul
)

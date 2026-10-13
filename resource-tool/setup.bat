@echo off
REM ShelterApp Resource Tool - one-time setup for Windows
echo ============================================================
echo   ShelterApp Resource Tool - Setup
echo ============================================================
echo.

where py >nul 2>nul
if errorlevel 1 (
  echo [!] Python launcher "py" was not found.
  echo     Install Python 3.11+ from https://www.python.org/downloads/
  echo     and be sure to check "Add Python to PATH" during install.
  echo.
  pause
  exit /b 1
)

echo Installing Python packages (playwright, openpyxl)...
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo.
echo Downloading the browser Playwright needs (first time only, a few minutes)...
py -m playwright install chromium
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo   Setup complete!
echo.
echo   Next step: open google_places_key.txt and paste in your
echo   Google Places API key (see README.md for how to get one).
echo.
echo   Then run:   py run.py
echo ============================================================
echo.
pause
exit /b 0

:fail
echo.
echo [!] Something went wrong during setup. Scroll up to see the error,
echo     or send README.md's Troubleshooting section to your helper.
echo.
pause
exit /b 1

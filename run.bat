@echo off
cd /d "%~dp0"
echo ============================================================
echo   AI Interior Design Agent - starting up
echo ============================================================
echo.
echo Installing dependencies (first run only, may take 1-3 min)...
echo.

python -m pip install -r requirements.txt --timeout 180 --retries 20
if errorlevel 1 goto trypy

echo.
echo Launching the app in your browser...
python -m streamlit run app.py
goto end

:trypy
echo.
echo Retrying with the "py" launcher (this also covers download timeouts)...
py -m pip install -r requirements.txt --timeout 180 --retries 20
if errorlevel 1 goto installfailed
py -m streamlit run app.py
goto end

:installfailed
echo.
echo ============================================================
echo   Install did not finish (often a slow/dropped download).
echo   Just CLOSE this window and double-click run again -
echo   already-downloaded packages are cached, so it resumes
echo   and only fetches what is left.
echo   (If it says Python is not installed, get it from
echo    https://www.python.org/downloads/ and TICK "Add to PATH".)
echo ============================================================

:end
echo.
pause

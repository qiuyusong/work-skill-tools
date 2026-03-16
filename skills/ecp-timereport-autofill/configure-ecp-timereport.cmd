@echo off
setlocal
set SCRIPT_DIR=%~dp0
python "%SCRIPT_DIR%scripts\configure_timereport.py" --interactive
if errorlevel 1 (
  echo.
  echo Failed to update ECP timereport config.
  exit /b 1
)
echo.
echo ECP timereport config updated.
endlocal

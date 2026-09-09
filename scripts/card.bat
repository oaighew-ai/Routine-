@echo off
REM Build this week's card from whatever opens have been captured.
REM     scripts\card.bat 2026 3

setlocal
cd /d "%~dp0.."
set SEASON=%1
set WEEK=%2
if "%SEASON%"=="" set SEASON=2026
if "%WEEK%"=="" (
  echo usage: scripts\card.bat SEASON WEEK
  exit /b 1
)

echo Rebuilding the slate with the latest results...
python -m cfb_edge.slate --season %SEASON% --week %WEEK% --out data\week%WEEK%_slate.csv || exit /b 1

echo.
echo Rebuilding opening lines from the raw capture...
python -m cfb_edge.watch --log data\opens.jsonl.gz --rebuild --out data\opens.csv || exit /b 1

echo.
python -m cfb_edge play --slate data\week%WEEK%_slate.csv --opens data\opens.csv --book-price -105

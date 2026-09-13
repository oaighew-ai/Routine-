@echo off
REM Build this week's card from whatever opens have been captured.
REM     scripts\card.bat 2026 3
REM
REM The week number is not decoration. Weeks 1 and 2 produce no plays at all:
REM measured over 1,375 replicated bets they return +0.049 points of closing
REM line value at t = 0.28, against +0.440 at t = 5.31 from week 3 on. This
REM script used to call `play` without --week, which skipped that check
REM entirely and priced an ungated board.

setlocal
cd /d "%~dp0.."
set SEASON=%1
set WEEK=%2
if "%SEASON%"=="" set SEASON=2026
if "%WEEK%"=="" (
  echo usage: scripts\card.bat SEASON WEEK
  echo   e.g. scripts\card.bat 2026 3
  exit /b 1
)

REM The season belongs in the filename. Without it, week 3 of next season
REM overwrites week 3 of this one, and the slate the repository tracks is
REM never the slate this script refreshes.
set SLATE=data\week%WEEK%_%SEASON%_slate.csv

echo Rebuilding the slate with the latest results...
python -m cfb_edge.slate --season %SEASON% --week %WEEK% --out %SLATE% || exit /b 1

echo.
echo Rebuilding opening lines from the raw capture...
python -m cfb_edge.watch --log data\opens.jsonl.gz --rebuild --out data\opens.csv || exit /b 1

echo.
python -m cfb_edge play --slate %SLATE% --opens data\opens.csv --week %WEEK% --book-price -105

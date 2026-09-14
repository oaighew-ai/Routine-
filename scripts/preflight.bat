@echo off
REM Ten seconds that tell you whether the capture will work.
REM
REM     scripts\preflight.bat
REM
REM The capture is a long-running poller. Left to itself, a parser that does not
REM match Kalshi's real field names looks exactly like a quiet board: it runs all
REM night and records nothing, and you find out when the release window has
REM already closed. This does one poll and says plainly which it is.

setlocal enabledelayedexpansion
cd /d "%~dp0.."
set SEASON=%1
set WEEK=%2
set SOURCE=%3
if "%SEASON%"=="" set SEASON=2026
if "%WEEK%"==""   set WEEK=current
if "%SOURCE%"=="" set SOURCE=kalshi
if not exist data mkdir data

echo 1/3  Building the slate (%SEASON%, week %WEEK%)...
python -m cfb_edge.slate --season %SEASON% --week %WEEK% --out data\slate_current.csv
if errorlevel 1 (
  echo.
  echo FAIL: could not build the slate.
  echo   The schedule comes from raw.githubusercontent.com and needs no key.
  exit /b 1
)

echo.
echo 2/3  One poll against %SOURCE%...
python -m cfb_edge.watch --source %SOURCE% --slate data\slate_current.csv ^
  --log data\preflight.jsonl.gz --out data\preflight.csv --once

echo.
echo 3/3  Counting what landed...
set LINES=0
for /f %%A in ('type data\preflight.csv ^| find /c /v ""') do set /a LINES=%%A-1
del /q data\preflight.jsonl.gz data\preflight.csv 2>nul

echo.
if !LINES! GTR 0 (
  echo PASS: !LINES! opening lines parsed from %SOURCE%.
  echo The chain works end to end. Start the real capture:
  echo     scripts\capture.bat
) else (
  echo FAIL: 0 lines parsed.
  echo.
  echo The slate built, so this is the exchange call or the parser, not the
  echo schedule. Send one market's raw JSON to tell those apart:
  echo     curl -s "https://api.elections.kalshi.com/trade-api/v2/markets?limit=3&status=open&series_ticker=KXNCAAFSPREAD"
  exit /b 1
)

@echo off
REM Capture opening lines for college football, continuously.
REM
REM     scripts\capture.bat              Kalshi, free, no key, current week
REM     scripts\capture.bat 2026 3       a specific season and week
REM     scripts\capture.bat 2026 3 oddsapi   sportsbook opens, needs ODDS_API_KEY
REM
REM Run this from Sunday afternoon onward. It polls every five minutes through
REM the release window and hourly outside it, and records the FIRST price it
REM sees for each market. Later polls never overwrite an open.
REM
REM The default source is Kalshi's spread ladder: a public read, no key, and it
REM prices the venue you actually fill on. The line is inverted out of the
REM ladder at the rung where the market makes the game a coin flip.
REM
REM The Kalshi path NEEDS A SLATE. Nothing in a Kalshi market says which team is
REM at home, so the schedule is the only thing that can orient the line. This
REM script builds it. An earlier version did not pass one and the capture
REM refused to start every time it was run.

setlocal
cd /d "%~dp0.."
set SEASON=%1
set WEEK=%2
set SOURCE=%3
if "%SEASON%"=="" set SEASON=2026
if "%WEEK%"==""   set WEEK=current
if "%SOURCE%"=="" set SOURCE=kalshi

if /i "%SOURCE%"=="oddsapi" (
  if "%ODDS_API_KEY%"=="" (
    echo ODDS_API_KEY is not set in this terminal.
    echo   setx ODDS_API_KEY "your-key"   then open a NEW terminal.
    echo Or drop the argument to use Kalshi, which needs no key.
    exit /b 1
  )
)

if not exist data mkdir data

echo Building the slate for %SEASON% week %WEEK%...
python -m cfb_edge.slate --season %SEASON% --week %WEEK% --out data\slate_current.csv || exit /b 1

echo.
echo Capturing from %SOURCE% to data\opens.jsonl.gz  ^(Ctrl+C to stop^)
python -m cfb_edge.watch --source %SOURCE% --slate data\slate_current.csv ^
  --log data\opens.jsonl.gz --out data\opens.csv

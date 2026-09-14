@echo off
REM Capture opening lines for college football, continuously.
REM
REM Run this from Sunday afternoon onward. It polls every five minutes through
REM the release window and hourly outside it, and records the FIRST price it
REM sees for each market. Later polls never overwrite an open.
REM
REM No key is needed. The default source is Kalshi's spread ladder, which is a
REM public read and which prices the venue you actually fill on. The line is
REM inverted out of the ladder: the rung where the market makes the game a coin
REM flip is the line.
REM
REM     scripts\capture.bat            Kalshi, free, no key
REM     scripts\capture.bat oddsapi    sportsbook opens, needs ODDS_API_KEY
REM
REM The oddsapi path bills one credit per region per market: about 1,956 a week
REM and 8,400 a month at this schedule. It also measures a venue you do not
REM trade, which is how a card came to quote a contract the exchange does not
REM list. Prefer the default.

setlocal
cd /d "%~dp0.."
set SOURCE=%1
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
echo Capturing from %SOURCE% to data\opens.jsonl.gz  ^(Ctrl+C to stop^)
python -m cfb_edge.watch --source %SOURCE% --log data\opens.jsonl.gz --out data\opens.csv

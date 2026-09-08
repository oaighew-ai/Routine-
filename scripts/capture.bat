@echo off
REM Capture opening lines for college football, continuously.
REM
REM Run this from Sunday afternoon onward. It polls every five minutes through
REM the release window and hourly outside it, and records the FIRST price it
REM sees for each market. Later polls never overwrite an open.
REM
REM One-time setup:
REM     setx ODDS_API_KEY "your-key-from-the-odds-api.com"
REM   then open a NEW terminal, because setx does not affect the current one.

setlocal
cd /d "%~dp0.."

if "%ODDS_API_KEY%"=="" (
  echo ODDS_API_KEY is not set in this terminal.
  echo   setx ODDS_API_KEY "your-key"   then open a new terminal.
  exit /b 1
)

if not exist data mkdir data
echo Capturing to data\opens.jsonl.gz  ^(Ctrl+C to stop^)
python -m cfb_edge.watch --log data\opens.jsonl.gz --out data\opens.csv

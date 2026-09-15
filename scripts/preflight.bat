@echo off
REM Share the same diagnostic and validation contract as Bash.
setlocal
cd /d "%~dp0.." || exit /b 2
python -m cfb_edge.preflight %*
exit /b %errorlevel%

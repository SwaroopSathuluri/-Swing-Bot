@echo off
cd /d C:\Users\swaro\OneDrive\Desktop\Trading\swing_trading_project
set UNIVERSE=%1
if "%UNIVERSE%"=="" set UNIVERSE=qqq
"C:\Users\swaro\AppData\Local\Programs\Python\Python312\python.exe" mag7_scanner.py %UNIVERSE%

@echo off
chcp 65001 >nul
tasklist | findstr /i "uvicorn" >nul && (echo 服务已在运行 & pause & exit /b)
cd /d %~dp0
py -m uvicorn src.main:app --host 0.0.0.0 --port 7863 || python -m uvicorn src.main:app --host 0.0.0.0 --port 7863
pause
@echo off
cd /d "%~dp0"
title LAN ChatRoom - 打开聊天室

REM 作用：如果服务还没启动，就先在后台把它跑起来，然后打开聊天窗口。
REM      适合做成桌面快捷方式，双击一次全搞定。

REM ---------- 1. 判断服务是否已在运行（看 8000 端口有没有被监听）----------
netstat -ano | findstr ":8000" | findstr "LISTENING" >nul
if errorlevel 1 (
  echo   服务还没启动，正在后台启动...
  if not exist "venv\Scripts\python.exe" (
    echo.
    echo   [提示] 还没装好环境，请先双击 start.bat 完成首次安装。
    echo.
    pause
    exit /b 1
  )
  start "LAN ChatRoom Server" /min cmd /c "venv\Scripts\python.exe" server.py
  echo   等待服务就绪...
  timeout /t 4 /nobreak >nul
)

REM ---------- 2. 打开窗口（优先用 Edge/Chrome 的“应用模式”，没有就用默认浏览器）----------
set "EDGE=C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
set "EDGE2=C:\Program Files\Microsoft\Edge\Application\msedge.exe"
set "CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe"
set "CHROME2=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"

if exist "%EDGE%"  goto open_edge
if exist "%EDGE2%" goto open_edge2
if exist "%CHROME%" goto open_chrome
if exist "%CHROME2%" goto open_chrome2

echo   正在用默认浏览器打开 http://127.0.0.1:8000
start "" "http://127.0.0.1:8000"
exit /b 0

:open_edge
start "" "%EDGE%" --app=http://127.0.0.1:8000 --window-size=1280,860
exit /b 0

:open_edge2
start "" "%EDGE2%" --app=http://127.0.0.1:8000 --window-size=1280,860
exit /b 0

:open_chrome
start "" "%CHROME%" --app=http://127.0.0.1:8000 --window-size=1280,860
exit /b 0

:open_chrome2
start "" "%CHROME2%" --app=http://127.0.0.1:8000 --window-size=1280,860
exit /b 0

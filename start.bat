@echo off
cd /d "%~dp0"
title LAN ChatRoom - 局域网聊天室

echo.
echo   ============================================
echo      局域网聊天室  -  正在启动
echo   ============================================
echo.

REM ---------- 1. 找 Python ----------
set "PY="
where python >nul 2>nul
if not errorlevel 1 set "PY=python"
where py >nul 2>nul
if not errorlevel 1 if not defined PY set "PY=py"

if not defined PY (
  echo   [出错了] 没找到 Python。
  echo.
  echo   请先安装 Python 3.10 或更高版本：
  echo       https://www.python.org/downloads/
  echo   安装时务必勾选 "Add Python to PATH"（添加到环境变量）。
  echo.
  pause
  exit /b 1
)

REM ---------- 2. 首次运行时自动建虚拟环境、装依赖 ----------
if not exist "venv\Scripts\python.exe" (
  echo   首次运行，正在创建虚拟环境...
  %PY% -m venv venv
  if errorlevel 1 (
    echo   [出错了] 虚拟环境创建失败，请把上面的报错截图反馈。
    pause
    exit /b 1
  )

  echo   正在安装依赖，请稍候（约 1 分钟）...
  "venv\Scripts\python.exe" -m pip install -q -r requirements.txt
  if errorlevel 1 (
    echo   官方源太慢或连不上，改用清华镜像重试...
    "venv\Scripts\python.exe" -m pip install -q -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 (
      echo   [出错了] 依赖安装失败，请检查网络后重新双击本脚本。
      pause
      exit /b 1
    )
  )
  echo   依赖安装完成。
  echo.
)

REM ---------- 3. 启动服务 ----------
echo   服务已就绪，正在启动...
echo   这个窗口请保持开着，关掉它聊天室就停了。
echo.

"venv\Scripts\python.exe" server.py

echo.
echo   服务已停止。
pause

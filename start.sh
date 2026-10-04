#!/usr/bin/env bash
# 局域网聊天室 —— Linux / macOS 一键启动脚本
# 用法：双击运行，或在终端里执行  ./start.sh

set -e
cd "$(dirname "$0")"

# 1) 没有虚拟环境就创建一个（首次运行时会执行，之后跳过）
if [ ! -d "venv" ]; then
  echo "首次运行，正在创建虚拟环境..."
  python3 -m venv venv
  echo "正在安装依赖（约 1 分钟）..."
  ./venv/bin/pip install -q -r requirements.txt \
    || ./venv/bin/pip install -q -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
fi

# 2) 启动服务
echo "正在启动聊天室..."
exec ./venv/bin/python server.py

# -*- coding: utf-8 -*-
"""
内网聊天室 - 后端服务
功能：托管页面 + 登录注册 + 多房间 + WebSocket 实时广播 + SQLite 持久化
      + 删除自己的消息 + 管理员清空频道 / 转让管理员

启动方式：python server.py
局域网访问：http://<本机IP>:8000
"""
import asyncio
import hashlib
import hmac
import os
import secrets
import socket
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel

# ===== 集中配置（按需修改）=====
HOST = "0.0.0.0"            # 监听所有网卡，局域网其他电脑才能访问

# 端口号。两种方式改：
#   1) 直接改这里的 8000（如改成 8080）
#   2) 不改代码，启动时设置环境变量，例如 Windows：set CHAT_PORT=8080
PORT = int(os.environ.get("CHAT_PORT", "8000"))
MAX_MESSAGE_LENGTH = 500    # 单条消息最大长度
MAX_HISTORY = 200           # 进入房间时加载的历史消息条数
USERNAME_MIN, USERNAME_MAX = 2, 20   # 账号名长度限制
PASSWORD_MIN = 4                     # 密码最短长度
HASH_ROUNDS = 100_000                # 密码哈希迭代次数
ROOMS = ["闲聊", "工作"]             # 房间列表，想加频道直接往这里加名字

BASE_DIR = Path(__file__).parent          # 项目目录（server.py 所在目录）
DB_PATH = BASE_DIR / "chat.db"            # SQLite 数据库文件，首次运行自动创建

app = FastAPI()

# ===== SQLite 初始化 =====
# 所有 SQL 均使用参数化查询（? 占位符），杜绝 SQL 注入
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,  -- 消息编号
        username   TEXT NOT NULL,                      -- 发言人账号名
        content    TEXT NOT NULL,                      -- 消息内容
        room       TEXT NOT NULL DEFAULT '闲聊',        -- 所属房间
        created_at TEXT NOT NULL                       -- 发送时间
    )
""")
db.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,  -- 账号编号
        username      TEXT NOT NULL UNIQUE,               -- 账号名，唯一
        password_hash TEXT NOT NULL,                      -- 密码哈希（绝不明文存储）
        salt          TEXT NOT NULL,                      -- 每个账号独立的随机盐
        is_admin      INTEGER NOT NULL DEFAULT 0,         -- 是否管理员（1=是）
        created_at    TEXT NOT NULL                       -- 注册时间
    )
""")
# 登录凭证表：把凭证存下来，服务重启后大家的登录状态还在，不用重新输密码
db.execute("""
    CREATE TABLE IF NOT EXISTS tokens (
        token      TEXT PRIMARY KEY,   -- 随机凭证串
        username   TEXT NOT NULL,      -- 属于哪个账号
        created_at TEXT NOT NULL,      -- 登录时间
        expires_at TEXT NOT NULL       -- 过期时间（超过就作废）
    )
""")
# 老数据库升级：如果 messages 表还没有 room 字段，就补上（老消息归入"闲聊"）
cols = [row[1] for row in db.execute("PRAGMA table_info(messages)")]
if "room" not in cols:
    db.execute("ALTER TABLE messages ADD COLUMN room TEXT NOT NULL DEFAULT '闲聊'")
db.commit()
# 数据库操作是同步代码，用普通线程锁（asyncio.Lock 只能配合 async with，会报错）
db_lock = threading.Lock()

# ===== 登录凭证 =====
# 同时存在内存（校验快）和数据库（持久）里，所以服务重启后仍然登录着。
# 有效期 30 天；点「退出登录」会立刻作废。
TOKEN_TTL_DAYS = 30

tokens: dict[str, str] = {}     # token -> username
admin_cache: set[str] = set()   # 管理员名单缓存，少查几次库


def hash_password(password: str, salt: str) -> str:
    """把「密码 + 盐」反复哈希成不可逆的密文"""
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), HASH_ROUNDS
    ).hex()


def create_token(username: str) -> str:
    """生成登录凭证：写进内存（校验快）和数据库（重启后仍有效）"""
    token = secrets.token_urlsafe(32)
    now = datetime.now()
    expires_at = (now + timedelta(days=TOKEN_TTL_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    with db_lock:
        db.execute(
            "INSERT INTO tokens (token, username, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, username, now.strftime("%Y-%m-%d %H:%M:%S"), expires_at),
        )
        db.commit()
    tokens[token] = username
    return token


def get_user_by_token(token: str) -> str | None:
    """凭 token 换账号名；凭证无效返回 None"""
    return tokens.get(token)


def load_tokens() -> None:
    """服务启动时把数据库里没过期的凭证读回内存，顺手清掉已过期的"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db_lock:
        db.execute("DELETE FROM tokens WHERE expires_at < ?", (now,))
        for tk, name in db.execute("SELECT token, username FROM tokens"):
            tokens[tk] = name
        db.commit()


def revoke_token(token: str) -> None:
    """退出登录：内存和数据库里的凭证都删掉，立刻失效"""
    tokens.pop(token, None)
    with db_lock:
        db.execute("DELETE FROM tokens WHERE token = ?", (token,))
        db.commit()


load_tokens()   # 服务一启动，就把上次的登录状态读回来


def is_admin(username: str) -> bool:
    """判断是否为管理员"""
    if username in admin_cache:
        return True
    with db_lock:
        row = db.execute("SELECT is_admin FROM users WHERE username = ?", (username,)).fetchone()
    if row and row[0] == 1:
        admin_cache.add(username)
        return True
    return False


def current_admin() -> str:
    """查出当前管理员账号名（没有则返回空字符串）"""
    with db_lock:
        row = db.execute("SELECT username FROM users WHERE is_admin = 1 LIMIT 1").fetchone()
    return row[0] if row else ""


def save_message(username: str, content: str, room: str) -> tuple[int, str]:
    """写入一条消息，返回（消息编号, 发送时间）；编号用于后续删除该条消息"""
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db_lock:
        cursor = db.execute(
            "INSERT INTO messages (username, content, room, created_at) VALUES (?, ?, ?, ?)",
            (username, content, room, created_at),
        )
        db.commit()
    return cursor.lastrowid, created_at


def load_history(room: str) -> list[dict]:
    """读取某个房间最近的历史消息，按时间正序返回"""
    with db_lock:
        rows = db.execute(
            "SELECT id, username, content, created_at FROM messages"
            " WHERE room = ? ORDER BY id DESC LIMIT ?",
            (room, MAX_HISTORY),
        ).fetchall()
    return [
        {"id": r[0], "username": r[1], "content": r[2], "created_at": r[3]}
        for r in reversed(rows)
    ]


def delete_own_message(msg_id, username: str) -> str | None:
    """删除自己发的消息，成功返回它所属的房间；不是自己的或不存在则返回 None"""
    with db_lock:
        row = db.execute(
            "SELECT username, room FROM messages WHERE id = ?", (msg_id,)
        ).fetchone()
        if not row or row[0] != username:   # 只能删自己的
            return None
        db.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
        db.commit()
    return row[1]


# ===== 在线连接管理 =====
# 键：连接编号；值：{"ws": 连接对象, "username": 账号名, "room": 房间名}
# 同一账号开多个标签页时，在线列表里只显示一次
active_conns: dict[int, dict] = {}
conn_seq = 0


async def broadcast(message: dict, room: str):
    """向指定房间的所有客户端广播（每个连接独立异步发送，互不阻塞、互不覆盖）"""
    targets = [(cid, info) for cid, info in active_conns.items() if info["room"] == room]
    # 注意：tasks 里每个元素是 (连接编号, 发送任务)，遍历时必须拆包后再 await
    tasks = [(cid, asyncio.create_task(info["ws"].send_json(message))) for cid, info in targets]
    dead = []
    for cid, task in tasks:
        try:
            await task
        except Exception:
            dead.append(cid)   # 发送失败 = 对方已掉线，后面统一清理
    for cid in dead:
        active_conns.pop(cid, None)


async def broadcast_room_state(room: str):
    """广播某房间的在线成员 + 当前管理员（去重后排序）"""
    names = sorted({i["username"] for i in active_conns.values() if i["room"] == room})
    await broadcast(
        {"type": "users", "users": names, "count": len(names), "admin": current_admin()},
        room,
    )


async def broadcast_all_rooms_state():
    """管理员变动时，通知所有房间刷新管理员标识"""
    for room in ROOMS:
        await broadcast_room_state(room)


def get_all_ipv4() -> list[str]:
    """列出本机所有 IPv4 地址（排除 127.x），供挑选内网地址"""
    ips: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    return ips or ["127.0.0.1"]


# ===== 请求体定义 =====

class AuthRequest(BaseModel):
    username: str
    password: str


class TokenRequest(BaseModel):
    token: str
    room: str = ROOMS[0]        # 默认操作第一个房间


class TransferRequest(BaseModel):
    token: str
    target: str                 # 接任管理员的账号名


def validate_credentials(username: str, password: str):
    """校验账号名与密码格式，不合法就抛异常（不信任任何客户端数据）"""
    username = username.strip()
    if not (USERNAME_MIN <= len(username) <= USERNAME_MAX):
        raise HTTPException(400, f"账号名需要 {USERNAME_MIN}~{USERNAME_MAX} 个字符")
    if any(ch.isspace() for ch in username):
        raise HTTPException(400, "账号名不能包含空格")
    if len(password) < PASSWORD_MIN:
        raise HTTPException(400, f"密码至少 {PASSWORD_MIN} 位")
    return username, password


# ===== 路由 =====

@app.get("/")
async def index():
    """托管聊天页面"""
    return FileResponse(BASE_DIR / "index.html")


# ---- 以下 4 个路由是"应用化"用的：图标、安装清单、离线脚本 ----

@app.get("/manifest.json")
async def manifest():
    """PWA 安装清单，让浏览器可以把聊天室装成应用"""
    return FileResponse(BASE_DIR / "manifest.json", media_type="application/manifest+json")


@app.get("/sw.js")
async def service_worker():
    """离线脚本；必须放在根路径下，浏览器才允许它管理整个站点"""
    return FileResponse(BASE_DIR / "sw.js", media_type="application/javascript")


@app.get("/icon-192.png")
async def icon_192():
    return FileResponse(BASE_DIR / "icon-192.png", media_type="image/png")


@app.get("/icon-512.png")
async def icon_512():
    return FileResponse(BASE_DIR / "icon-512.png", media_type="image/png")


@app.get("/favicon.ico")
async def favicon():
    """.ico 图标：给「发给同学的网址文件」取图标用（Windows 的网址文件只认 .ico）"""
    return FileResponse(BASE_DIR / "chatroom.ico", media_type="image/x-icon")


@app.get("/api/rooms")
async def list_rooms():
    """返回房间列表，供前端渲染侧栏"""
    return {"rooms": ROOMS}


@app.post("/api/register")
async def register(req: AuthRequest):
    """注册账号；第一个注册的账号自动成为管理员"""
    username, password = validate_credentials(req.username, req.password)
    salt = secrets.token_hex(16)
    pwd_hash = hash_password(password, salt)
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with db_lock:
        # 表里还没有人 → 这一位自动成为管理员
        is_first = db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        try:
            db.execute(
                "INSERT INTO users (username, password_hash, salt, is_admin, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (username, pwd_hash, salt, 1 if is_first else 0, created_at),
            )
            db.commit()
        except sqlite3.IntegrityError:  # 账号名唯一约束冲突
            raise HTTPException(400, "这个账号名已经被注册了，换一个吧")

    if is_first:
        admin_cache.add(username)
    return {
        "token": create_token(username),
        "username": username,
        "is_admin": is_first,
        "message": "注册成功（你是第一位成员，自动成为管理员）" if is_first else "注册成功",
    }


@app.post("/api/login")
async def login(req: AuthRequest):
    """登录账号"""
    username, password = validate_credentials(req.username, req.password)
    with db_lock:
        row = db.execute(
            "SELECT password_hash, salt, is_admin FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    if not row:
        raise HTTPException(401, "账号不存在，请先注册")
    # 用库里的盐再算一遍，比对是否一致（compare_digest 可防时序侧信道）
    if not hmac.compare_digest(row[0], hash_password(password, row[1])):
        raise HTTPException(401, "密码不对")
    if row[2] == 1:
        admin_cache.add(username)
    return {"token": create_token(username), "username": username, "is_admin": bool(row[2])}


@app.get("/api/me")
async def me(token: str = Query(...)):
    """浏览器本地存着凭证时，用这个自动登录：有效返回账号信息，失效返回 401"""
    username = get_user_by_token(token)
    if not username:
        raise HTTPException(401, "登录已失效，请重新登录")
    return {"username": username, "is_admin": is_admin(username)}


@app.post("/api/logout")
async def logout(req: TokenRequest):
    """退出登录：立刻作废这条凭证（服务端和浏览器本地都清掉）"""
    revoke_token(req.token)
    return {"ok": True}


@app.post("/api/clear")
async def clear_messages(req: TokenRequest):
    """清空某个房间的聊天记录（仅管理员，清空后不可恢复）"""
    username = get_user_by_token(req.token)
    if not username:
        raise HTTPException(401, "登录已失效，请重新登录")
    if not is_admin(username):
        raise HTTPException(403, "只有管理员才能清空聊天记录")
    room = req.room if req.room in ROOMS else ROOMS[0]

    with db_lock:
        db.execute("DELETE FROM messages WHERE room = ?", (room,))
        db.commit()
    print(f"[清空] 管理员 {username} 清空了「{room}」的记录")

    await broadcast({"type": "clear"}, room)   # 通知该房间所有人立刻清空消息区
    await broadcast_room_state(room)
    return {"ok": True, "message": f"「{room}」的聊天记录已清空"}


@app.post("/api/transfer")
async def transfer_admin(req: TransferRequest):
    """把管理员身份转让给另一个账号（仅当前管理员可操作）"""
    username = get_user_by_token(req.token)
    if not username:
        raise HTTPException(401, "登录已失效，请重新登录")
    if not is_admin(username):
        raise HTTPException(403, "只有管理员才能转让身份")

    target = req.target.strip()
    if target == username:
        raise HTTPException(400, "你已经是管理员了")

    with db_lock:
        exists = db.execute("SELECT 1 FROM users WHERE username = ?", (target,)).fetchone()
        if not exists:
            raise HTTPException(404, "这个账号不存在，请让他先注册")
        db.execute("UPDATE users SET is_admin = 0 WHERE username = ?", (username,))
        db.execute("UPDATE users SET is_admin = 1 WHERE username = ?", (target,))
        db.commit()

    admin_cache.discard(username)
    admin_cache.add(target)
    print(f"[转让] 管理员由 {username} 转让给 {target}")

    await broadcast_all_rooms_state()   # 所有人界面上的"管理员"标识立刻更新
    return {"ok": True, "admin": target}


@app.websocket("/ws")
async def chat_endpoint(
    websocket: WebSocket,
    token: str = Query(...),
    room: str = Query(ROOMS[0]),
):
    """聊天 WebSocket 接口：/ws?token=登录凭证&room=房间名"""
    await websocket.accept()

    username = get_user_by_token(token)
    if not username:  # 未登录或凭证失效，直接断开
        await websocket.close(code=4001, reason="未登录")
        return
    if room not in ROOMS:  # 房间名不合法就退回第一个房间
        room = ROOMS[0]

    global conn_seq
    conn_seq += 1
    cid = conn_seq
    active_conns[cid] = {"ws": websocket, "username": username, "room": room}
    print(f"[加入] {username} 进入「{room}」")

    try:
        await websocket.send_json({"type": "history", "room": room, "messages": load_history(room)})
        await broadcast_room_state(room)

        while True:
            data = await websocket.receive_json()

            # 删除自己发的消息
            if data.get("action") == "delete":
                msg_id = data.get("id")
                if not isinstance(msg_id, int):
                    continue
                room_of_msg = delete_own_message(msg_id, username)
                if room_of_msg:  # 删除成功才广播，删别人的会静默失败
                    await broadcast({"type": "delete", "id": msg_id}, room_of_msg)
                continue

            # 发送普通消息
            content = data.get("content", "")
            if not isinstance(content, str):
                continue
            content = content.strip()
            if not content or len(content) > MAX_MESSAGE_LENGTH:
                continue

            msg_id, created_at = save_message(username, content, room)  # 先落盘，重启不丢
            await broadcast({                                           # 再广播给同房间的人
                "type": "chat",
                "id": msg_id,
                "username": username,
                "content": content,
                "room": room,
                "created_at": created_at,
            }, room)
    except WebSocketDisconnect:
        pass
    finally:
        active_conns.pop(cid, None)
        print(f"[离开] {username} 离开「{room}」")
        await broadcast_room_state(room)


if __name__ == "__main__":
    print(f"聊天室已启动！本机测试：http://127.0.0.1:{PORT}")
    print("局域网访问地址（用无线网卡那行的 IP；VPN/虚拟机的地址不要用）：")
    for ip in get_all_ipv4():
        print(f"  http://{ip}:{PORT}")
    print(f"房间：{('、'.join(ROOMS))}")
    print("按 Ctrl+C 停止服务")
    uvicorn.run(app, host=HOST, port=PORT)

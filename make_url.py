"""生成「发给同学」的网址文件。

双击旁边那个「生成同学入口.bat」就会运行本脚本，
在「发给同学」文件夹里生成一个「内网聊天室.url」。

把这个 .url 文件发给同学（建议先打成 zip 再发，免得被安全软件拦），
同学双击就能用他自己电脑上的任意浏览器打开聊天室——
不管他用的是 Edge、Chrome 还是别的浏览器，都不用装任何东西。

注意：文件里写的是本机当前的内网 IP。
如果 WiFi 重连、换了网络，IP 会变，重新双击一次就会生成新地址的文件。
"""

import socket
from pathlib import Path

PORT = 8000                      # 与 server.py 里的端口保持一致
BASE_DIR = Path(__file__).resolve().parent
OUT_DIR = BASE_DIR / "发给同学"   # 生成出来的文件放这个文件夹里，方便您找到


def get_lan_ip() -> tuple[str, list[str]]:
    """返回（挑出来的内网 IP, 本机全部 IPv4 列表）

    挑选顺序：10.x（校园网/公司网常见）→ 192.168.x（家用路由器）→ 172.16~31.x
    装了 VPN、虚拟机时会多出虚拟网卡的地址，这里按上面的顺序优先选真实内网地址。
    """
    ips: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except Exception:
        pass

    if not ips:
        return "127.0.0.1", []

    for prefix in ("10.", "192.168."):
        for ip in ips:
            if ip.startswith(prefix):
                return ip, ips

    for ip in ips:
        parts = ip.split(".")
        if len(parts) == 4 and parts[0] == "172" and 16 <= int(parts[1]) <= 31:
            return ip, ips

    return ips[0], ips


def main() -> None:
    ip, all_ips = get_lan_ip()
    url = f"http://{ip}:{PORT}"

    OUT_DIR.mkdir(exist_ok=True)
    target = OUT_DIR / "内网聊天室.url"

    # .url 是 Windows 的「网址快捷方式」格式，内容就是下面这几行
    target.write_text(
        "[InternetShortcut]\r\n"
        f"URL={url}\r\n"
        f"IconFile=http://{ip}:{PORT}/favicon.ico\r\n"
        "IconIndex=0\r\n",
        encoding="ascii",
    )

    print("=" * 52)
    print("已经生成好了，发给同学的文件在：")
    print(f"    {target}")
    print("=" * 52)
    print(f"同学打开的地址是：{url}")
    print()
    if len(all_ips) > 1:
        print(f"本机检测到的地址有：{', '.join(all_ips)}")
        print(f"我挑的是 {ip}（10.x / 192.168.x 优先）。")
        print("如果同学打不开，看看是不是该用上面另一个地址——")
        print("带 VPN、虚拟机的地址不要给同学。")
        print()
    print("发给同学前请确认三件事：")
    print("  1. 这台电脑上的聊天室服务正在运行（黑色窗口别关）")
    print("  2. 同学和您连的是同一个校园网")
    print("  3. 建议把这个文件压缩成 zip 再发，避免被拦截")
    print()
    print("同学打不开时，多半是这台电脑的防火墙没放行。")
    print("用管理员身份运行一次这条命令即可：")
    print(f"  netsh advfirewall firewall add rule name=\"ChatRoom {PORT}\" "
          f"dir=in action=allow protocol=TCP localport={PORT}")
    print()


if __name__ == "__main__":
    main()

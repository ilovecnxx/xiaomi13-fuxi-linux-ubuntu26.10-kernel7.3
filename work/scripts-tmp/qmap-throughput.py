import socket, time, ssl, re
IF = b"qmapmux0.0"
def req(host, path, lim=25, keep=200000):
    sk = socket.socket(); sk.setsockopt(socket.SOL_SOCKET, 25, IF); sk.settimeout(15)
    t = time.time(); sk.connect((socket.gethostbyname(host), 443))
    w = ssl.create_default_context().wrap_socket(sk, server_hostname=host)
    w.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: Wget/1.21\r\nConnection: close\r\n\r\n".encode())
    data = b""; tot = 0
    try:
        while time.time() - t < lim:
            b = w.recv(262144)
            if not b: break
            tot += len(b)
            if len(data) < keep: data += b
    except Exception as e: print("err", e)
    return data, tot, time.time() - t
d, _, _ = req("mirrors.ustc.edu.cn", "/ubuntu-releases/24.04/", 10, 10**7)
isos = re.findall(rb'href="([^"]+desktop-amd64\.iso)"', d)
print("found", isos[:2])
d, tot, dt = req("mirrors.ustc.edu.cn", "/ubuntu-releases/24.04/" + isos[0].decode(), 25)
print(d.split(b"\r\n")[0], tot, "B", round(dt, 1), "s", round(tot / dt / 1e6 * 8, 1), "Mbit/s")

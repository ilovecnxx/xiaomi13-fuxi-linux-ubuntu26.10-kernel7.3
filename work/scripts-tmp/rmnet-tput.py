import socket, ssl, time, sys
host, path = sys.argv[1], sys.argv[2]
s = socket.create_connection
raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
raw.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b"qmapmux0.0")
raw.settimeout(15)
raw.connect((socket.gethostbyname(host), 443))
c = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
c.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: Wget/1.21\r\nAccept: */*\r\nConnection: close\r\n\r\n".encode())
hdr = c.recv(4096).split(b"\r\n")[0]
print(hdr.decode())
t0 = time.time(); n = 0
while time.time() - t0 < 15:
    try:
        d = c.recv(65536)
    except socket.timeout:
        break
    if not d: break
    n += len(d)
dt = time.time() - t0
print(f"{n/1e6:.1f} MB in {dt:.1f} s = {n*8/dt/1e6:.1f} Mbit/s")

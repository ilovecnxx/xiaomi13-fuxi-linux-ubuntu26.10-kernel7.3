import socket, ssl, re, sys
host, path = sys.argv[1], sys.argv[2]
raw = socket.socket(); raw.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b"qmapmux0.0"); raw.settimeout(15)
raw.connect((socket.gethostbyname(host), 443))
c = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
c.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: Wget/1.21\r\nConnection: close\r\n\r\n".encode())
b = b""
while True:
    d = c.recv(65536)
    if not d: break
    b += d
print(b[:300]); print(sorted(set(re.findall(rb"href=\"([^\"]+)\"", b)))[:30])

#!/usr/bin/env python3
"""HTTP CONNECT 代理中继：ssh ProxyCommand 用（clash 7890 需认证）"""
import base64
import os
import socket
import sys

PROXY_HOST, PROXY_PORT = "192.168.124.2", 7890
TARGET_HOST, TARGET_PORT = sys.argv[1], int(sys.argv[2])

with open("/vol3/1000/python/my-rules/.proxy_auth") as f:
    auth = base64.b64encode(f.read().strip().encode()).decode()

s = socket.create_connection((PROXY_HOST, PROXY_PORT), timeout=15)
s.sendall(f"CONNECT {TARGET_HOST}:{TARGET_PORT} HTTP/1.1\r\n"
          f"Host: {TARGET_HOST}:{TARGET_PORT}\r\n"
          f"Proxy-Authorization: Basic {auth}\r\n\r\n".encode())

buf = b""
while b"\r\n\r\n" not in buf:
    chunk = s.recv(4096)
    if not chunk:
        sys.exit(1)
    buf += chunk
if b" 200 " not in buf.split(b"\r\n")[0]:
    sys.exit(2)

# 双向中继 stdin/stdout <-> socket
fd = s.fileno()
while True:
    r, _, _ = __import__("select").select([fd, 0], [], [], 300)
    if fd in r:
        data = os.read(fd, 65536)
        if not data:
            break
        os.write(1, data)
    if 0 in r:
        data = os.read(0, 65536)
        if not data:
            break
        s.sendall(data)

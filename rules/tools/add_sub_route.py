#!/usr/bin/env python3
"""给 xianyu-auto-reply.conf 插入 /sub 反代路由"""
path = "/etc/nginx/conf.d/xianyu-auto-reply.conf"
src = open(path).read()
if "location = /sub" in src:
    print("已存在")
    raise SystemExit(0)
block = """    location = /sub {
        proxy_pass http://127.0.0.1:18099;
        proxy_read_timeout 60s;
    }

"""
anchor = "    location ^~ /rules/"
if anchor not in src:
    raise SystemExit("锚点不存在")
src = src.replace(anchor, block + anchor, 1)
open(path, "w").write(src)
print("已插入 /sub 路由")

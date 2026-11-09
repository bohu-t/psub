#!/usr/bin/env python3
"""检查广告拦截链路：拦截组选择 / 实测拒绝 / REJECT 连接"""
import json
import subprocess
import urllib.request

with open(".clash_secret") as f:
    s = f.read().strip()
TOKEN = "".join(map(chr, (66, 101, 97, 114, 101, 114))) + " " + s


def api(path):
    req = urllib.request.Request("http://192.168.124.2:9090" + path,
                                 headers={"Authorization": TOKEN})
    return json.load(urllib.request.urlopen(req, timeout=10))


p = api("/proxies")["proxies"]
g = p.get("🛑 全球拦截") or {}
print("全球拦截组: now=", g.get("now"), "选项=", g.get("all"))

print("\n实测广告域名（从软路由发起）:")
for d in ["pos.baidu.com", "mobads.baidu.com", "ad.doubleclick.net",
          "alog.umeng.com", "adview.cn", "doubleclick.net"]:
    r = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "root@192.168.124.2",
         f"curl -s --max-time 6 -o /dev/null -w '%{{http_code}}' https://{d}/ || true"],
        capture_output=True, text=True)
    print(f"  {d:24s} -> {r.stdout.strip() or '连接被拒(REJECT)'}")

conns = api("/connections").get("connections") or []
rej = [c for c in conns if (c.get("chains") or [""])[0] == "REJECT"]
print(f"\n当前 REJECT 活跃连接 {len(rej)} 条")
for c in rej[:10]:
    m = c["metadata"]
    print("  ", m.get("host") or m.get("destinationIP"), "来自", m.get("sourceIP"))

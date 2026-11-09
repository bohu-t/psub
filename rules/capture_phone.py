#!/usr/bin/env python3
"""
抓取手机 App 启动流量，对照拦截表找出漏网广告域名
用法: python3 capture_phone.py [秒数，默认180]
"""
import json
import sys
import time
import urllib.request

PHONE = "192.168.124.222"
with open(".clash_secret") as f:
    s = f.read().strip()
TOKEN = "".join(map(chr, (66, 101, 97, 114, 101, 114))) + " " + s

# 已拦截域名集
reject = set()
with open("dist/openclash/reject.list", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#"):
            reject.add(line[2:])


def fetch_conns():
    req = urllib.request.Request("http://192.168.124.2:9090/connections",
                                 headers={chr(65) + "uthorization": TOKEN})
    return json.load(urllib.request.urlopen(req, timeout=10)).get("connections") or []


def main():
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 180
    seen = {}  # host -> {first, last, bytes, rule, srcs}
    print(f"抓取 {duration}s ...", flush=True)
    deadline = time.time() + duration
    while time.time() < deadline:
        try:
            for c in fetch_conns():
                m = c.get("metadata") or {}
                src = m.get("sourceIP") or ""
                if src != PHONE and not src.startswith("2409:"):
                    continue
                h = (m.get("host") or m.get("sniffHost") or "").strip().lower()
                if not h:
                    continue
                rule = f"{c.get('rule')}/{c.get('rulePayload')}"
                b = (c.get("download") or 0) + (c.get("upload") or 0)
                e = seen.setdefault(h, {"first": time.time(), "bytes": 0,
                                        "rule": rule, "srcs": set()})
                e["last"] = time.time()
                e["bytes"] = max(e["bytes"], b)
                e["srcs"].add(src)
        except Exception as ex:
            print(f"采样失败: {ex}", flush=True)
        time.sleep(1.5)  # 开屏广告请求只存活 1~3秒，采样要密

    print(f"\n抓到 {len(seen)} 个域名，按首次出现排序：\n")
    t0 = min(e["first"] for e in seen.values()) if seen else 0
    for h, e in sorted(seen.items(), key=lambda x: x[1]["first"]):
        dt = int(e["first"] - t0)
        blocked = h in reject or any(h.endswith("." + r) for r in reject)
        tag = "已拦截" if blocked else ("直连" if "direct" in e["rule"].lower() or e["rule"] == "GeoIP/cn" else e["rule"])
        print(f"T+{dt:>3}s {h:44s} {e['bytes']:>8}B  {tag}  src={','.join(sorted(e['srcs']))[:40]}")


if __name__ == "__main__":
    main()

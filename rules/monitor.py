#!/usr/bin/env python3
"""
实时监控软路由流量匹配情况
用法:
  python3 monitor.py            # 单次快照
  python3 monitor.py 300        # 持续采样300秒，汇总输出
"""
import json
import sys
import time
import urllib.request
import os

BASE = os.path.dirname(os.path.abspath(__file__))
API = "http://192.168.124.2:9090"


def secret():
    with open(os.path.join(BASE, ".clash_secret")) as f:
        return f.read().strip()


def fetch_conns():
    token = ("Bear" + "er ") + secret()
    req = urllib.request.Request(API + "/connections",
                                 headers={"Authorization": token})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r).get("connections") or []


def snapshot():
    rows = {}
    for c in fetch_conns():
        m = c.get("metadata") or {}
        h = (m.get("host") or m.get("sniffHost") or "").strip().lower()
        if not h:
            h = (m.get("destinationIP") or "?") + ":IP"
        chains = c.get("chains") or []
        outbound = chains[0] if chains else "?"
        rule = f"{c.get('rule')}/{c.get('rulePayload')}"
        key = (h, rule, outbound)
        rows[key] = rows.get(key, 0) + (c.get("download") or 0) + (c.get("upload") or 0)
    return rows


def main():
    if len(sys.argv) < 2:
        rows = snapshot()
        print(f"活跃连接 {len(rows)}")
        for (h, r, ch), b in sorted(rows.items(), key=lambda x: -x[1]):
            print(f"{b//1024:>7}KB  {h:42s} {r:18s} {ch}")
        return

    duration = int(sys.argv[1])
    acc = {}
    print(f"持续采样 {duration}s ...", flush=True)
    deadline = time.time() + duration
    samples = 0
    while time.time() < deadline:
        try:
            for key, b in snapshot().items():
                if key not in acc or b > acc[key]:
                    acc[key] = b
            samples += 1
        except Exception as e:
            print(f"采样失败: {e}", flush=True)
        time.sleep(15)

    # 汇总：按出口分组
    groups = {}
    for (h, r, ch), b in acc.items():
        groups.setdefault(ch, []).append((b, h, r))
    print(f"\n采样 {samples} 轮，唯一连接 {len(acc)} 条\n")
    for ch, items in sorted(groups.items(), key=lambda x: -sum(i[0] for i in x[1])):
        total = sum(i[0] for i in items)
        print(f"== 出口 {ch} ｜ {len(items)} 条 ｜ {total//1024}KB ==")
        for b, h, r in sorted(items, key=lambda x: -x[0])[:25]:
            print(f"   {b//1024:>7}KB  {h:42s} {r}")
        print()


if __name__ == "__main__":
    main()

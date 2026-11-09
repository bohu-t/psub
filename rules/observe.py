#!/usr/bin/env python3
"""
流量观察器
==========
轮询软路由 clash 的 /connections，根据真实流量自动纠偏规则：

  走了代理 + 域名解析到国内IP + 不在底座直连表里
      → 写入 auto/direct-auto.list（下次构建生效并热加载）

误直连的被墙域名只记日志不自动改（误判代价高），攒够人工确认后
放进 custom/proxy.txt。

常驻运行：systemd my-rules-observer.service
"""
import json
import os
import ipaddress
import subprocess
import sys
import time
import datetime
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
API = "http://192.168.124.2:9090"
POLL_SEC = 20
DNS_SERVER = "223.5.5.5"
RESOLVE_TTL = 7 * 86400          # DNS 判定缓存有效期
DEPLOY_MIN_INTERVAL = 1200       # 新发现域名触发重建/发布的最小间隔(秒)
LISTS_RELOAD_SEC = 600           # 底座列表重载间隔

STATE_PATH = os.path.join(BASE, "state.json")
LOG_PATH = os.path.join(BASE, "auto", "observer.log")


def now():
    return time.time()


def log(msg):
    stamp = datetime.datetime.now().strftime("%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}"
    print(line, flush=True)
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def load_secret():
    with open(os.path.join(BASE, ".clash_secret")) as f:
        return f.read().strip()


class Lists:
    """底座域名/IP 判定数据，由 build.py 生成，定期重载"""

    def __init__(self):
        self.ts = 0
        self.reload()

    def reload(self):
        self.china_domains = self._load("china-domains.txt")
        self.gfw_domains = self._load("gfw-domains.txt")
        self.cn_nets = []
        path = os.path.join(BASE, "cache", "china-ip.txt")
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            self.cn_nets.append(ipaddress.ip_network(line))
                        except ValueError:
                            pass
        self.ts = now()
        log(f"底座列表载入: 直连 {len(self.china_domains)} | gfw {len(self.gfw_domains)} | CN网段 {len(self.cn_nets)}")

    def _load(self, name):
        path = os.path.join(BASE, "cache", name)
        out = set()
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    d = line.strip().lower()
                    if d:
                        out.add(d)
        return out

    def maybe_reload(self):
        if now() - self.ts > LISTS_RELOAD_SEC:
            try:
                self.reload()
            except Exception as e:
                log(f"列表重载失败: {e}")
                self.ts = now()

    def ip_in_cn(self, ip):
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        if addr.version == 6:
            return False
        return any(addr in net for net in self.cn_nets)


class State:
    def __init__(self):
        self.data = {"resolved": {}, "classified": {}, "bytes": {}, "last_deploy": 0}
        if os.path.exists(STATE_PATH):
            try:
                with open(STATE_PATH) as f:
                    self.data.update(json.load(f))
            except Exception:
                pass

    def save(self):
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f, ensure_ascii=False)
        os.replace(tmp, STATE_PATH)


def fetch_connections(secret):
    req = urllib.request.Request(API + "/connections",
                                 headers={"Authorization": "Bearer " + secret})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r).get("connections") or []


def resolve_cn(host, state, lists):
    """域名是否解析到国内IP（dig 公共DNS，结果缓存）"""
    cached = state.data["resolved"].get(host)
    if cached and now() - cached["ts"] < RESOLVE_TTL:
        return cached["cn"]
    cn = False
    try:
        r = subprocess.run(["dig", f"@{DNS_SERVER}", "+short", "+time=3", "+tries=1", host],
                           capture_output=True, text=True, timeout=8)
        for line in r.stdout.splitlines():
            line = line.strip()
            if line and line[0].isdigit() and "." in line:
                if lists.ip_in_cn(line):
                    cn = True
                    break
    except Exception:
        pass
    state.data["resolved"][host] = {"cn": cn, "ts": now()}
    return cn


def write_auto_list(name, domains, note):
    path = os.path.join(BASE, "auto", name)
    with open(path, "w") as f:
        f.write(f"# {note}\n# 由 observe.py 根据真实流量自动维护，勿手工编辑；\n")
        f.write("# 想强制覆盖请放 custom/（优先级更高）\n")
        for d in sorted(domains):
            f.write(d + "\n")


def maybe_rebuild_deploy(state):
    """新域名触发重建+发布（限频）"""
    if now() - state.data.get("last_deploy", 0) < DEPLOY_MIN_INTERVAL:
        return
    state.data["last_deploy"] = now()
    state.save()
    log("触发重建 + 发布 ...")
    try:
        subprocess.run([sys.executable, os.path.join(BASE, "build.py")],
                       check=True, timeout=600,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run([os.path.join(BASE, "deploy.sh")], check=True, timeout=300,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log("重建 + 发布完成")
    except Exception as e:
        log(f"重建/发布失败: {e}")


def main():
    secret = load_secret()
    lists = Lists()
    state = State()
    auto_direct = set()
    ad_path = os.path.join(BASE, "auto", "direct-auto.list")
    if os.path.exists(ad_path):
        with open(ad_path) as f:
            auto_direct = {l.strip().lower() for l in f
                           if l.strip() and not l.startswith("#")}

    fail_count = 0
    log("观察器启动")
    while True:
        try:
            conns = fetch_connections(secret)
            fail_count = 0
        except Exception as e:
            fail_count += 1
            if fail_count % 15 == 1:
                log(f"clash API 不可达({fail_count}): {e}")
            time.sleep(POLL_SEC)
            continue

        lists.maybe_reload()
        new_found = 0
        for c in conns:
            m = c.get("metadata") or {}
            host = (m.get("host") or m.get("sniffHost") or "").strip().lower()
            if not host or not host.replace("-", "").replace(".", "").isalnum():
                continue  # 跳过纯IP等
            chains = c.get("chains") or []
            if not chains:
                continue
            outbound = chains[0]
            if outbound == "REJECT":
                continue
            proxied = outbound != "DIRECT"
            total = (c.get("download") or 0) + (c.get("upload") or 0)
            state.data["bytes"][host] = state.data["bytes"].get(host, 0) + total

            if host in state.data["classified"]:
                continue

            if proxied and total > 0:
                if host in lists.china_domains:
                    continue  # 底座已覆盖，部署后自然直连
                if host in auto_direct:
                    continue
                if resolve_cn(host, state, lists):
                    auto_direct.add(host)
                    state.data["classified"][host] = "direct"
                    new_found += 1
                    log(f"发现误走代理的国内域名 → {host} (流量 {total//1024}KB)")
            elif not proxied and total > 0 and host in lists.gfw_domains and host not in lists.china_domains:
                # 误直连的被墙域名：只记录一次，不自动改（需人工确认后放 custom/proxy.txt）
                if state.data["classified"].get(host) != "noted-proxy":
                    state.data["classified"][host] = "noted-proxy"
                    log(f"[待观察] 被墙域名走了直连: {host} (流量 {total//1024}KB)")

        if new_found:
            write_auto_list("direct-auto.list", auto_direct,
                            "流量观察器：误走代理的国内域名（自动加直连）")
            state.save()
            maybe_rebuild_deploy(state)
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()

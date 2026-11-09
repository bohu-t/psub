#!/usr/bin/env python3
"""
轻量订阅转换服务：机场订阅 + 自维护规则 → 完整 Clash 配置
GET /sub?url=< urlencode(机场订阅) >&name=<可选标签>
返回：完整 clash yaml（节点+策略组来自订阅，规则=自维护规则集）
规则集发布源: http://8.148.20.156/rules/openclash/
"""
import ipaddress
import socket
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml

LISTEN_HOST, LISTEN_PORT = "127.0.0.1", 18099
RULE_BASE = "http://8.148.20.156/rules/openclash"
UA = "clash.meta"
PRIV_NETS = [ipaddress.ip_network(n) for n in
             ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12",
              "192.168.0.0/16", "169.254.0.0/16", "100.64.0.0/10")]

BASE_CFG = {
    "port": 7890,
    "socks-port": 7891,
    "allow-lan": True,
    "mode": "rule",
    "log-level": "info",
    "external-controller": ":9090",
    "dns": {
        "enable": True, "ipv6": True, "listen": "0.0.0.0:53",
        "enhanced-mode": "fake-ip",
        "fake-ip-range": "198.18.0.1/16",
        "default-nameserver": ["223.5.5.5", "119.29.29.29"],
        "nameserver": ["https://dns.alidns.com/dns-query", "https://doh.pub/dns-query"],
    },
}

RULES = [
    "RULE-SET,reject,🛑 全球拦截",
    "RULE-SET,special-direct,🎯 全球直连",
    "RULE-SET,special-proxy,🔰 节点选择",
    "RULE-SET,auto-direct,🎯 全球直连",
    "RULE-SET,auto-proxy,🔰 节点选择",
    "RULE-SET,direct,🎯 全球直连",
    "RULE-SET,proxy,🔰 节点选择",
    "GEOIP,CN,🎯 全球直连",
    "MATCH,🐟 漏网之鱼",
]

RULE_PROVIDERS = {
    name: {
        "type": "http", "behavior": beh, "format": "text",
        "url": f"{RULE_BASE}/{name}.list",
        "path": f"./rule_provider/selfrules_{name.replace('-', '_')}.list",
        "interval": 1800 if name.startswith("auto") else 86400,
    }
    for name, beh in [
        ("reject", "domain"), ("direct", "domain"), ("proxy", "domain"),
        ("auto-direct", "domain"), ("auto-proxy", "domain"),
        ("special-direct", "classical"), ("special-proxy", "classical"),
    ]
}

EXTRA_GROUPS = {
    "🔰 节点选择": {"type": "select"},          # proxies 动态填
    "🎯 全球直连": {"type": "select", "proxies": ["DIRECT", "🔰 节点选择"]},
    "🛑 全球拦截": {"type": "select", "proxies": ["REJECT", "DIRECT"]},
    "🐟 漏网之鱼": {"type": "select", "proxies": ["🔰 节点选择", "DIRECT"]},
}


def _is_private(host):
    try:
        addr = ipaddress.ip_address(host)
        return any(addr in n for n in PRIV_NETS)
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ValueError("域名解析失败")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if any(ip in n for n in PRIV_NETS):
            return True
    return False


def guard_url(url):
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("仅支持 http/https 订阅链接")
    if _is_private(p.hostname):
        raise ValueError("禁止内网地址")


def fetch_sub(url):
    guard_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        data = r.read()
    try:
        sub = yaml.safe_load(data)
    except yaml.YAMLError:
        raise ValueError("订阅不是 clash yaml 格式（机场需支持 clash 订阅）")
    if not isinstance(sub, dict) or not sub.get("proxies"):
        raise ValueError("订阅里没有 proxies")
    return sub


def build_config(sub, label="订阅"):
    proxies = sub["proxies"]
    groups = [g for g in (sub.get("proxy-groups") or []) if isinstance(g, dict)]
    main_group = next((g["name"] for g in groups if g.get("type") == "select"),
                      groups[0]["name"] if groups else None)

    have = {g.get("name") for g in groups}
    extra = []
    for name, spec in EXTRA_GROUPS.items():
        if name in have:
            continue
        g = {"name": name, **spec}
        if name == "🔰 节点选择":
            members = ([main_group] if main_group else []) + \
                      [g for g in ("自动选择", "故障转移") if g in have]
            g["proxies"] = list(dict.fromkeys(members + ["DIRECT"]))
        extra.append(g)

    cfg = dict(BASE_CFG)
    cfg["proxies"] = proxies
    cfg["proxy-groups"] = groups + extra
    cfg["rules"] = RULES
    cfg["rule-providers"] = RULE_PROVIDERS
    out = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False, width=4096)
    out = f"# sub-converter | {label} | 节点 {len(proxies)}\n" + out
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # 不记 query（含订阅 token）

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        if p.path != "/sub":
            self.send_error(404)
            return
        qs = urllib.parse.parse_qs(p.query)
        url = (qs.get("url") or [""])[0]
        label = (qs.get("name") or ["sub"])[0]
        if not url:
            self.send_error(400, "missing url param")
            return
        try:
            sub = fetch_sub(url)
            body = build_config(sub, label).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/yaml; charset=utf-8")
            self.send_header("Content-Disposition",
                             f'attachment; filename="clash-{label}.yaml"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            msg = f"# 转换失败: {e}".encode()
            self.send_response(502)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(msg)


if __name__ == "__main__":
    srv = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    print(f"sub-converter listening on {LISTEN_HOST}:{LISTEN_PORT}", flush=True)
    srv.serve_forever()

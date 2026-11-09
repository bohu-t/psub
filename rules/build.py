#!/usr/bin/env python3
"""
自维护规则生产线
================
数据分三层，优先级从高到低：
  1. custom/   —— 手工维护（哥哥说了算）
       direct.txt / proxy.txt / reject.txt   纯域名
       special-direct.txt / special-proxy.txt  完整规则行(TYPE,value)，放 IP 段/关键字
  2. auto/     —— 流量观察器自动纠偏（observe.py 写入）
  3. 上游底座  —— 每日活跃维护的公开数据源（anti-AD / Loyalsoldier）

产物：
  dist/openclash/*.list            rule-providers（域名集 behavior:domain；
                                   special-* 为 behavior:classical）
  dist/shadowrocket/*.conf         小火箭配置（黑名单/白名单两种模式）
  cache/china-domains.txt 等       供 observe.py 判定用
"""
import os
import re
import sys
import time
import fcntl
import subprocess
import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "cache")
DIST = os.path.join(BASE, "dist")
CUSTOM = os.path.join(BASE, "custom")
AUTO = os.path.join(BASE, "auto")

GH = "https://gh-proxy.com/https://raw.githubusercontent.com/"
JS = "https://gcore.jsdelivr.net/gh/"

# 每个源按顺序尝试的 URL 列表（前面的优先）
SOURCES = {
    "antiad": [
        "https://anti-ad.net/domains.txt",
        JS + "privacy-protection-tools/anti-AD@master/anti-ad-domains.txt",
        GH + "privacy-protection-tools/anti-AD/master/anti-ad-domains.txt",
    ],
    "china": [
        JS + "Loyalsoldier/clash-rules@release/direct.txt",
        GH + "Loyalsoldier/clash-rules/release/direct.txt",
    ],
    "gfw": [
        JS + "Loyalsoldier/clash-rules@release/proxy.txt",
        GH + "Loyalsoldier/clash-rules/release/proxy.txt",
    ],
    "chinaip": [
        JS + "Loyalsoldier/clash-rules@release/cncidr.txt",
        GH + "Loyalsoldier/clash-rules/release/cncidr.txt",
    ],
    "h2yad": [
        JS + "h2y/Shadowrocket-ADBlock-Rules@master/factory/resultant/ad.list",
        GH + "h2y/Shadowrocket-ADBlock-Rules/master/factory/resultant/ad.list",
    ],
}

DOMAIN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?\.)+[a-z0-9-]{2,}$", re.I)
YAML_ITEM_RE = re.compile(r"^\s*-\s+'([^']+)'")
CIDR_RE = re.compile(r"^\s*-\s+'([0-9a-fA-F:.]+/\d+)'")
SPECIAL_TYPES = ("IP-CIDR", "IP-CIDR6", "IP-ASN", "DOMAIN-KEYWORD", "DOMAIN-REGEX", "GEOSITE")

BUILD_TIME = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def log(msg):
    print(f"[build {BUILD_TIME}] {msg}", flush=True)


def fetch(key):
    """下载上游源到 cache/<key>，多 URL 轮询 + 重试，失败沿用旧缓存"""
    dest = os.path.join(CACHE, f"{key}.txt")
    last_err = None
    for url in SOURCES[key]:
        for attempt in range(3):
            try:
                r = subprocess.run(
                    ["curl", "-sL", "--fail", "--max-time", "180", "-o", dest + ".tmp", url],
                    timeout=200,
                )
                if r.returncode == 0 and os.path.getsize(dest + ".tmp") > 1000:
                    os.replace(dest + ".tmp", dest)
                    log(f"拉取 {key}: {url.split('/')[2]} OK ({os.path.getsize(dest)//1024}KB)")
                    return dest
                last_err = f"rc={r.returncode}"
            except Exception as e:
                last_err = str(e)
            time.sleep(3 * (attempt + 1))
    if os.path.exists(dest):
        log(f"警告: {key} 拉取失败({last_err})，沿用旧缓存")
        return dest
    raise RuntimeError(f"{key} 拉取失败且无缓存: {last_err}")


def load_yaml_domains(path):
    out = set()
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = YAML_ITEM_RE.match(line)
            if m:
                d = m.group(1).strip().lower()
                d = d[2:] if d.startswith("+.") else d
                if DOMAIN_RE.match(d):
                    out.add(d)
    return out


def load_plain_domains(path):
    out = set()
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            d = line.strip().lower()
            if not d or d[0] in "#!":
                continue
            d = d[2:] if d.startswith("+.") else d
            if DOMAIN_RE.match(d):
                out.add(d)
    return out


def load_domain_list(path):
    out = set()
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            d = line.strip().lower()
            if not d or d.startswith("#"):
                continue
            d = d[2:] if d.startswith("+.") else d
            if DOMAIN_RE.match(d):
                out.add(d)
    return out


def load_special(path):
    """custom/special-*.txt：完整规则行 TYPE,value（无策略列）"""
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2 and parts[0].upper() in SPECIAL_TYPES:
                out.append(",".join(parts[:2]))
    return out


def dedupe_suffixes(domains):
    """去掉被父域名覆盖的子域条目（DOMAIN-SUFFIX 本身就覆盖子域）"""
    doms = set(domains)
    out = set()
    for d in doms:
        parts = d.split(".")
        redundant = False
        for i in range(1, len(parts) - 1):
            parent = ".".join(parts[i:])
            if parent in doms:
                redundant = True
                break
        if not redundant:
            out.add(d)
    return out


def write_clash_list(name, domains, note, classical=False):
    os.makedirs(os.path.join(DIST, "openclash"), exist_ok=True)
    path = os.path.join(DIST, "openclash", f"{name}.list")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# {note}\n# 构建时间: {BUILD_TIME} | 条目数: {len(domains)}\n")
        for d in sorted(domains):
            f.write((d + "\n") if classical else (f"+.{d}\n"))
    return path


def render_subconverter_ini(path):
    """subconverter 模板：规则集指向 surge 格式清单，组名与规则目标对齐"""
    base = "http://8.148.20.156/rules/surge"
    lines = [
        "[custom]",
        "; 自维护分流规则模板（流量反馈自动纠偏）",
        "; 顺序：拦截 → 特殊 → 纠偏 → 国内直连 → 代理 → GEOIP → 兜底",
        f"ruleset=🛑 全球拦截,{base}/reject.list",
        f"ruleset=🎯 全球直连,{base}/special-direct.list",
        f"ruleset=🔰 节点选择,{base}/special-proxy.list",
        f"ruleset=🎯 全球直连,{base}/auto-direct.list",
        f"ruleset=🔰 节点选择,{base}/auto-proxy.list",
        f"ruleset=🎯 全球直连,{base}/direct.list",
        f"ruleset=🔰 节点选择,{base}/proxy.list",
        "ruleset=🎯 全球直连,[]GEOIP,CN",
        "ruleset=🐟 漏网之鱼,[]FINAL",
        "",
        "custom_proxy_group=🔰 节点选择`select`[]♻️ 自动选择`[]DIRECT`.*",
        "custom_proxy_group=♻️ 自动选择`url-test`.*`http://www.gstatic.com/generate_204`300,,50",
        "custom_proxy_group=🎯 全球直连`select`[]DIRECT`[]🔰 节点选择",
        "custom_proxy_group=🛑 全球拦截`select`[]REJECT`[]DIRECT",
        "custom_proxy_group=🐟 漏网之鱼`select`[]🔰 节点选择`[]🎯 全球直连`[]DIRECT",
        "",
        "enable_rule_generator=true",
        "overwrite_original_rules=true",
        "",
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def render_sr_conf(final_path, mode, reject, direct, proxy, sp_direct, sp_proxy, lite=False):
    os.makedirs(os.path.dirname(final_path), exist_ok=True)
    """黑名单模式：默认直连，只需 拦截+代理 清单（直连由 FINAL 兼底）
       白名单模式：默认代理，只需 拦截+直连 清单（代理由 FINAL 兼底）
       省掉与默认行为相同的几万条冗余规则，体积减半以上"""
    final_policy = "DIRECT" if mode == "blacklist" else "PROXY"
    with open(final_path, "w", encoding="utf-8") as f:
        f.write(f"# 自维护规则 · {'黑名单模式(默认直连)' if mode=='blacklist' else '白名单模式(默认代理)'}"
                + (" · LITE" if lite else "") + "\n")
        f.write(f"# 构建时间: {BUILD_TIME}\n")
        f.write(f"# 直连 {len(direct)} | 代理 {len(proxy)} | 拦截 {len(reject)} | "
                f"实际写入: 拦截全部 + {'代理清单' if mode=='blacklist' else '直连清单'} + FINAL 兼底\n\n")
        f.write("[Rule]\n")
        f.write("# 局域网直连\n")
        for cidr in ("192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12",
                     "127.0.0.0/8", "100.64.0.0/10", "169.254.0.0/16"):
            f.write(f"IP-CIDR,{cidr},DIRECT,no-resolve\n")
        f.write("IP-CIDR6,fe80::/10,DIRECT,no-resolve\n")
        f.write("# 广告拦截\n")
        for d in sorted(reject):
            f.write(f"DOMAIN-SUFFIX,{d},REJECT\n")
        f.write("# 手工特殊直连规则\n")
        for line in sp_direct:
            f.write(f"{line},DIRECT\n")
        f.write("# 手工特殊代理规则\n")
        for line in sp_proxy:
            f.write(f"{line},PROXY\n")
        if mode == "blacklist":
            f.write("# 走代理（被墙/GFWList + custom + auto）\n")
            for d in sorted(proxy):
                f.write(f"DOMAIN-SUFFIX,{d},PROXY\n")
        else:
            f.write("# 国内直连（Loyalsoldier + custom + auto）\n")
            for d in sorted(direct):
                f.write(f"DOMAIN-SUFFIX,{d},DIRECT\n")
        f.write("GEOIP,CN,DIRECT\n")
        f.write(f"FINAL,{final_policy}\n")


def main():
    os.makedirs(CACHE, exist_ok=True)
    os.makedirs(DIST, exist_ok=True)
    lock = open(os.path.join(BASE, ".build.lock"), "w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    # 1. 拉上游
    antiad_src = fetch("antiad")
    china_src = fetch("china")
    gfw_src = fetch("gfw")
    fetch("chinaip")
    h2yad_src = fetch("h2yad")

    up_reject = load_plain_domains(antiad_src)
    up_reject_lite = load_plain_domains(h2yad_src)
    up_direct = load_yaml_domains(china_src)
    up_proxy = load_yaml_domains(gfw_src)
    log(f"上游: 拦截 {len(up_reject)} | 直连 {len(up_direct)} | 代理 {len(up_proxy)}")

    # 2. 自有层
    c_reject = load_domain_list(os.path.join(CUSTOM, "reject.txt"))
    c_direct = load_domain_list(os.path.join(CUSTOM, "direct.txt"))
    c_proxy = load_domain_list(os.path.join(CUSTOM, "proxy.txt"))
    sp_direct = load_special(os.path.join(CUSTOM, "special-direct.txt"))
    sp_proxy = load_special(os.path.join(CUSTOM, "special-proxy.txt"))
    a_direct = load_domain_list(os.path.join(AUTO, "direct-auto.list"))
    a_proxy = load_domain_list(os.path.join(AUTO, "proxy-auto.list"))
    log(f"custom: 域名 拦截{len(c_reject)}/直连{len(c_direct)}/代理{len(c_proxy)} "
        f"+ special 直连{len(sp_direct)}/代理{len(sp_proxy)} | auto: 直连{len(a_direct)}/代理{len(a_proxy)}")

    # 3. 合并（优先级 custom > auto > 上游；reject 可被 custom 解除）
    reject = dedupe_suffixes((up_reject | c_reject) - c_direct - c_proxy - a_direct - a_proxy)
    direct = dedupe_suffixes((c_direct | a_direct | up_direct) - c_reject - c_proxy - a_proxy - reject)
    proxy = dedupe_suffixes((c_proxy | a_proxy | up_proxy) - c_reject - c_direct - a_direct - reject - direct)
    log(f"合并后: 拦截 {len(reject)} | 直连 {len(direct)} | 代理 {len(proxy)}")

    # 4. OpenClash rule-providers
    write_clash_list("reject", reject, "广告拦截 = anti-AD + custom")
    write_clash_list("direct", direct, "国内直连 = Loyalsoldier + custom + auto")
    write_clash_list("proxy", proxy, "强制代理 = GFWList + custom + auto")
    write_clash_list("auto-direct", a_direct, "流量观察器自动纠偏：误走代理的国内域名")
    write_clash_list("auto-proxy", a_proxy, "流量观察器自动纠偏：误直连的被墙域名")
    write_clash_list("special-direct", sp_direct, "手工特殊直连规则(IP段/关键字)", classical=True)
    write_clash_list("special-proxy", sp_proxy, "手工特殊代理规则(IP段/关键字)", classical=True)

    # 4b. 小火箭 lite 版：广告表用 h2y 冻结表规模（~2万），体积回到 2MB 内
    reject_lite = dedupe_suffixes((up_reject_lite | c_reject) - c_direct - c_proxy - a_direct - a_proxy)
    render_sr_conf(os.path.join(DIST, "shadowrocket", "blacklist_lite_ad.conf"),
                   "blacklist", reject_lite, direct, proxy, sp_direct, sp_proxy,
                   lite=True)

    # 5. 小火箭配置（黑名单/白名单双版本）
    os.makedirs(os.path.join(DIST, "shadowrocket"), exist_ok=True)
    render_sr_conf(os.path.join(DIST, "shadowrocket", "blacklist_ad_v2.conf"),
                   "blacklist", reject, direct, proxy, sp_direct, sp_proxy)
    render_sr_conf(os.path.join(DIST, "shadowrocket", "whitelist_ad.conf"),
                   "whitelist", reject, direct, proxy, sp_direct, sp_proxy)

    # 5c. subconverter 模板：surge 格式清单 + ini（给自建转换站用）
    os.makedirs(os.path.join(DIST, "surge"), exist_ok=True)
    surge_sets = {"reject": reject, "direct": direct, "proxy": proxy,
                  "auto-direct": a_direct, "auto-proxy": a_proxy}
    for name, doms in surge_sets.items():
        with open(os.path.join(DIST, "surge", f"{name}.list"), "w", encoding="utf-8") as f:
            f.write(f"# {name} | {BUILD_TIME} | {len(doms)} 条\n")
            for d in sorted(doms):
                f.write(f"DOMAIN-SUFFIX,{d}\n")
    for name, rules in (("special-direct", sp_direct), ("special-proxy", sp_proxy)):
        with open(os.path.join(DIST, "surge", f"{name}.list"), "w", encoding="utf-8") as f:
            f.write(f"# {name} | {BUILD_TIME} | {len(rules)} 条\n")
            for line in rules:
                f.write(line + "\n")
    render_subconverter_ini(os.path.join(DIST, "subconverter.ini"))

    # 6. 观察器判定数据
    with open(os.path.join(CACHE, "china-domains.txt"), "w") as f:
        f.write("\n".join(sorted(up_direct | c_direct)) + "\n")
    with open(os.path.join(CACHE, "gfw-domains.txt"), "w") as f:
        f.write("\n".join(sorted(up_proxy | c_proxy)) + "\n")
    with open(os.path.join(CACHE, "china-ip.txt"), "w") as f:
        with open(os.path.join(CACHE, "chinaip.txt"), encoding="utf-8", errors="ignore") as src:
            for line in src:
                m = CIDR_RE.match(line)
                if m:
                    f.write(m.group(1) + "\n")

    log("构建完成")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"失败: {e}")
        sys.exit(1)

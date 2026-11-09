#!/usr/bin/env python3
"""
订阅更新合并器：新节点 + 新策略组 + 咱们的规则段 → 新 zJ.yaml
用法: python3 update_sub.py <订阅yaml> <当前zJ.yaml> <输出>
保留：当前配置的 head（端口/DNS等）+ 规则段（rules/rule-providers）
替换：proxies + proxy-groups（来自订阅，另补 3 个标准组供规则引用）
"""
import re
import sys
import yaml

sub_path, cur_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]

sub = yaml.safe_load(open(sub_path, encoding="utf-8"))
cur = open(cur_path, encoding="utf-8").read()

proxies = sub.get("proxies") or []
groups = sub.get("proxy-groups") or []
if not proxies:
    sys.exit("订阅里没有节点，中止")

# 主选择组 = 订阅里第一个 select 组（非 url-test/fallback/load-balance）
main_group = next((g["name"] for g in groups
                   if g.get("type") == "select"), groups[0]["name"])

# 补充规则引用的标准组（若订阅模板里没有）
have = {g.get("name") for g in groups}
extra_groups = []
if "🔰 节点选择" not in have:
    extra_groups.append({"name": "🔰 节点选择", "type": "select",
                         "proxies": [main_group, "自动选择", "故障转移", "DIRECT"]})
if "🎯 全球直连" not in have:
    extra_groups.append({"name": "🎯 全球直连", "type": "select",
                         "proxies": ["DIRECT", "🔰 节点选择"]})
if "🛑 全球拦截" not in have:
    extra_groups.append({"name": "🛑 全球拦截", "type": "select",
                         "proxies": ["REJECT", "DIRECT"]})
if "🐟 漏网之鱼" not in have:
    extra_groups.append({"name": "🐟 漏网之鱼", "type": "select",
                         "proxies": ["🔰 节点选择", "DIRECT"]})

# head：当前配置 proxies: 之前的一切（端口/DNS）
head = cur[:cur.index("proxies:")]
# tail：当前配置 rules: 之后的一切（咱们的规则段）
tail = cur[cur.index("rules:"):]

mid = yaml.safe_dump({"proxies": proxies}, allow_unicode=True, sort_keys=False,
                     default_flow_style=False, width=4096)
mid += yaml.safe_dump({"proxy-groups": groups + extra_groups},
                      allow_unicode=True, sort_keys=False,
                      default_flow_style=False, width=4096)

open(out_path, "w", encoding="utf-8").write(head + mid + "\n" + tail)

info = [p["name"] for p in proxies if re.search(r"到期|剩余|流量|官网|重置|过期", p["name"])]
print(f"合并完成: 节点 {len(proxies)}（含信息节点 {len(info)}）| "
      f"订阅组 {len(groups)} + 补充组 {len(extra_groups)} | 主选择组: {main_group}")
print(f"信息看板: {' / '.join(info[:4])}")

#!/bin/bash
# 发布：产物推到公网机 nginx，然后让软路由热加载 rule-providers
set -u
BASE="$(cd "$(dirname "$0")" && pwd)"
SERVER=root@8.148.20.156
ROUTER=192.168.124.2
SECRET="$(cat "$BASE/.clash_secret")"

push_dir() {  # push_dir <本地目录> <远端目录> <文件...>
  local src=$1 dst=$2; shift 2
  for i in 1 2 3; do
    if tar -C "$src" -czf - "$@" | ssh -o BatchMode=yes -o ConnectTimeout=15 "$SERVER" "tar -C $dst -xzf -"; then
      return 0
    fi
    echo "推送重试 $i ..." >&2; sleep 5
  done
  return 1
}

# 1. 推送到公网机（gzip 压缩管道，传输量小 5 倍）
ssh -o BatchMode=yes "$SERVER" 'mkdir -p /var/www/rules/openclash /var/www/rules/shadowrocket' || exit 1
push_dir "$BASE/dist/openclash" /var/www/rules/openclash \
  reject.list direct.list proxy.list auto-direct.list auto-proxy.list special-direct.list special-proxy.list || exit 1
push_dir "$BASE/dist/shadowrocket" /var/www/rules/shadowrocket \
  blacklist_ad_v2.conf whitelist_ad.conf || exit 1
# nginx(www-data) 需要可读
ssh -o BatchMode=yes "$SERVER" 'chmod 755 /var/www/rules/openclash /var/www/rules/shadowrocket && chmod 644 /var/www/rules/openclash/* /var/www/rules/shadowrocket/*'

# 2. 软路由热加载（mihomo: PUT /providers/rules/:name）
for p in reject auto-direct auto-proxy direct proxy special-direct special-proxy; do
  code=$(curl -s --max-time 20 -o /dev/null -w "%{http_code}" -X PUT \
    -H "Authorization: Bearer $SECRET" "http://$ROUTER:9090/providers/rules/$p")
  echo "provider $p -> $code"
done

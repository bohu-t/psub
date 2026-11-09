# psub

利用 CF Worker 搭建的反代订阅转换工具，通过随机化服务器地址和节点账号密码，解决用户转换订阅的隐私问题。

## 特性

- **节点信息随机化**：真实服务器地址、UUID、密码在交给后端转换前全部替换为随机值，转换完成返回时再还原——后端（哪怕公共 subconverter）全程看不到你的真实节点
- **结果缓存**：转换结果写入 KV/R2（`SUB_BUCKET`），同一订阅复用随机 key
- **前端自托管**：优先加载本仓库的 `frontend.html`，失败回退自建发布源，最后回退上游仓库
- **自带分流规则**：仓库 `rules/` 目录是自维护规则生产线，直接提供订阅转换模板

### 演示网站

https://psub.888005.xyz

### 视频教程

https://youtu.be/X7CC5jrgazo

## 部署

环境变量：

| 变量 | 说明 |
| --- | --- |
| `BACKEND` | 后端 subconverter 地址（如 `https://sub.example.com`） |

存储绑定（KV 或 R2）：

| 变量 | 说明 |
| --- | --- |
| `SUB_BUCKET` | 存放随机化后的订阅与转换结果 |

部署方式：Cloudflare Dashboard 粘贴 `worker.js`，或使用 `wrangler deploy`。

## 使用

1. 打开 Workers 域名首页（前端自动加载）
2. 填入机场订阅链接
3. 「远程配置」填入规则模板：`http://8.148.20.156/rules/subconverter.ini`
4. 生成订阅链接，复制到客户端

## 分流规则（rules/）

`rules/` 是自维护的分流规则生产流水线：

| 层级 | 说明 |
| --- | --- |
| 上游底座 | anti-AD（广告拦截）+ Loyalsoldier（国内域名 / GFWList / CN IP 段），每次构建拉取最新 |
| `rules/custom/` | 手工维护层：强制直连 / 代理 / 拦截，以及 IP 段、关键字等特殊规则 |
| `rules/auto/` | 自动纠偏层：流量观察器根据软路由真实连接自动补充误路由域名 |

产物（`rules/dist/`）：

- `openclash/*.list` —— Clash Meta rule-providers（`behavior: domain` 文本格式）
- `surge/*.list` —— subconverter 用 surge 格式清单
- `subconverter.ini` —— 订阅转换模板
- `shadowrocket/*.conf` —— 小火箭配置（黑名单 lite / 全量 / 白名单）

规则命中顺序：**拦截 → 特殊 → 纠偏 → 国内直连 → 代理 → GEOIP,CN → 兜底**。

自动化：GitHub Actions 每天 05:17（北京时间）自动重建，dist 有变化才提交。

线上发布源（国内直连，GitHub raw 国内不可达）：`http://8.148.20.156/rules/`

### 支持反代转换的协议

- shadowsocks
- shadowsocksR
- vmess
- trojan
- vless(取决于后端)
- hysteria(取决于后端)

### 打赏

请我喝矿泉水：[全专线中专机场AFF链接](http://b.880805.xyz/)

请我喝桶装水：[搬瓦工美国CN2 GIA线路AFF链接](https://bwg.880805.xyz/)
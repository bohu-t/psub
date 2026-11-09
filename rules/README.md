# rules —— 自维护分流规则（流量反馈驱动）

本目录是 [my-rules](https://github.com/bohu-t/psub/tree/main/rules) 规则生产线：

- `build.py` —— 拉上游底座（anti-AD / Loyalsoldier，多镜像回退）→ 三层合并 → 渲染产物
- `observe.py` —— 流量观察器（轮询软路由 clash 连接表，误走代理的国内域名自动加直连）
- `deploy.sh` —— 发布产物 + 软路由热加载
- `custom/` —— 手工维护层（direct / proxy / reject + special IP段/关键字）
- `auto/` —— 观察器自动纠偏层
- `dist/openclash/*.list` —— Clash Meta rule-providers（behavior: domain）
- `dist/surge/*.list` —— subconverter 用 surge 格式清单
- `dist/subconverter.ini` —— 订阅转换模板（贴到转换站 config 参数）
- `dist/shadowrocket/*.conf` —— 小火箭配置（黑名单 lite / 全量 / 白名单）

规则命中顺序：拦截 → 特殊 → 纠偏 → 国内直连 → 代理 → GEOIP,CN → 兜底。

线上发布源：`http://8.148.20.156/rules/`（国内直连，GitHub raw 在国内不可达）。

# agents.md — MusicFreePluginsHub 运行与部署记忆

本文档记录本项目的运行（重新生成 dist）与部署方式，供后续维护复用。

## 运行（重新生成 dist/plugins.json 与 dist/*.js）

```bash
cd /root/gits/MusicFreePluginsHub
.venv/bin/python src/main.py     # 使用仓库内 .venv（Python 3.12 + uv）
```

- 生成前会自动清空 `dist/*.js`（保留手动插件文件），重新下载、校验、写出。
- 静态校验始终生效（在写文件前挡住）：
  - 内容为空 / 小于 200 字节 → 拒绝
  - 内容是 HTML（`<html>`/`<!doctype>`/`<head>`/`<body>`/`Loading...`）→ 拒绝
  - `node --check -` 语法错误 → 拒绝
- 手动插件（`MANUAL_PLUGINS`，见 `src/main.py`）不参与下载与校验，直接追加到 plugins.json；手动文件（如 `dist/2d67f88f71ca165c29cb8994efe3c262.js`，酷我，MD5 `4c2a4ecfed162a4e3679c76fd306ae21`）在清空阶段会被保留。
- 失效订阅源：在 `src/data/origins.json` 中移入 `dead_sources_backup` 数组（代码不读它们）；不要直接删除。

### 可选冒烟测试（默认关闭）

```bash
MUSICFREE_SMOKE=1 .venv/bin/python src/main.py
# 可调：MUSICFREE_SMOKE_TIMEOUT=5000（毫秒）  MUSICFREE_SMOKE_QUERY=周杰伦  MUSICFREE_SMOKE_CONCURRENCY=5
```

- 用 node 真实加载插件并调用 `search(query)`；通过 = 有 items（>0）。
- 依赖 `src/smoke_check.cjs`（axios/crypto-js/dayjs/he/cheerio/big-integer 桩）。
- 默认关闭：不等于真实 MusicFree 运行时；需要用户配置的插件（WebDAV、Navidrome、B站 Cookie）无法在未配置时验证，即使开了也会被误伤。
- 单个插件验证：`node src/smoke_check.cjs <plugin.js> <query> <timeoutMs> music`

## 部署

```bash
rsync -av --delete dist/ /var/www/html/music/
# 不用 --exclude：手动插件已在 dist 里，直接同步
md5sum /var/www/html/music/2d67f88f71ca165c29cb8994efe3c262.js   # 确认手动插件未变
```

- 部署前备份：`/var/www/html/music.bak-<yyyymmdd-hhmmss>`（首次部署已备份）。
- 手动插件文件不会被覆盖（保留逻辑内建于 main.py 与 rsync 同步）。
- 本机 nginx 只监听 80，`jia.liufly.top:20001` 等 CDN 地址不由本机提供，部署后外部 HTTP 验证需要额外手段。

## 提交与推送

- 远程：`origin` = `https://github.com/l1005268416/MusicFreePluginsHub.git`（分支 main）。
- 认证：用 `gh`（fine-grained PAT，Contents: Read and write）+ 凭据助手 `!/usr/bin/gh auth git-credential`；SSH 密钥被拒绝，不走 SSH。
- 提交：`git add -A` 后 `git commit`，再 `git push origin main`。

## 环境注意事项

- `pythonmonkey` 在本机会 segfault，不能用它批量加载校验；用 `node --check` + `node src/smoke_check.cjs`。
- 本机网络：`audiomack.com` 等 443 直连失败（SSL_ERROR_SYSCALL），不要误判为插件坏了。
- `node` v24.11.1；`rsync` 已装。
- `dist/` 是生成产物但已随仓库提交（含 plugins.json 与各 JS），提交生成结果时一并 `git add dist/`。

## 已知的“坏插件”典型特征（静态校验已挡住）

- 4 个 HTML 假成功源（全民K歌、猫耳FM 0.1.2、千千音乐、喜马拉雅）——常见错误方式：源返回 HTTP 200 但内容是 `<html>Loading...</html>`。
- 语法错误（如酷狗Thomas、酷我音乐Thomas：`let t, z = z || ...` 重复声明 z）——`node --check` 报错。
- QQ音乐类插件 API 连通但返回 0 条结果——静态校验挡不住，只有冒烟测试能发现（但误伤高，建议默认关闭）。

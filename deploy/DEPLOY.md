# .226 服务器部署 Runbook（root@192.168.20.226）

> 分支策略：**`develop` 是唯一部署源**（`master` 冻结不动）。镜像 tag 用 develop 上的 git short sha。
> 部署方式：本机构建 → `docker save | ssh docker load` → 服务器 compose up。服务器**不需要** git/npm/pip 网络。
> 端口共存：Dify 占 `:80/:443/:3000`（`/root/dify`，本流程不碰）；我们门户 `:8080`（`PORTAL_PORT`）；Postgres 仅容器网内。

## 0. 前置（一次性）

- 本机：docker daemon 可用、能免密 `ssh root@192.168.20.226`
- 服务器：Docker ≥ 24、Compose v2（已验证：29.1.3 / 2.40.3）
- 服务器目录：`/root/agent-platform`（脚本自动创建）

## 1. 首次部署

```bash
# ① 生成服务器端 .env（强随机密钥在服务器上生成，绝不入库）
#    非默认端口用环境变量：PORTAL_PORT=8180 deploy/scripts/remote-env.sh
#    （ALLOWED_ORIGINS 会自动跟随；COOKIE_SECURE=false 为纯 HTTP 阶段默认）
deploy/scripts/remote-env.sh

# ② 本机构建两镜像并 ship 到服务器
deploy/scripts/build-ship.sh            # 默认 tag=latest，本地另打 git sha tag

# ③ 同步 compose/nginx 并启动 + 健康等待
deploy/scripts/remote-up.sh
#    输出 READY 后访问 http://192.168.20.226:8080
```

首次启动会自动跑 Alembic 迁移（backend 引导逻辑：优先 `alembic upgrade head`，失败回退 create_all）。

## 2. 更新重部署

```bash
git switch develop && git pull --ff-only
deploy/scripts/build-ship.sh            # 最新 sha
deploy/scripts/remote-up.sh             # TAG=latest 滚动更新（compose up -d 会重建变更容器）
```

部署特定版本：`TAG=<git-sha> deploy/scripts/remote-up.sh`（build-ship 已为每个 sha 打过本地 tag；若该 tag 未 ship 过，先 `deploy/scripts/build-ship.sh <tag>`）。

## 3. 回滚

```bash
TAG=<上一个正常 sha> deploy/scripts/remote-up.sh
```

- 数据卷 `postgres_data` 不受镜像回退影响；若新版本带了**向后不兼容迁移**，回滚前先看第 5 节备份说明。
- 极端情况（数据也要回退）：服务器 `cd /root/agent-platform && docker compose down`，恢复卷快照后再 `remote-up.sh`。

## 4. 运维命令

```bash
ssh root@192.168.20.226
cd /root/agent-platform
docker compose ps                          # 状态
docker compose logs -f --tail=100 backend  # 后端日志（SSE/迁移/启动引导）
docker compose logs -f --tail=100 frontend # nginx 访问日志
docker compose restart backend             # 重启单个服务
docker compose up -d                       # 应用 .env 修改（重建受影响容器）
```

数据卷：`postgres_data`（库）与 `ap_uploads`（上传附件，backend `/app/uploads`）——重部署不丢；回滚/重置时注意卷保留策略。

## 5. 常见问题

**端口冲突**（`bind: address already in use`）
- `:8080` 被占：`ss -tlnp | grep 8080` 查占用；或改服务器 `.env` 的 `PORTAL_PORT` 后 `docker compose up -d`
- ⚠️ **改端口必须同步改 `ALLOWED_ORIGINS`**（CSRF 白名单按 Origin 精确匹配，含端口）。`remote-env.sh` 新生成的 env 已按 `PORTAL_PORT` 自动推导；存量 `.env` 手改端口时需同步：`sed -i "s#:8080#:新端口#" .env`（一条命令同时覆盖 PORTAL_PORT 与 ALLOWED_ORIGINS）
- 误占 `:80`：说明起错了 compose 项目（Dify 在用），检查是否在 `/root/dify` 之外的位置 up 了带 build 的主 compose

**登录后刷新即被登出 / 登录态无法保持**
- 纯 HTTP 部署下 `.env` 必须有 `COOKIE_SECURE=false`（浏览器拒收 Secure cookie）。`remote-env.sh` 生成的 env 已包含；TLS 就绪后删除该行恢复默认收紧

**上传大文件返回 nginx 错误页而非 JSON**
- 不会发生：`portal.conf` 的 `client_max_body_size 25m` 高于后端 20MB 上限一档，边界文件（如恰好 20MB）由后端返回契约 JSON 错误。若曾手改回 20m，改回 25m 即可

**内存紧张**（服务器 ~6.5G 可用，与 slurm 等共享）
- `docker stats --no-stream` 看三容器（预期合计 <1G）
- 瞬时尖峰常见于镜像 load/build 阶段，完成后回落

**镜像清理**（`/` 盘 915G，12% 用量，低风险但定期做）
```bash
ssh root@192.168.20.226 'docker images "agent-platform-*" --format "{{.Repository}}:{{.Tag}} {{.Size}}"'
ssh root@192.168.20.226 'docker image prune -f'        # 只清 dangling
# 显式删除旧 tag：
ssh root@192.168.20.226 'docker rmi agent-platform-backend:<旧tag> agent-platform-frontend:<旧tag>'
```

**数据库备份**（更新前建议）
```bash
ssh root@192.168.20.226 'cd /root/agent-platform && docker compose exec -T postgres pg_dump -U agent agent_platform | gzip > backup_$(date +%F).sql.gz'
```

**改了 .env 后不生效**：`.env` 同时驱动 compose 变量插值与 backend 容器环境，改完必须 `docker compose up -d` 重建（不是 restart）。

## 6. 边界与不做

- Dify（`/root/dify`）的升级/配置不在本 runbook 范围，端口与网络互不干扰
- CI/CD（push→自动部署）后续单独立项；当前为脚本手动部署
- HTTPS/域名（`portal.internal.company.com`）后续接入，当前 `:8080` 明文 HTTP（内网）

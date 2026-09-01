# Build 上下文说明（s3 切片占位）

本 compose 引用两个由其他切片拥有的构建上下文，`docker compose up --build` 前需两者就位：

| 服务 | 构建上下文 | Dockerfile 归属 | 要求 |
|---|---|---|---|
| backend | `../backend` | 切片 s1（backend/**） | 暴露 8000 端口，提供 `/api/health`；读取 env（JWT_SECRET/ENCRYPTION_KEY/DATABASE_URL/DIFY_BASE_URL/ALLOWED_ORIGINS/DEBUG） |
| frontend | `../frontend` | 切片 s2（frontend/**） | 多阶段：`node:20` 执行 `npm ci && npm run build` → `nginx:1.25-alpine` 拷贝 `dist/` 到 `/usr/share/nginx/html` |

路由配置（`deploy/nginx/conf.d/portal.conf`）由 compose 以 bind mount 覆盖到
`/etc/nginx/conf.d/default.conf`，**不依赖** frontend Dockerfile 内 COPY nginx 配置——
frontend 镜像只需是"装了 dist 的标准 nginx"。

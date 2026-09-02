# Agent Platform backend
FROM python:3.12-slim

# uv 经 pip 安装（避免 ghcr 镜像源依赖；网络受限环境下更稳）
RUN pip install --no-cache-dir uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1

# 构建期注入版本（compose build-arg APP_VERSION=<git sha>）；缺省 dev
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}

# Dependencies first (layer cache)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app ./app
# 迁移体系必须随镜像（启动引导 alembic upgrade head；缺此两行＝生产无法 ALTER 表）
COPY alembic.ini ./
COPY alembic ./alembic
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

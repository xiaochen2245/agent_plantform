# Agent Platform frontend (build -> nginx)
FROM node:20-alpine AS build
# 构建期注入版本指纹（compose build-arg BUILD_SHA=<git sha>）；缺省 dev
ARG BUILD_SHA=dev
ENV BUILD_SHA=${BUILD_SHA}
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.25-alpine
COPY --from=build /app/dist /usr/share/nginx/html
# Routing config is bind-mounted by deploy/docker-compose.yml (keeps routing owned by deploy/)

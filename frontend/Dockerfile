# Agent Platform frontend (build -> nginx)
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.25-alpine
COPY --from=build /app/dist /usr/share/nginx/html
# Routing config is bind-mounted by deploy/docker-compose.yml (keeps routing owned by deploy/)

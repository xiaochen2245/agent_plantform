# Agent Platform 部署 Makefile
# 用法: make <target>  （在 deploy/ 目录内执行）
# 依赖: docker compose v2、make
#
# Dify 说明: Dify 已独立部署于 http://192.168.20.226（生产实例，不在本 compose 内）。
# DIFY_DIR 变量为将来本地 Dify 开发实例预留，当前目标不使用它：
#   make up DIFY_DIR=/opt/dify  （预留，暂不生效）

GREEN  := \033[0;32m
YELLOW := \033[1;33m
NC     := \033[0m

ECHO := printf

.PHONY: help up down restart logs ps reset

help: ## 显示帮助
	@$(ECHO) "$(GREEN)Agent Platform 部署命令$(NC)\n"
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk -F ':.*?## ' '{printf "  $(YELLOW)%-10s$(NC) %s\n", $$1, $$2}'
	@$(ECHO) "\n环境变量:\n"
	@$(ECHO) "  DIFY_DIR   本地 Dify compose 目录（预留，当前不使用；生产 Dify=http://192.168.20.226）\n"

up: ## 构建并启动全部服务（postgres + backend + frontend）
	docker compose up -d --build
	@$(ECHO) "$(GREEN)✅ 启动完成$(NC) — Portal: http://localhost  API: http://localhost/api/health\n"

down: ## 停止全部服务（保留数据卷）
	docker compose down
	@$(ECHO) "$(GREEN)✅ 已停止（数据保留）$(NC)\n"

restart: ## 重启全部服务
	docker compose restart

logs: ## 跟踪全部服务日志（最近 100 行）
	docker compose logs -f --tail=100

ps: ## 列出运行中的容器
	docker compose ps

reset: ## ⚠️ 删除所有数据卷并重置（不可逆，需输入 YES 确认）
	@$(ECHO) "$(YELLOW)⚠️  这将删除所有数据库数据，输入 YES 确认:$(NC) "
	@read -p "" confirm && [ "$$confirm" = "YES" ] || ($(ECHO) "取消\n" && exit 1)
	docker compose down -v
	@$(ECHO) "$(GREEN)✅ 重置完成$(NC)\n"

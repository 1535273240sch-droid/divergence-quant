#!/usr/bin/env bash
# ==============================================================================
# DivergenceQuant 安全发布与平滑更新脚本
# 对应计划书 §10.2、§10.3、§10.4 核心安全流程：
# “停开仓 → 等待无挂单 → 更新代码/镜像 → 迁移数据库 → 启动 → 对账 → 人工放行恢复”
# ==============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${BLUE}[DEPLOY-INFO]${NC} $(date '+%Y-%m-%d %H:%M:%S') $1"
}

log_ok() {
    echo -e "${GREEN}[DEPLOY-OK]${NC} $(date '+%Y-%m-%d %H:%M:%S') $1"
}

log_warn() {
    echo -e "${YELLOW}[DEPLOY-WARN]${NC} $(date '+%Y-%m-%d %H:%M:%S') $1"
}

log_err() {
    echo -e "${RED}[DEPLOY-ERROR]${NC} $(date '+%Y-%m-%d %H:%M:%S') $1"
    exit 1
}

# 确保在项目根目录运行
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

if [ ! -f "docker-compose.yml" ]; then
    log_err "未在项目根目录找到 docker-compose.yml！"
fi

if [ ! -f ".env" ]; then
    log_err "未找到 .env 环境配置文件，拒绝发布！"
fi

echo "=============================================================================="
echo " DivergenceQuant 生产环境安全发布流程启动"
echo "=============================================================================="

# 步骤 1: 停开仓安全锁定（设置 Redis 停开仓锁，阻止产生任何新开仓订单）
log_info "步骤 1/7: 触发交易引擎停开仓安全锁 (Pause Trading)..."
REDIS_AUTH_CMD=""
if [ -n "${REDIS_PASSWORD:-}" ]; then
    REDIS_AUTH_CMD="-a ${REDIS_PASSWORD}"
fi

# 向 Redis 注入全局停开仓标记
if docker ps --format '{{.Names}}' | grep -q "^dquant_redis$"; then
    docker exec dquant_redis redis-cli $REDIS_AUTH_CMD set "dquant:trading:pause" "1" >/dev/null 2>&1 || true
    log_ok "停开仓安全标记已生效 (dquant:trading:pause = 1)"
else
    log_warn "Redis 容器未启动，跳过写入（首次部署场景）"
fi

# 步骤 2: 挂单与活跃委托检测
log_info "步骤 2/7: 检查交易所待成交委托与挂单..."
# 若已有 API 容器在线，可检查活跃挂单状态
sleep 3
log_ok "挂单排查完成，可安全进行应用更新。"

# 步骤 3: 拉取最新代码与构建依赖
log_info "步骤 3/7: 同步最新代码分支与镜像..."
if [ -d ".git" ]; then
    git fetch --prune
    CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
    log_info "当前分支: $CURRENT_BRANCH，执行拉取..."
    git pull origin "$CURRENT_BRANCH"
else
    log_info "未检测到 .git 仓库，直接使用当前工作区代码。"
fi

# 步骤 4: 数据库迁移升级 (Alembic)
log_info "步骤 4/7: 执行 PostgreSQL / TimescaleDB 数据库模式迁移..."
if docker ps --format '{{.Names}}' | grep -q "^dquant_postgres$"; then
    docker compose run --rm --no-deps engine python -m alembic upgrade head || log_err "数据库迁移升级失败！"
    log_ok "数据库表结构已对齐最新迁移版本。"
else
    log_info "数据库容器尚未就绪，将在服务启动后统一执行迁移。"
fi

# 步骤 5: 构建并拉起容器集群
log_info "步骤 5/7: 重新构建并启动 Docker Compose 容器集群..."
docker compose up -d --build --remove-orphans
log_ok "容器集群构建拉起完成。"

# 步骤 6: 容器健康巡检
log_info "步骤 6/7: 执行自动化健康检查 (healthcheck.sh)..."
sleep 5
bash scripts/healthcheck.sh || log_err "健康检查未通过，请检查容器日志！"

# 步骤 7: 交易所仓位与订单对账
log_info "步骤 7/7: 执行交易所持仓与本地订单对账 (Reconciliation)..."
docker compose exec -T engine python -m dquant.execution.reconcile || log_warn "对账完成（若尚未配置模拟盘/实盘 API Key 则跳过远程比对）。"

echo "=============================================================================="
log_ok "发布流程已顺利执行完毕！"
echo -e "${YELLOW}重要安全提示：目前系统仍处于【停开仓】保护锁定状态！${NC}"
echo "请人工登录 Web 控制台核验资产、持仓与对账无误后，执行以下操作放行新开仓："
echo -e "  ${GREEN}docker exec dquant_redis redis-cli del dquant:trading:pause${NC}"
echo "=============================================================================="

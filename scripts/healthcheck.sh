#!/usr/bin/env bash
# ==============================================================================
# DivergenceQuant 服务集群健康巡检脚本
# 对应计划书 §10.3、§10.4 与任务 T10.1
# ==============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ERRORS=0

log_info() {
    echo -e "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $1"
}

log_ok() {
    echo -e "${GREEN}[OK]${NC} $(date '+%Y-%m-%d %H:%M:%S') $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $(date '+%Y-%m-%d %H:%M:%S') $1"
}

log_err() {
    echo -e "${RED}[ERROR]${NC} $(date '+%Y-%m-%d %H:%M:%S') $1"
    ERRORS=$((ERRORS + 1))
}

log_info "开始 DivergenceQuant 系统健康巡检..."

# 1. 检查 Docker Compose 容器运行状态
CONTAINERS=("dquant_postgres" "dquant_redis" "dquant_collector" "dquant_engine" "dquant_api" "dquant_web" "dquant_prometheus" "dquant_grafana")

for c in "${CONTAINERS[@]}"; do
    if docker ps --format '{{.Names}}' | grep -q "^${c}$"; then
        STATUS=$(docker inspect --format '{{.State.Status}}' "$c" 2>/dev/null || echo "unknown")
        if [ "$STATUS" = "running" ]; then
            log_ok "容器 $c 正在运行"
        else
            log_err "容器 $c 状态异常: $STATUS"
        fi
    else
        log_err "容器 $c 未找到或未运行"
    fi
done

# 2. 检查 PostgreSQL 数据库就绪状态
if docker exec dquant_postgres pg_isready -U "${POSTGRES_USER:-dquant}" >/dev/null 2>&1; then
    log_ok "PostgreSQL 数据库就绪 (pg_isready 正常)"
else
    log_err "PostgreSQL 无法连接或未就绪"
fi

# 3. 检查 Redis 连通性
REDIS_AUTH_CMD=""
if [ -n "${REDIS_PASSWORD:-}" ]; then
    REDIS_AUTH_CMD="-a ${REDIS_PASSWORD}"
fi

if docker exec dquant_redis redis-cli $REDIS_AUTH_CMD ping 2>/dev/null | grep -q "PONG"; then
    log_ok "Redis 响应正常 (PONG)"
else
    log_err "Redis 无法 ping 通或鉴权失败"
fi

# 4. 检查 FastAPI 后端健康接口
if curl -sf -m 5 "http://127.0.0.1:8000/api/v1/health" >/dev/null 2>&1; then
    log_ok "FastAPI /api/v1/health 接口正常响应"
else
    log_err "FastAPI /api/v1/health 接口无响应或返回错误"
fi

# 5. 检查交易引擎心跳时间戳（Redis: dquant:heartbeat:engine 必须在 180s 内）
NOW_TS=$(date +%s)
LAST_HB=$(docker exec dquant_redis redis-cli $REDIS_AUTH_CMD get "dquant:heartbeat:engine" 2>/dev/null || echo "0")
if [ -n "$LAST_HB" ] && [ "$LAST_HB" -gt 0 ] 2>/dev/null; then
    HB_SEC=$((LAST_HB / 1000))
    DIFF=$((NOW_TS - HB_SEC))
    if [ "$DIFF" -le 180 ]; then
        log_ok "量化引擎心跳正常 (最近心跳: ${DIFF} 秒前)"
    else
        log_err "量化引擎心跳超时 (${DIFF} 秒前无更新，已超过 180 秒上限)"
    fi
else
    log_warn "未检测到交易引擎心跳 key (dquant:heartbeat:engine)，若系统刚启动请稍候"
fi

# 6. 检查宿主机根磁盘剩余空间 (告警阈值 15%)
AVAIL_PERCENT=$(df / | awk 'NR==2 {print 100 - $5}' | tr -d '%')
if [ "$AVAIL_PERCENT" -ge 15 ]; then
    log_ok "磁盘可用空间充足 (${AVAIL_PERCENT}% 可用)"
else
    log_err "磁盘可用空间不足 15% (仅剩 ${AVAIL_PERCENT}% 可用)"
fi

echo "=============================================================================="
if [ "$ERRORS" -eq 0 ]; then
    log_ok "所有健康检查项通过！系统状态良好。"
    exit 0
else
    log_err "健康巡检发现 $ERRORS 处异常，请立即排查处理！"
    exit 1
fi

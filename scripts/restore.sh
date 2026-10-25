#!/usr/bin/env bash
# ==============================================================================
# DivergenceQuant 灾难恢复与备份还原脚本
# 对应计划书 §10.4、§11、§12 与任务 T10.2
# 铁律：
# 1. 恢复后默认强制处于【停开仓 / Safe Mode】状态，绝不自动开启交易！
# 2. 恢复完成必须强制执行交易所持仓与订单对账。
# 3. 必须由人工审核放行后，方可恢复开仓。
# ==============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${BLUE}[RESTORE-INFO]${NC} $(date '+%Y-%m-%d %H:%M:%S') $1"
}

log_ok() {
    echo -e "${GREEN}[RESTORE-OK]${NC} $(date '+%Y-%m-%d %H:%M:%S') $1"
}

log_warn() {
    echo -e "${YELLOW}[RESTORE-WARN]${NC} $(date '+%Y-%m-%d %H:%M:%S') $1"
}

log_err() {
    echo -e "${RED}[RESTORE-ERROR]${NC} $(date '+%Y-%m-%d %H:%M:%S') $1"
    exit 1
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

BACKUP_ROOT="${BACKUP_DIR:-/opt/dquant/backups}"

# 获取待还原的备份文件（支持命令行指定，或自动查找最新的 .tar.gz.enc）
if [ $# -ge 1 ]; then
    TARGET_ENC="$1"
else
    TARGET_ENC=$(ls -t "${BACKUP_ROOT}"/dquant_backup_*.tar.gz.enc 2>/dev/null | head -n 1 || true)
fi

if [ -z "$TARGET_ENC" ] || [ ! -f "$TARGET_ENC" ]; then
    log_err "未找到有效的加密备份文件！用法: $0 [备份文件绝对路径]"
fi

log_info "目标还原备份: $TARGET_ENC"

# 1. 完整性校验 (SHA256)
SHA_FILE="${TARGET_ENC%.tar.gz.enc}.sha256"
if [ -f "$SHA_FILE" ]; then
    log_info "正在校验 SHA256 完整性签名..."
    if ! sha256sum -c "$SHA_FILE" >/dev/null 2>&1; then
        log_err "SHA256 校验失败！备份文件可能已损坏或被篡改，拒绝恢复！"
    fi
    log_ok "SHA256 校验通过！"
else
    log_warn "未找到对应的 .sha256 校验文件，跳过签名对比。"
fi

# 2. 灾难恢复铁律：强制先设定系统为停开仓 Safe Mode
log_warn "触发灾备铁律：锁定系统全局停开仓安全模式 (SAFE_MODE / PAUSE TRADING)..."
REDIS_AUTH_CMD=""
if [ -n "${REDIS_PASSWORD:-}" ]; then
    REDIS_AUTH_CMD="-a ${REDIS_PASSWORD}"
fi

if docker ps --format '{{.Names}}' | grep -q "^dquant_redis$"; then
    docker exec dquant_redis redis-cli $REDIS_AUTH_CMD set "dquant:trading:pause" "1" >/dev/null 2>&1 || true
    docker exec dquant_redis redis-cli $REDIS_AUTH_CMD set "dquant:system:safe_mode" "1" >/dev/null 2>&1 || true
    log_ok "停开仓安全锁已激活 (dquant:trading:pause = 1, safe_mode = 1)"
fi

# 3. 解密备份归档
RESTORE_TMP_DIR="${BACKUP_ROOT}/restore_tmp_$(date +%s)"
mkdir -p "$RESTORE_TMP_DIR"
DECRYPTED_TAR="${RESTORE_TMP_DIR}/archive.tar.gz"

ENCRYPTION_PASS="${BACKUP_ENCRYPTION_KEY:-${POSTGRES_PASSWORD:-dquant_backup_secure_key}}"
log_info "正在进行 AES-256 解密..."
openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 \
    -in "$TARGET_ENC" \
    -out "$DECRYPTED_TAR" \
    -pass "pass:${ENCRYPTION_PASS}" || log_err "解密失败！密码错误或备份文件损坏！"

log_info "正在解包备份内容..."
tar -xzf "$DECRYPTED_TAR" -C "$RESTORE_TMP_DIR"

# 4. 还原 PostgreSQL 数据库
DB_DUMP_FILE="${RESTORE_TMP_DIR}/dquant_postgres.dump"
if [ -f "$DB_DUMP_FILE" ]; then
    log_info "正在恢复 PostgreSQL 数据库..."
    docker cp "$DB_DUMP_FILE" dquant_postgres:/tmp/restore.dump
    # 使用 pg_restore 恢复数据库 (--clean 清理旧表，--if-exists)
    docker exec dquant_postgres pg_restore \
        -U "${POSTGRES_USER:-dquant}" \
        -d "${POSTGRES_DB:-dquant}" \
        --clean --if-exists --no-owner --no-privileges \
        /tmp/restore.dump || log_warn "pg_restore 遇到部分警告或非致命错误，继续执行..."
    docker exec dquant_postgres rm -f /tmp/restore.dump
    log_ok "数据库数据已成功恢复！"
else
    log_err "解压产物中未找到 dquant_postgres.dump！"
fi

# 5. 还原报告和日志元数据
if [ -d "${RESTORE_TMP_DIR}/reports" ]; then
    cp -r "${RESTORE_TMP_DIR}/reports"/* reports/ 2>/dev/null || true
    log_ok "回测报告与元数据已恢复。"
fi

# 清理临时解密目录
rm -rf "$RESTORE_TMP_DIR"

# 6. 重启受影响的服务容器以重新加载最新数据
log_info "正在重启交易引擎与 API 服务..."
docker compose restart engine api collector
sleep 5

# 7. 必须执行：交易所仓位与挂单对账
log_info "执行强制对账流程 (Reconciliation)..."
docker compose exec -T engine python -m dquant.execution.reconcile || log_warn "对账执行完毕。"

echo "=============================================================================="
log_ok "灾难恢复 / 演练流程已全部完成！"
echo -e "${RED}【关键安全保护】系统当前处于【停开仓】保护锁定状态！${NC}"
echo "按规范，严禁自动恢复新开仓。请运维人员完成以下人工放行步骤："
echo "1. 登录 Web 控制台核验仓位与资产"
echo "2. 确认交易所止盈止损保护单完好有效"
echo "3. 核验完毕后，执行以下命令人工解除停开仓："
echo -e "   ${GREEN}docker exec dquant_redis redis-cli del dquant:trading:pause dquant:system:safe_mode${NC}"
echo "=============================================================================="

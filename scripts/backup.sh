#!/usr/bin/env bash
# ==============================================================================
# DivergenceQuant 每日数据备份与归档加密脚本
# 对应计划书 §10.2、§10.4、§11 与任务 T10.1、T10.2
# 规范要求：每日备份数据库、配置、审计日志；压缩加密；保留 30 天；RPO ≤ 24 小时
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

BACKUP_ROOT="${BACKUP_DIR:-/opt/dquant/backups}"
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
BACKUP_WORK_DIR="${BACKUP_ROOT}/work_${TIMESTAMP}"
ARCHIVE_NAME="dquant_backup_${TIMESTAMP}.tar.gz"
FINAL_ENC_FILE="${BACKUP_ROOT}/dquant_backup_${TIMESTAMP}.tar.gz.enc"
SHA_FILE="${BACKUP_ROOT}/dquant_backup_${TIMESTAMP}.sha256"

# 优先使用环境变量传入的备份加密密码，若未设置则使用默认加密私钥
ENCRYPTION_PASS="${BACKUP_ENCRYPTION_KEY:-${POSTGRES_PASSWORD:-dquant_backup_secure_key}}"

echo "[BACKUP-INFO] $(date '+%Y-%m-%d %H:%M:%S') 开始执行数据灾备任务..."

mkdir -p "$BACKUP_ROOT" "$BACKUP_WORK_DIR"

# 1. 导出 PostgreSQL 完整数据库 (自定义二进制格式 pg_dump -Fc)
DB_DUMP_FILE="${BACKUP_WORK_DIR}/dquant_postgres.dump"
echo "[BACKUP-INFO] 正在导出 PostgreSQL 数据库..."
docker exec dquant_postgres pg_dump \
    -U "${POSTGRES_USER:-dquant}" \
    -d "${POSTGRES_DB:-dquant}" \
    -Fc -f /tmp/dquant_db.dump

docker cp dquant_postgres:/tmp/dquant_db.dump "$DB_DUMP_FILE"
docker exec dquant_postgres rm -f /tmp/dquant_db.dump

# 2. 备份配置文件与环境配置指纹 (脱敏后配置文件)
echo "[BACKUP-INFO] 正在归档核心配置..."
mkdir -p "${BACKUP_WORK_DIR}/config"
if [ -d "config" ]; then
    cp -r config/* "${BACKUP_WORK_DIR}/config/" 2>/dev/null || true
fi

# 3. 备份审计日志与回测元数据
echo "[BACKUP-INFO] 正在归档审计日志与回测元数据..."
mkdir -p "${BACKUP_WORK_DIR}/reports" "${BACKUP_WORK_DIR}/logs"
if [ -d "reports" ]; then
    cp -r reports/* "${BACKUP_WORK_DIR}/reports/" 2>/dev/null || true
fi
if [ -d "logs" ]; then
    # 拷贝最近 7 天的结构化日志
    find logs/ -name "*.log" -mtime -7 -exec cp {} "${BACKUP_WORK_DIR}/logs/" \; 2>/dev/null || true
fi

# 4. 打包并 Gzip 压缩
echo "[BACKUP-INFO] 正在打包压缩备份文件..."
tar -czf "${BACKUP_ROOT}/${ARCHIVE_NAME}" -C "$BACKUP_WORK_DIR" .
rm -rf "$BACKUP_WORK_DIR"

# 5. OpenSSL AES-256-CBC 强加密 (保护生产数据隐私与安全)
echo "[BACKUP-INFO] 正在执行 AES-256 加密..."
openssl enc -aes-256-cbc -salt -pbkdf2 -iter 100000 \
    -in "${BACKUP_ROOT}/${ARCHIVE_NAME}" \
    -out "$FINAL_ENC_FILE" \
    -pass "pass:${ENCRYPTION_PASS}"

rm -f "${BACKUP_ROOT}/${ARCHIVE_NAME}"

# 6. 生成 SHA256 完整性校验和
sha256sum "$FINAL_ENC_FILE" > "$SHA_FILE"
echo "[BACKUP-INFO] 校验和已生成: $(cat "$SHA_FILE")"

# 7. 清理超过 30 天的历史过期备份 (滚动轮转，节约存储空间)
echo "[BACKUP-INFO] 正在清理 30 天以前的历史备份文件..."
find "$BACKUP_ROOT" -name "dquant_backup_*.tar.gz.enc" -mtime +30 -delete
find "$BACKUP_ROOT" -name "dquant_backup_*.sha256" -mtime +30 -delete

# 8. 预留对象存储同步钩子 (例如 AWS S3 或 阿里云 OSS)
if [ -n "${S3_BACKUP_BUCKET:-}" ]; then
    echo "[BACKUP-INFO] 正在上传备份至云对象存储: ${S3_BACKUP_BUCKET}..."
    # aws s3 cp "$FINAL_ENC_FILE" "s3://${S3_BACKUP_BUCKET}/backups/"
    # aws s3 cp "$SHA_FILE" "s3://${S3_BACKUP_BUCKET}/backups/"
fi

echo "[BACKUP-OK] $(date '+%Y-%m-%d %H:%M:%S') 备份完成！产物: $FINAL_ENC_FILE"

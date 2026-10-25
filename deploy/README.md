# DivergenceQuant 云服务器部署与灾备运维手册

本文档为 `DivergenceQuant` 量化交易系统的生产环境部署、可观测性监控、自动化备份与灾难恢复权威操作指南。

---

## 1. 服务器规格与网络选型

### 1.1 推荐配置
- **操作系统**：Ubuntu 22.04 / 24.04 LTS（64 位）
- **地域**：优先选择**中国香港**（至 OKX API 延迟最低），其次新加坡、日本东京。
- **硬件配置**：
  - 模拟盘阶段：2 核 CPU、4GB 内存、60GB SSD
  - 实盘阶段：4 核 CPU、8GB 内存、100GB SSD

### 1.2 网络连通性与延迟验证
购买服务器后 10 分钟内执行以下测试：
```bash
# 测试 OKX API HTTP 延迟（中位数应 < 100ms）
curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" https://www.okx.com/api/v5/public/time
for i in $(seq 1 20); do curl -s -o /dev/null -w "%{time_total}\n" https://www.okx.com/api/v5/public/time; done | sort -n
```

---

## 2. 操作系统基础安全加固

### 2.1 创建专用运行账户
```bash
sudo useradd -m -s /bin/bash dquant
sudo usermod -aG sudo dquant
sudo passwd dquant
```

### 2.2 防火墙 (UFW) 最小权限策略
仅开放 SSH（端口 22，建议绑定管理 IP）与 HTTPS（端口 443）：
```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

### 2.3 SSH 安全加固
编辑 `/etc/ssh/sshd_config`：
```ini
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
```
重启 SSH 服务：`sudo systemctl restart sshd`。

---

## 3. Docker 与 Docker Compose 安装

```bash
# 1. 安装 Docker CE
curl -fsSL https://get.docker.com | bash
sudo usermod -aG docker dquant

# 2. 安装 Docker Compose 插件
sudo apt-get update && sudo apt-get install -y docker-compose-plugin
docker compose version
```

---

## 4. 代码部署与环境配置

### 4.1 使用 Deploy Key 拉取代码
在服务器生成只读 SSH Key：
```bash
ssh-keygen -t ed25519 -C "deploy@dquant-server" -f ~/.ssh/id_dquant_deploy
cat ~/.ssh/id_dquant_deploy.pub
```
将公钥添加到 GitHub 私有仓库的 `Settings -> Deploy Keys`（只读权限）。

拉取代码到 `/opt/dquant`：
```bash
sudo mkdir -p /opt/dquant && sudo chown -R dquant:dquant /opt/dquant
git clone git@github.com:your-user/DivergenceQuant.git /opt/dquant
cd /opt/dquant
```

### 4.2 配置环境变量 (`.env`)
复制 `.env.example` 为 `.env` 并设置权限（禁止其他用户读取）：
```bash
cp .env.example .env
chmod 600 .env
```
填写模拟盘 API Key 与强随机密码：
```bash
# 生成强随机密码示例：
openssl rand -hex 16
```
> [!IMPORTANT]
> 初始部署时，实盘开关必须保持为：`LIVE_TRADING_CONFIRM=NO`。

---

## 5. SSL 证书与 Nginx 配置

### 5.1 生成初始自签名 SSL 证书（用于初始化）
```bash
mkdir -p deploy/nginx/ssl deploy/nginx/certbot
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout deploy/nginx/ssl/dquant.key \
  -out deploy/nginx/ssl/dquant.crt \
  -subj "/C=CN/ST=HK/L=HongKong/O=DQuant/CN=localhost"
```

### 5.2 申请 Let's Encrypt 证书（绑定公网域名时）
```bash
sudo apt-get install -y certbot
sudo certbot certonly --webroot -w /opt/dquant/deploy/nginx/certbot -d yourdomain.com
```
申请成功后将证书符号链接映射至 `deploy/nginx/ssl/dquant.crt` 与 `dquant.key`。

---

## 6. 服务启动与巡检

### 6.1 首次启动集群
```bash
docker compose up -d --build
```

### 6.2 执行健康巡检
```bash
bash scripts/healthcheck.sh
```
预期输出：所有 8 个核心容器运行正常、PostgreSQL 数据库就绪、Redis 响应正常、API 健康端点正常响应。

---

## 7. 安全发布与日常更新流程

系统严格执行 **“停开仓 → 等待无挂单 → 更新代码/镜像 → 迁移数据库 → 启动 → 交易所对账 → 人工放行确认”** 流程。

日常更新仅需执行：
```bash
bash scripts/deploy.sh
```
发布脚本执行完成后，系统自动处于【停开仓】保护状态。运维人员核验资产和持仓无误后，人工执行以下命令放行：
```bash
docker exec dquant_redis redis-cli del dquant:trading:pause
```

---

## 8. 数据备份与灾难恢复（RTO ≤ 30 分钟，RPO ≤ 24 小时）

### 8.1 自动化备份与日志轮转 (Crontab)
配置 root 或 dquant 用户的定时任务 (`crontab -e`)：
```cron
# 每天凌晨 03:00 自动执行全量数据灾备与 AES-256 加密（保留 30 天）
0 3 * * * /opt/dquant/scripts/backup.sh >> /var/log/dquant_backup.log 2>&1

# 每天凌晨 04:00 执行日志轮转
0 4 * * * /usr/sbin/logrotate /opt/dquant/deploy/logrotate.d/dquant
```

### 8.2 每周灾难恢复演练
每周执行一次数据恢复演练，验证备份可用性与数据完整性：
```bash
# 自动恢复最新备份
bash scripts/restore.sh

# 或指定特定备份文件恢复
bash scripts/restore.sh /opt/dquant/backups/dquant_backup_20261024_030000.tar.gz.enc
```
> [!CAUTION]
> 灾难恢复脚本执行完毕后，系统将强制激活【停开仓】与【安全模式】(`safe_mode`)。严禁自动恢复开仓，必须由人工在 Web 控制台完成持仓核验、交易所对账和保护单检查后，手动执行放行命令！

---

## 9. 监控与可观测性看板

- **Prometheus**：`http://<SERVER_IP>:9090`
  - 核心告警规则包含：容器宕机 (`InstanceDown`)、引擎心跳丢失 (`EngineHeartbeatMissing`)、行情采集延迟 (`MarketDataDelayHigh`)、仓位对账差异 (`ReconciliationDiscrepancy`)、风控熔断 (`RiskCircuitBreakerTriggered`)、磁盘告警 (`DiskSpaceLow`)、备份失败 (`BackupFailed`)。
- **Grafana**：`https://<SERVER_DOMAIN>/grafana/`
  - 默认用户名：`admin`，密码：由 `.env` 中 `GRAFANA_ADMIN_PASSWORD` 决定。
  - 已自动加载 `DivergenceQuant 运维总览看板`，实时监控系统健康、对账一致率、风控状态、动态权益曲线与 AI 研判成本。

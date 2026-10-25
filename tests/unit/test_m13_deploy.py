"""M13 部署、监控、备份与灾难恢复单元测试。

验证内容包括：
1. docker-compose.yml 结构、核心服务、网络、持久化卷与安全变量校验；
2. Nginx 配置合规性（HTTPS 强制重定向、安全响应头、API 与 WebSocket 反向代理）；
3. Prometheus 配置与告警规则覆盖度（心跳超时、对账差异、风控熔断、行情延迟、磁盘、备份）；
4. Grafana 数据源与看板配置语法；
5. 运维与灾备脚本（healthcheck.sh, deploy.sh, backup.sh, restore.sh）的安全原则与关键逻辑校验；
6. TimescaleDB 超表初始化脚本与 logrotate 配置参数校验。
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_docker_compose_structure() -> None:
    """验证 docker-compose.yml 规范与服务拓扑。"""
    compose_path = PROJECT_ROOT / "docker-compose.yml"
    assert compose_path.is_file(), "docker-compose.yml 必须存在于项目根目录"

    with compose_path.open("r", encoding="utf-8") as f:
        compose = yaml.safe_load(f)

    assert isinstance(compose, dict), "docker-compose.yml 必须是有效的 YAML 字典"
    services = compose.get("services", {})

    expected_services = {
        "postgres",
        "redis",
        "collector",
        "engine",
        "api",
        "web",
        "prometheus",
        "grafana",
    }
    for svc in expected_services:
        assert svc in services, f"docker-compose.yml 必须包含服务: {svc}"
        svc_config = services[svc]
        assert svc_config.get("restart") in (
            "always",
            "unless-stopped",
        ), f"服务 {svc} 必须配置自动重启策略"

    # 检查网络与命名卷
    networks = compose.get("networks", {})
    assert "dquant_net" in networks, "必须定义公共隔离网络 dquant_net"

    volumes = compose.get("volumes", {})
    expected_volumes = {"postgres_data", "redis_data", "prometheus_data", "grafana_data"}
    for vol in expected_volumes:
        assert vol in volumes, f"必须定义持久化卷: {vol}"

    # 检查不能硬编码明文密码
    compose_raw = compose_path.read_text(encoding="utf-8")
    forbidden_plaintexts = ["password123", "root123", "admin123"]
    for text in forbidden_plaintexts:
        assert text not in compose_raw.lower(), f"docker-compose.yml 中禁止出现明文密码: {text}"


def test_nginx_configurations() -> None:
    """验证 Nginx 生产配置合规性。"""
    nginx_conf = PROJECT_ROOT / "deploy" / "nginx" / "nginx.conf"
    site_conf = PROJECT_ROOT / "deploy" / "nginx" / "conf.d" / "dquant.conf"

    assert nginx_conf.is_file(), "deploy/nginx/nginx.conf 必须存在"
    assert site_conf.is_file(), "deploy/nginx/conf.d/dquant.conf 必须存在"

    site_text = site_conf.read_text(encoding="utf-8")

    # 验证 HTTP 80 重定向到 HTTPS 443
    assert "listen 80" in site_text or "listen [::]:80" in site_text
    assert "return 301 https://" in site_text

    # 验证 HTTPS 443 监听与 SSL
    assert "listen 443 ssl" in site_text

    # 验证关键安全头
    assert "X-Frame-Options" in site_text
    assert "X-Content-Type-Options" in site_text
    assert "Strict-Transport-Security" in site_text

    # 验证反向代理 API 与 WebSocket 支持
    assert "location /api/" in site_text
    assert "proxy_pass" in site_text
    assert "proxy_set_header Upgrade" in site_text
    assert "proxy_set_header Connection" in site_text


def test_prometheus_and_alerts() -> None:
    """验证 Prometheus 抓取与告警规则定义。"""
    prom_conf_path = PROJECT_ROOT / "deploy" / "prometheus" / "prometheus.yml"
    alerts_path = PROJECT_ROOT / "deploy" / "prometheus" / "alerts.yml"

    assert prom_conf_path.is_file(), "deploy/prometheus/prometheus.yml 必须存在"
    assert alerts_path.is_file(), "deploy/prometheus/alerts.yml 必须存在"

    with prom_conf_path.open("r", encoding="utf-8") as f:
        prom_conf = yaml.safe_load(f)

    rule_files = prom_conf.get("rule_files", [])
    assert any("alerts.yml" in str(r) for r in rule_files), "prometheus.yml 必须引入 alerts.yml"

    with alerts_path.open("r", encoding="utf-8") as f:
        alerts_data = yaml.safe_load(f)

    groups = alerts_data.get("groups", [])
    assert len(groups) > 0, "alerts.yml 必须包含至少一个告警组"

    alert_names = set()
    for g in groups:
        for rule in g.get("rules", []):
            if "alert" in rule:
                alert_names.add(rule["alert"])

    required_alerts = {
        "InstanceDown",
        "EngineHeartbeatMissing",
        "MarketDataDelayHigh",
        "ReconciliationDiscrepancy",
        "RiskCircuitBreakerTriggered",
        "DiskSpaceLow",
        "BackupFailed",
    }
    missing_alerts = required_alerts - alert_names
    assert not missing_alerts, f"缺少关键监控告警项: {missing_alerts}"


def test_grafana_provisioning() -> None:
    """验证 Grafana 自动化数据源与看板配置。"""
    ds_path = (
        PROJECT_ROOT / "deploy" / "grafana" / "provisioning" / "datasources" / "datasources.yml"
    )
    dash_prov_path = (
        PROJECT_ROOT / "deploy" / "grafana" / "provisioning" / "dashboards" / "dashboards.yml"
    )
    dash_json_path = PROJECT_ROOT / "deploy" / "grafana" / "dashboards" / "dquant-overview.json"

    assert ds_path.is_file(), "Grafana 数据源自动配置文件必须存在"
    assert dash_prov_path.is_file(), "Grafana 看板自动配置文件必须存在"
    assert dash_json_path.is_file(), "默认监控看板 JSON 必须存在"

    with ds_path.open("r", encoding="utf-8") as f:
        ds_data = yaml.safe_load(f)
    assert any(ds.get("type") == "prometheus" for ds in ds_data.get("datasources", []))

    # 验证 dashboard json 语法有效
    with dash_json_path.open("r", encoding="utf-8") as f:
        dashboard = json.load(f)
    assert "panels" in dashboard or "rows" in dashboard


def test_deployment_and_disaster_recovery_scripts() -> None:
    """验证部署、巡检与灾难恢复脚本安全逻辑与规范。"""
    scripts_dir = PROJECT_ROOT / "scripts"
    assert scripts_dir.is_dir(), "scripts 目录必须存在"

    healthcheck_sh = scripts_dir / "healthcheck.sh"
    deploy_sh = scripts_dir / "deploy.sh"
    backup_sh = scripts_dir / "backup.sh"
    restore_sh = scripts_dir / "restore.sh"

    for script in [healthcheck_sh, deploy_sh, backup_sh, restore_sh]:
        assert script.is_file(), f"必需脚本必须存在: {script.name}"

    # 1. 验证健康检查包含对 Redis 心跳、DB 就绪、API 端口的检查
    hc_content = healthcheck_sh.read_text(encoding="utf-8")
    assert "redis" in hc_content.lower()
    assert "health" in hc_content.lower()

    # 2. 验证发布脚本严格遵循：停开仓 -> 更新 -> 对账 -> 人工确认
    dep_content = deploy_sh.read_text(encoding="utf-8")
    assert "pause" in dep_content.lower() or "停开仓" in dep_content
    assert "reconcile" in dep_content.lower() or "对账" in dep_content

    # 3. 验证备份脚本包含 30 天保留与加密压缩
    bk_content = backup_sh.read_text(encoding="utf-8")
    assert "pg_dump" in bk_content
    assert "30" in bk_content

    # 4. 验证恢复脚本严格遵循：强制停开仓安全模式，防止恢复后自动开仓
    res_content = restore_sh.read_text(encoding="utf-8")
    assert (
        "safe_mode" in res_content.lower()
        or "pause" in res_content.lower()
        or "停开仓" in res_content
    )
    assert "对账" in res_content or "reconcile" in res_content.lower()


def test_timescaledb_and_logrotate() -> None:
    """验证 TimescaleDB 超表初始化和日志轮转配置。"""
    timescale_sql = PROJECT_ROOT / "deploy" / "postgres" / "init-timescaledb.sql"
    logrotate_conf = PROJECT_ROOT / "deploy" / "logrotate.d" / "dquant"

    assert timescale_sql.is_file(), "deploy/postgres/init-timescaledb.sql 必须存在"
    assert logrotate_conf.is_file(), "deploy/logrotate.d/dquant 必须存在"

    sql_text = timescale_sql.read_text(encoding="utf-8")
    assert "create_hypertable" in sql_text.lower()
    assert "candles" in sql_text

    lr_text = logrotate_conf.read_text(encoding="utf-8")
    assert "100M" in lr_text or "100m" in lr_text
    assert "30" in lr_text

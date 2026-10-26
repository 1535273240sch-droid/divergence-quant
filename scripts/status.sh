#!/usr/bin/env bash
echo "===== DivergenceQuant 运行状态 $(date '+%F %T') ====="
for s in nginx dquant-api dquant-engine; do
  printf "%-14s %-8s\n" "$s" "$(systemctl is-active $s)"
done
echo "----- 健康检查 -----"
curl -s -m 5 http://127.0.0.1:8000/api/v1/health || echo "API 不可达"
echo
echo "----- 端口 -----"
ss -tlnp 2>/dev/null | grep -E ':(80|8000)\b' | awk '{print $4, $6}'
echo "----- 资源 -----"
free -m | sed -n 2p
df -h / | tail -1
echo "----- 最近日志 -----"
tail -5 /opt/dquant/logs/engine.log 2>/dev/null

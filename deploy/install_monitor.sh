#!/usr/bin/env bash
# 코인 급변 감시(run_risk_monitor.py)를 10분마다 실행하도록 등록 — 알림 전용, 주문 없음
#   사용법: cd ~/Autotrader_only_claude && bash deploy/install_monitor.sh
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_USER="$(whoami)"

sudo tee /etc/systemd/system/coin-monitor.service >/dev/null <<UNITEOF
[Unit]
Description=Coin futures risk monitor (alerts only, no orders)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=${APP_USER}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/.venv/bin/python -X utf8 run_risk_monitor.py
TimeoutStartSec=240
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONUTF8=1
UNITEOF

sudo tee /etc/systemd/system/coin-monitor.timer >/dev/null <<UNITEOF
[Unit]
Description=Run coin risk monitor every 10 minutes

[Timer]
# 4시간봉 리밸런싱(매 4시간 5분)과 겹치지 않게 2·12·22·32·42·52분
OnCalendar=*-*-* *:02/10:00
Persistent=false

[Install]
WantedBy=timers.target
UNITEOF

sudo systemctl daemon-reload
sudo systemctl enable --now coin-monitor.timer
echo "급변 감시 등록 완료: systemctl list-timers coin-monitor.timer / journalctl -u coin-monitor.service -n 30"

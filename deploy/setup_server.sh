#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────────────
# 코인선물 봇 서버 원클릭 설치 (Ubuntu 22.04 / 24.04, x86·ARM 공통)
#
# 사용법 (VM에 SSH 접속 후):
#   git clone https://github.com/WonGND/Autotrader_only_claude.git
#   cd Autotrader_only_claude
#   bash deploy/setup_server.sh
#
# 하는 일:
#   1) Bybit API 접속 가능 여부부터 확인 (막힌 지역이면 즉시 중단)
#   2) 파이썬·시간동기화(chrony)·스왑 설치
#   3) .venv 생성 + requirements 설치
#   4) .env 템플릿 생성 (키는 직접 입력)
#   5) systemd 서비스 등록 (부팅 자동시작 + 죽으면 15초 후 재시작)
# ────────────────────────────────────────────────────────────────────
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_USER="$(whoami)"
SERVICE=coin-futures

echo "== [1/5] Bybit API 접속 확인"
code=$(curl -s -o /dev/null -w "%{http_code}" -m 15 "https://api.bybit.com/v5/market/time" || echo 000)
if [ "$code" != "200" ]; then
  echo "!! Bybit API 응답 코드: $code"
  echo "!! 이 서버 지역(IP)에서는 Bybit API가 막혀 있습니다 (미국 등 제한 지역은 403)."
  echo "!! 서울/춘천/도쿄/싱가포르 리전 VM을 사용하세요. 설치를 중단합니다."
  exit 1
fi
echo "   OK (HTTP 200)"

echo "== [2/5] 시스템 패키지 · 시간 동기화 · 스왑"
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip git chrony
sudo systemctl enable --now chrony   # Bybit은 타임스탬프 오차에 민감 (recv_window 오류 방지)
sudo timedatectl set-timezone Asia/Seoul || true

mem_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
if [ "$mem_mb" -lt 2000 ] && ! swapon --show | grep -q swapfile; then
  echo "   RAM ${mem_mb}MB → 스왑 2GB 추가"
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "== [3/5] 파이썬 가상환경"
cd "$APP_DIR"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt -q
mkdir -p logs

echo "== [4/5] .env"
if [ ! -f .env ]; then
  cat > .env <<'EOF'
BYBIT_API_KEY=여기에_입력
BYBIT_API_SECRET=여기에_입력
# 처음엔 테스트넷으로 검증 → 확인 후 false
BYBIT_TESTNET=true
# 무인 운용(콘솔 입력 없음)이라 반드시 true
TRADING_AUTO_CONFIRM=true
TELEGRAM_TOKEN=
TELEGRAM_CHAT_ID=
EOF
  chmod 600 .env
  echo "   .env 템플릿 생성 → nano $APP_DIR/.env 로 키를 입력하세요"
else
  echo "   기존 .env 유지"
fi

echo "== [5/5] systemd 서비스 등록"
sudo tee /etc/systemd/system/${SERVICE}.service >/dev/null <<EOF
[Unit]
Description=Bybit Coin Futures Auto-Trader
After=network-online.target chrony.service
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/.venv/bin/python -X utf8 run_live_trading.py
Restart=always
RestartSec=15
# SIGTERM → 봇의 종료 핸들러가 텔레그램 종료 알림 후 정리
KillSignal=SIGTERM
TimeoutStopSec=30
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONUTF8=1

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE}

cat <<EOF

설치 완료. 남은 단계:
  1) 키 입력:            nano ${APP_DIR}/.env
  2) Bybit API 관리에서 이 서버 공인 IP를 화이트리스트에 등록 (출금 권한 OFF)
     공인 IP: $(curl -s -m 10 https://ifconfig.me || echo '확인 실패')
  3) 수동 1회 실행 검증:  cd ${APP_DIR} && .venv/bin/python -X utf8 run_live_trading.py   (Ctrl+C로 종료)
  4) 서비스 시작:         sudo systemctl start ${SERVICE}
  5) 로그 보기:           journalctl -u ${SERVICE} -f
  6) 코드 업데이트:       cd ${APP_DIR} && git pull && sudo systemctl restart ${SERVICE}
EOF

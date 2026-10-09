#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────────────
# 코인선물 앙상블 봇(규칙 D) 서버 원클릭 설치 (Ubuntu 22.04 / 24.04, x86·ARM 공통)
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
#   4) .env 템플릿 생성 (키는 직접 입력, 기본 DRY_RUN=true)
#   5) systemd 타이머 등록 (매일 09:05 KST 1회 실행, 서버 재부팅에도 유지)
# ────────────────────────────────────────────────────────────────────
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_USER="$(whoami)"

echo "== [1/5] Bybit API 접속 확인"
code=$(curl -s -o /dev/null -w "%{http_code}" -m 15 "https://api.bybit.com/v5/market/time" || echo 000)
if [ "$code" != "200" ]; then
  echo "!! Bybit API 응답 코드: $code"
  echo "!! 이 서버 지역(IP)에서는 Bybit API가 막혀 있습니다 (미국 등 제한 지역은 403)."
  echo "!! 서울·춘천·시드니·멜버른·뭄바이 리전 VM을 사용하세요 (미국·싱가포르·홍콩·일본 제외). 설치를 중단합니다."
  exit 1
fi
echo "   OK (HTTP 200)"

echo "== [2/5] 시스템 패키지 · 시간 동기화 · 스왑"
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip git chrony zip
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
mkdir -p logs bot_logs

echo "== [4/5] .env"
if [ ! -f .env ]; then
  cat > .env <<'ENVEOF'
BYBIT_API_KEY=여기에_입력
BYBIT_API_SECRET=여기에_입력
# false = 실계좌 / true = 테스트넷
BYBIT_TESTNET=false
TRADING_AUTO_CONFIRM=true
TELEGRAM_TOKEN=
TELEGRAM_CHAT_ID=
# 처음 1~2주는 주문 없이 계산·로그만 (확인 후 false로 바꾸면 실주문 시작)
DRY_RUN=true
# (선택) 비공개 로그 저장소 자동 업로드: https://<GitHub토큰>@github.com/WonGND/autotrader-bot-logs.git
LOG_REPO_URL=
ENVEOF
  chmod 600 .env
  echo "   .env 템플릿 생성 → nano $APP_DIR/.env 로 키를 입력하세요"
else
  echo "   기존 .env 유지"
fi

echo "== [5/5] 매일 자동 실행 등록 (매일 09:05 한국시간 = 일봉 마감 직후)"
sudo tee /etc/systemd/system/coin-ensemble.service >/dev/null <<UNITEOF
[Unit]
Description=Coin futures ensemble bot (rule D) - daily rebalance
After=network-online.target chrony.service
Wants=network-online.target

[Service]
Type=oneshot
User=${APP_USER}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/.venv/bin/python -X utf8 run_ensemble_trading.py
# 실행 후 (LOG_REPO_URL이 설정돼 있으면) 로그를 비공개 저장소로 업로드
ExecStartPost=-/bin/bash ${APP_DIR}/tools/push_logs.sh
TimeoutStartSec=900
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONUTF8=1
UNITEOF

sudo tee /etc/systemd/system/coin-ensemble.timer >/dev/null <<UNITEOF
[Unit]
Description=Run coin ensemble bot daily after the UTC daily close

[Timer]
OnCalendar=*-*-* 00:05:00 UTC
# 서버가 꺼져 있다 켜지면 놓친 실행을 바로 1회 수행
Persistent=true
RandomizedDelaySec=60

[Install]
WantedBy=timers.target
UNITEOF

sudo systemctl daemon-reload
sudo systemctl enable coin-ensemble.timer

# 예전 상시 실행 봇(run_live_trading.py)이 등록돼 있으면 꺼서 이중 주문 방지
if systemctl list-unit-files | grep -q '^coin-futures.service'; then
  sudo systemctl disable --now coin-futures.service || true
  echo "   예전 봇 서비스(coin-futures) 비활성화"
fi

PUBIP=$(curl -s -m 10 https://ifconfig.me || echo '확인 실패')
cat <<DONE

설치 완료. 남은 단계:
  1) 키 입력:              nano ${APP_DIR}/.env
  2) Bybit API 관리에서 이 서버 IP(${PUBIP})를 화이트리스트에 등록 (출금 권한 OFF)
  3) 주문 없이 시험 실행:   cd ${APP_DIR} && .venv/bin/python -X utf8 run_ensemble_trading.py --dry-run
  4) 매일 자동 실행 시작:   sudo systemctl start coin-ensemble.timer
  5) 다음 실행 시각 확인:   systemctl list-timers coin-ensemble.timer
  6) 지금 바로 1회 실행:    sudo systemctl start coin-ensemble.service
  7) 실행 로그 보기:        journalctl -u coin-ensemble.service -n 100
  8) 피드백 자료 묶기:      bash ${APP_DIR}/tools/make_feedback_bundle.sh
  9) 코드 업데이트:         cd ${APP_DIR} && git pull
DONE

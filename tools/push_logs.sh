#!/usr/bin/env bash
# (선택) 매일 실행 후 bot_logs를 '비공개' GitHub 저장소에 자동 업로드.
# .env에 LOG_REPO_URL 이 있을 때만 동작:
#   LOG_REPO_URL=https://<GitHub토큰>@github.com/WonGND/autotrader-bot-logs.git
# 그러면 다음 대화에서 Claude가 그 저장소를 직접 읽고 피드백할 수 있습니다.
# ⚠️ 저장소는 반드시 Private. 자산·거래 내역이 들어 있습니다.
set -uo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_REPO_URL="$(grep -E '^LOG_REPO_URL=' "$APP_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
[ -z "$LOG_REPO_URL" ] && exit 0
DEST="$HOME/bot-logs-repo"
if [ ! -d "$DEST/.git" ]; then
  git clone -q "$LOG_REPO_URL" "$DEST" || { echo "로그 저장소 clone 실패"; exit 0; }
fi
cd "$DEST"
git pull -q --rebase || true
mkdir -p bot_logs && cp -r "$APP_DIR/bot_logs/." bot_logs/
cp "$APP_DIR/config/ensemble.yaml" .
git add -A
git -c user.name="coin-bot" -c user.email="coin-bot@localhost" commit -q -m "logs $(date +%F)" || exit 0
git push -q || echo "로그 push 실패 (토큰/권한 확인)"

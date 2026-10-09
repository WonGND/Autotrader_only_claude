#!/usr/bin/env bash
# Claude에게 피드백 받을 자료를 zip 하나로 묶는다.
#   bash tools/make_feedback_bundle.sh        → ~/feedback_YYYYMMDD.zip 생성
# 이 zip을 Claude 대화창에 첨부하고 "로그 분석해줘"라고 하면 됩니다.
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$HOME/feedback_$(date +%Y%m%d).zip"
TMP="$(mktemp -d)"
mkdir -p "$TMP/feedback"
cp -r "$APP_DIR/bot_logs" "$TMP/feedback/" 2>/dev/null || echo "bot_logs 없음 (아직 실행 전?)"
cp "$APP_DIR/config/ensemble.yaml" "$TMP/feedback/"
git -C "$APP_DIR" log --oneline -10 > "$TMP/feedback/git_version.txt" 2>/dev/null || true
journalctl -u coin-ensemble.service --since "-30 days" --no-pager > "$TMP/feedback/service_journal.txt" 2>/dev/null || true
systemctl list-timers coin-ensemble.timer --no-pager > "$TMP/feedback/timer_status.txt" 2>/dev/null || true
# API 키·토큰은 절대 포함하지 않음 (.env 미포함). 혹시 로그에 섞였으면 가림.
{ grep -rlE "BYBIT_API|TELEGRAM_TOKEN" "$TMP/feedback" 2>/dev/null || true; } | xargs -r sed -i -E 's/(BYBIT_API_[A-Z]+|TELEGRAM_TOKEN)=[^ ]+/\1=***/g'
(cd "$TMP" && zip -qr "$OUT" feedback)
rm -rf "$TMP"
echo "생성: $OUT"
echo "PC로 내려받기 (PC의 PowerShell에서):  scp -i <키파일> ubuntu@<서버IP>:$OUT ."

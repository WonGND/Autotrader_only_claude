# -*- coding: utf-8 -*-
"""
자동매매 워치독 (감시 프로세스)

run_live_trading.py 를 자식 프로세스로 실행하고 감시합니다.
  - 로그 파일이 STALE_THRESHOLD_SEC 동안 갱신되지 않으면 멈춘 것으로 판단
  - 자식 프로세스가 예기치 않게 종료되면 즉시 감지
  두 경우 모두 강제 종료 후 재시작하고 텔레그램으로 알립니다.

실행 방법:
    python -X utf8 -u watchdog.py
"""

import os
import sys
import time
import subprocess
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from utils.telegram_notifier import TelegramNotifier
from utils.logger import get_logger

logger = get_logger(__name__)

PROJECT_DIR  = os.path.dirname(os.path.abspath(__file__))
LOG_FILE     = os.path.join(PROJECT_DIR, "logs", "live_trading.log")
SCRIPT       = os.path.join(PROJECT_DIR, "run_live_trading.py")

CHECK_INTERVAL_SEC   = 60          # 감시 주기
STALE_THRESHOLD_SEC  = 40 * 60     # 사이클 주기(30분) + 여유 10분 = 40분 무응답 시 재시작
RESTART_COOLDOWN_SEC = 30          # 재시작 사이 최소 대기


def launch() -> subprocess.Popen:
    """run_live_trading.py 를 자식 프로세스로 실행하고 출력을 로그 파일에 연결"""
    log_f = open(LOG_FILE, "a", encoding="utf-8")
    env = {**os.environ, "TRADING_AUTO_CONFIRM": "true"}
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-u", SCRIPT],
        cwd=PROJECT_DIR,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        env=env,
    )
    logger.info(f"자동매매 프로세스 시작 (PID={proc.pid})")
    return proc


def kill(proc: subprocess.Popen):
    try:
        proc.kill()
        proc.wait(timeout=20)
    except Exception as e:
        logger.warning(f"프로세스 종료 중 오류: {e}")


def log_age_sec() -> float:
    if not os.path.exists(LOG_FILE):
        return 0.0
    return time.time() - os.path.getmtime(LOG_FILE)


def main():
    telegram = TelegramNotifier()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    telegram.send(f"🐶 <b>워치독 시작</b>\n시각: {now}\n자동매매 프로세스 감시를 시작합니다 (무응답 {STALE_THRESHOLD_SEC//60}분 시 자동 재시작)")
    logger.info("워치독 시작")

    proc = launch()
    last_restart = time.time()

    while True:
        time.sleep(CHECK_INTERVAL_SEC)

        # 1) 자식 프로세스가 예기치 않게 종료된 경우
        ret = proc.poll()
        if ret is not None:
            now = datetime.now().strftime("%H:%M")
            logger.warning(f"자동매매 프로세스 종료 감지 (exit={ret}) → 재시작")
            telegram.send(
                f"⚠️ <b>자동매매 프로세스 종료 감지</b>\n"
                f"시각: {now}  종료코드: {ret}\n"
                f"즉시 재시작합니다."
            )
            proc = launch()
            last_restart = time.time()
            continue

        # 2) 로그가 STALE_THRESHOLD_SEC 동안 갱신되지 않은 경우 (행 멈춤)
        age = log_age_sec()
        if age > STALE_THRESHOLD_SEC and (time.time() - last_restart) > RESTART_COOLDOWN_SEC:
            now = datetime.now().strftime("%H:%M")
            logger.warning(f"로그 무응답 {age/60:.0f}분 감지 → 프로세스 강제 재시작")
            telegram.send(
                f"🔴 <b>자동매매 멈춤 감지</b>\n"
                f"시각: {now}\n"
                f"로그 무응답: {age/60:.0f}분\n"
                f"프로세스를 강제 종료 후 재시작합니다."
            )
            kill(proc)
            proc = launch()
            last_restart = time.time()
            telegram.send(f"✅ <b>재시작 완료</b>\n시각: {datetime.now().strftime('%H:%M')}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[중단] 워치독 종료")

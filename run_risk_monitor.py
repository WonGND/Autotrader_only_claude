# -*- coding: utf-8 -*-
"""
코인 선물 급변 감시 (알림 전용 — 주문하지 않음)

앙상블 봇은 4시간봉 마감마다 판단한다. 그 사이 뉴스 등으로 시장이 급변하면
거래소에 걸린 비상 손절이 손실을 막지만, 그 사실을 다음 실행(최대 4시간 뒤)에야 알게 된다.
이 스크립트는 10분마다 가볍게 돌면서 아래 상황을 '바로' 텔레그램으로 알린다.

  1) 급변: 보유 종목·BTC가 15분/1시간 사이 기준 이상 움직임 (보유 방향 기준 유리/불리 표시)
  2) 손절 임박: 현재가가 거래소 비상 손절가에 기준(%) 이내로 접근
  3) 손절 없음: 보유 포지션에 비상 손절이 걸려 있지 않음 (위험)
  4) 청산 체결: 직전 확인 이후 거래소에서 새로 청산된 포지션 (비상 손절 체결 포함)

중복 알림 방지: 같은 종목·같은 종류는 cooldown_min 동안 한 번만 보낸다 (bot_logs/monitor_state.json).
실행:  python -X utf8 run_risk_monitor.py
"""
import os
import sys
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from utils.logger import get_logger

logger = get_logger("risk_monitor")
LOG_DIR = ROOT / "bot_logs"
STATE_PATH = LOG_DIR / "monitor_state.json"

DEFAULTS = dict(move_15m_pct=4.0, move_1h_pct=7.0, btc_15m_pct=2.5, btc_1h_pct=4.0,
                near_stop_pct=1.5, cooldown_min=60)


# ── 순수 계산 (거래소와 무관 — 테스트 가능) ──────────────────────────
def pct_moves(closes_5m: list, price: float) -> dict:
    """완성된 5분봉 종가 목록(오래된→최신)과 현재가로 15분·1시간 변화율(%)."""
    out = {}
    if price and len(closes_5m) >= 3 and closes_5m[-3] > 0:
        out["15m"] = (price / closes_5m[-3] - 1) * 100
    if price and len(closes_5m) >= 12 and closes_5m[-12] > 0:
        out["1h"] = (price / closes_5m[-12] - 1) * 100
    return out


def find_alerts(symbol: str, moves: dict, cfg: dict, side: str = "",
                price: float = 0.0, stop: float = 0.0, upnl: float = 0.0):
    """한 종목의 알림 목록 [(key, 문구)]. side: 'Buy'(롱) / 'Sell'(숏) / ''(미보유)."""
    alerts = []
    is_btc = symbol == "BTC"
    th = {"15m": cfg["btc_15m_pct"] if is_btc and not side else cfg["move_15m_pct"],
          "1h": cfg["btc_1h_pct"] if is_btc and not side else cfg["move_1h_pct"]}
    for win, chg in moves.items():
        if abs(chg) < th[win]:
            continue
        direction = "up" if chg > 0 else "down"
        if side:
            good = (chg > 0) == (side == "Buy")
            tag = "🟢 유리" if good else "🔴 불리"
            pos = f"{'롱' if side == 'Buy' else '숏'} 보유 · 미실현 {upnl:+.2f}U"
            alerts.append((f"{symbol}:move{win}:{direction}",
                           f"⚡ {symbol} {win} {chg:+.1f}% ({tag}) — {pos}"))
        else:
            alerts.append((f"{symbol}:move{win}:{direction}",
                           f"⚡ 시장 급변: {symbol} {win} {chg:+.1f}% (보유 없음)"))
    if side:
        if not stop:
            alerts.append((f"{symbol}:nostop",
                           f"⚠️ {symbol} {'롱' if side == 'Buy' else '숏'} 포지션에 비상 손절이 없습니다"))
        elif price:
            gap = (price / stop - 1) * 100 if side == "Buy" else (stop / price - 1) * 100
            if gap <= cfg["near_stop_pct"]:
                alerts.append((f"{symbol}:nearstop",
                               f"🛑 {symbol} 비상 손절 임박: 현재 {price:g} / 손절 {stop:g} (남은 거리 {gap:.1f}%)"))
    return alerts


def filter_cooldown(alerts: list, state: dict, now_ts: float, cooldown_min: float):
    """쿨다운 안에 이미 보낸 알림은 빼고, 보낼 것만 돌려준다 (state['sent'] 갱신)."""
    sent = state.setdefault("sent", {})
    out = []
    for key, text in alerts:
        if key not in sent or now_ts - sent[key] >= cooldown_min * 60:
            sent[key] = now_ts
            out.append(text)
    # 오래된 기록 정리 (하루 지난 것)
    for k in [k for k, t in sent.items() if now_ts - t > 86400]:
        del sent[k]
    return out


def new_closes(closed: list, state: dict):
    """직전 확인 이후 새 청산 내역. 첫 실행은 기준점만 잡고 알리지 않는다."""
    times = [int(c.get("updatedTime") or 0) for c in closed]
    last = state.get("last_closed_ms")
    newest = max(times) if times else (last or 0)
    if last is None:
        state["last_closed_ms"] = newest
        return []
    fresh = [c for c in closed if int(c.get("updatedTime") or 0) > last]
    state["last_closed_ms"] = max(newest, last)
    return sorted(fresh, key=lambda c: int(c.get("updatedTime") or 0))


def format_close(c: dict) -> str:
    pos_side = "롱" if c.get("side") == "Sell" else "숏"   # 청산 주문이 Sell이면 롱을 닫은 것
    pnl = float(c.get("closedPnl") or 0)
    return (f"{'💰' if pnl >= 0 else '🔻'} 청산 체결: {str(c.get('symbol', '')).replace('USDT', '')} {pos_side} "
            f"{c.get('qty')} · 진입 {c.get('avgEntryPrice')} → 청산 {c.get('avgExitPrice')} · {pnl:+.2f}U")


# ── 상태 파일 ────────────────────────────────────────────────────────
def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_PATH)


# ── 한 번 감시 ────────────────────────────────────────────────────────
def run_once(broker, cfg: dict, state: dict, now_ts: float = None):
    """보낼 메시지 목록을 돌려준다 (전송은 호출한 쪽에서)."""
    now_ts = now_ts or time.time()
    alerts, msgs = [], []

    positions = broker.get_positions()
    held = {}
    for p in positions:
        held[p.symbol.replace("USDT", "")] = p
    watch = sorted(set(held) | {"BTC"})

    for s in watch:
        try:
            k = broker.get_klines(f"{s}-USD", "5", 13)
            closes = [float(x) for x in k["Close"].tolist()]
            price = float(broker.get_price(f"{s}-USD"))
        except Exception as e:
            logger.warning(f"{s} 시세 조회 실패: {e}")
            continue
        p = held.get(s)
        alerts += find_alerts(s, pct_moves(closes, price), cfg,
                              side=p.side if p else "", price=price,
                              stop=float(p.stop_loss or 0) if p else 0.0,
                              upnl=float(p.unrealized_pnl or 0) if p else 0.0)

    msgs += filter_cooldown(alerts, state, now_ts, cfg["cooldown_min"])

    try:
        fresh = new_closes(broker.get_closed_pnl(limit=50), state)
        msgs += [format_close(c) for c in fresh]
    except Exception as e:
        logger.warning(f"청산 내역 조회 실패: {e}")
    return msgs


def main():
    cfg_all = yaml.safe_load(open(ROOT / "config" / "ensemble.yaml", encoding="utf-8"))
    cfg = {**DEFAULTS, **(cfg_all.get("monitor") or {})}
    state = load_state()
    telegram = None
    try:
        from utils.telegram_notifier import TelegramNotifier
        telegram = TelegramNotifier()
    except Exception:
        pass
    try:
        from broker.bybit_futures_broker import BybitFuturesBroker
        msgs = run_once(BybitFuturesBroker(), cfg, state)
        state["last_ok_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        state.pop("fail_count", None)
    except Exception as e:
        # 일시적인 네트워크 오류는 흔하므로, 3회 연속 실패할 때만 알린다
        state["fail_count"] = state.get("fail_count", 0) + 1
        logger.error(f"감시 실패({state['fail_count']}회 연속): {e}")
        msgs = [f"❗ 급변 감시 3회 연속 실패: {str(e)[:200]}"] if state["fail_count"] == 3 else []
    save_state(state)
    if msgs:
        text = "🚨 <b>코인 급변 감시</b>\n" + "\n".join(msgs)
        print(text)
        if telegram and getattr(telegram, "_enabled", False):
            telegram.send_now(text)
    else:
        logger.info("이상 없음")


if __name__ == "__main__":
    main()

"""급변 감시(run_risk_monitor.py) 오프라인 테스트 — 거래소 호출 없음."""
from types import SimpleNamespace

import pandas as pd

import run_risk_monitor as M

CFG = dict(M.DEFAULTS)


class Broker:
    def __init__(self, positions=(), closes=None, prices=None, closed=()):
        self.positions, self.closed = list(positions), list(closed)
        self.closes, self.prices = closes or {}, prices or {}

    def get_positions(self): return list(self.positions)
    def get_klines(self, symbol, interval, total):
        s = symbol.replace("-USD", "")
        return pd.DataFrame({"Close": self.closes.get(s, [100.0] * 13)})
    def get_price(self, symbol): return self.prices.get(symbol.replace("-USD", ""), 100.0)
    def get_closed_pnl(self, limit=50): return list(self.closed)


def pos(sym, side, stop=0.0, upnl=0.0):
    return SimpleNamespace(symbol=f"{sym}USDT", side=side, stop_loss=stop, unrealized_pnl=upnl)


def test_pct_moves_windows():
    closes = [100.0] * 10 + [104.0, 104.0, 104.0]
    m = M.pct_moves(closes, 104.0)
    assert round(m["15m"], 2) == 0.0 and round(m["1h"], 2) == 4.0


def test_adverse_move_alert_for_long_and_quiet_market():
    b = Broker(positions=[pos("SOL", "Buy", stop=80.0, upnl=-1.2)],
               closes={"SOL": [100.0] * 13}, prices={"SOL": 95.0})
    msgs = M.run_once(b, CFG, {}, now_ts=1000)
    assert any("SOL 15m -5.0%" in m and "불리" in m for m in msgs)
    assert not any("BTC" in m for m in msgs)          # BTC는 조용함


def test_btc_shock_without_positions_and_cooldown():
    b = Broker(closes={"BTC": [100.0] * 13}, prices={"BTC": 97.0})
    st = {}
    assert any("시장 급변: BTC 15m -3.0%" in m for m in M.run_once(b, CFG, st, now_ts=1000))
    assert M.run_once(b, CFG, st, now_ts=1000 + 600) == []          # 10분 뒤: 쿨다운
    assert M.run_once(b, CFG, st, now_ts=1000 + 3700) != []         # 1시간 뒤: 다시 알림


def test_missing_and_near_stop():
    b = Broker(positions=[pos("ETH", "Sell", stop=0.0), pos("XRP", "Buy", stop=99.0)])
    msgs = M.run_once(b, CFG, {}, now_ts=1000)
    assert any("ETH 숏 포지션에 비상 손절이 없습니다" in m for m in msgs)
    assert any("XRP 비상 손절 임박" in m for m in msgs)


def test_new_closes_only_after_first_sync():
    c1 = {"symbol": "BTCUSDT", "side": "Sell", "qty": "0.001", "avgEntryPrice": "1",
          "avgExitPrice": "2", "closedPnl": "0.5", "updatedTime": "10"}
    b = Broker(closed=[c1])
    st = {}
    assert M.run_once(b, CFG, st, now_ts=1000) == []                 # 첫 실행: 기준점만
    b.closed.append({**c1, "symbol": "DOGEUSDT", "side": "Buy", "closedPnl": "-0.3", "updatedTime": "20"})
    msgs = M.run_once(b, CFG, st, now_ts=2000)
    assert msgs == ["🔻 청산 체결: DOGE 숏 0.001 · 진입 1 → 청산 2 · -0.30U"]
    assert M.run_once(b, CFG, st, now_ts=3000) == []                 # 같은 청산은 한 번만

"""규칙 D 실매매 로직 검증: python -m pytest tests -q"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import yaml, pandas as pd
from backtester import rule_sim as R
import run_ensemble_trading as E

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = yaml.safe_load(open(os.path.join(ROOT, "config", "ensemble.yaml"), encoding="utf-8"))
D = R.load()
INS = {s: {"qty_step": 0.001, "min_qty": 0.001, "min_notional": 5.0} for s in D}
PX = {s: float(D[s].Close.iloc[-1]) for s in D}


def test_live_weights_equal_backtest_weights():
    """실매매 목표비중 == 백테스트 비중 (같은 함수, 같은 결과)."""
    W_live = E.compute_targets(D, CFG)
    k = CFG["exposure_multiplier"]
    W_bt = R.ensemble_weights(D, [10, 20, 30, 60, 90, 150, 250], 0.25 * k, 2.0 * k, 0.43, True)
    pd.testing.assert_frame_equal(W_live, W_bt)
    assert (W_live.abs().sum(axis=1) <= 2.0 * k + 1e-9).all()  # 총 레버리지 한도


def test_no_lookahead():
    """마지막 날 데이터를 바꿔도 그 전날까지의 비중은 변하지 않아야 함."""
    D2 = {s: df.copy() for s, df in D.items()}
    D2["BTC"].iloc[-1, D2["BTC"].columns.get_loc("Close")] *= 1.5
    a, b = E.compute_targets(D, CFG), E.compute_targets(D2, CFG)
    pd.testing.assert_frame_equal(a.iloc[:-1], b.iloc[:-1])


def test_plan_open_close_flip_band():
    t = {"BTC": 0.10, "ETH": 0.0, "SOL": -0.05, "XRP": 0.10}
    cur = {"ETH": 50.0, "SOL": 30.0, "XRP": 95.0}
    plans = {p["symbol"]: p for p in E.plan_orders(t, cur, PX, 1000, INS, CFG, halted=False)}
    assert plans["BTC"]["action"] == "open" and plans["BTC"]["side"] == "Buy"
    assert plans["ETH"]["action"] == "close"
    assert plans["XRP"]["action"] == "hold"                     # 95 → 100: 밴드 이내
    sol = [p for p in E.plan_orders(t, cur, PX, 1000, INS, CFG, False) if p["symbol"] == "SOL"]
    assert [p["action"] for p in sol] == ["close", "open"] and sol[1]["side"] == "Sell"


def test_min_order_skip_and_halt():
    ins = dict(INS, BTC={"qty_step": 0.001, "min_qty": 0.001, "min_notional": 5.0})
    p = E.plan_orders({"BTC": 0.036}, {}, {"BTC": 83000.0}, 140, ins, CFG, False)[0]
    assert p["action"] == "skip_min_order"                      # 5 USDT 필요 vs 83 USDT 최소
    p = E.plan_orders({"XRP": 0.1}, {}, PX, 1000, INS, CFG, halted=True)[0]
    assert p["action"] == "skip_halt"
    p = E.plan_orders({"XRP": 0.0}, {"XRP": 50.0}, PX, 1000, INS, CFG, halted=True)[0]
    assert p["action"] == "close"                               # 중단 중에도 청산은 허용


def test_exchange_leverage_keeps_liquidation_beyond_stop():
    """어떤 종목이든 강제청산 거리(≈1/레버리지)가 비상 손절 거리보다 멀어야 함."""
    for s, df in D.items():
        lev = E.exchange_leverage_for(df, CFG)
        a = float(R.atr(df, 14).iloc[-1]); px = float(df.Close.iloc[-1])
        stop_dist = CFG["disaster_stop_atr"] * a / px
        assert 1 <= lev <= CFG["exchange_leverage_max"]
        assert stop_dist < (1.0 / lev) * 0.95 or lev == 1, (s, lev, stop_dist)


def test_auto_multiplier_bounds_and_brake():
    p = dict(vol_target=0.30, dd_limit=0.20, k_min=1.0, k_max=3.0)
    assert R.auto_multiplier(0.05, 0.0, p)[0] == 3.0          # 잔잔한 시장 + 낙폭 없음 → 상한
    assert R.auto_multiplier(0.30, 0.0, p)[0] == 1.0          # 거친 시장 → 하한
    assert R.auto_multiplier(0.05, 0.10, p)[0] == 1.5         # 낙폭 10% → 3×(1−0.5)
    assert R.auto_multiplier(0.05, 0.25, p)[0] == 1.0         # 낙폭 한도 초과 → 하한
    assert R.auto_multiplier(float("nan"), 0.0, p)[0] == 3.0  # 데이터 부족 시에도 범위 안


def test_decide_multiplier_uses_config_mode():
    W1 = E.compute_targets(D, dict(CFG, exposure_multiplier=1))
    k, info = E.decide_multiplier(D, W1, dict(CFG, leverage_mode="fixed"), 100, 100)
    assert k == CFG["exposure_multiplier"] and info["mode"] == "fixed"
    k, info = E.decide_multiplier(D, W1, dict(CFG, leverage_mode="auto"), 85, 100)
    a = CFG["auto_leverage"]
    assert a["k_min"] <= k <= a["k_max"] and info["drawdown"] == 0.15

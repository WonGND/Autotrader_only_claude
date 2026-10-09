"""
단기 매매(4시간봉·1시간봉) + 최대 10배 레버리지 검토 — 140 USDT 계좌

비교: 일봉 / 4시간봉 / 1시간봉 × 룩백(보유기간) × 레버리지(고정 3·5·10배, 자동 최대 3·10배)
현실화: 봉 마감 신호 → 다음 봉 시가 체결, 수수료 0.055% + 슬리피지 0.05% (편도), 봉당 펀딩비,
       실매매 봇과 같은 주문 로직(20% 밴드·최소주문), 봉 단위 최악가 강제청산 점검.
BTC 추세 필터(숏 허용 조건)는 단기봉에서도 '일봉 200일선, 전날 확정값'을 사용.
비교 구간: W1 2025-01-01~ (일봉 vs 4h), W2 2026-05-15~ (일봉 vs 4h vs 1h, 1h 데이터가 짧아서)
실행: python research/coin_rules_sim/timeframe_study.py
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import numpy as np, pandas as pd, yaml
import backtester.rule_sim as R
from run_ensemble_trading import plan_orders
from tests.fake_broker import LOTS

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MAINT = 0.005


def daily_bull_for(index, D1):
    """일봉 BTC 200일선 강세 여부(전날 확정)를 임의 인덱스에 매핑."""
    btc = D1["BTC"].Close
    bull_d = (btc > btc.rolling(200).mean())
    bull_d.index = bull_d.index + pd.Timedelta(days=1)       # 그날 마감 후에야 알 수 있음
    return bull_d.reindex(index, method="ffill").fillna(False)


def run_account(D, W1, start, end, capital, cfg, mode, bpd):
    """mode: ("fixed", k) 또는 ("auto", params)"""
    syms = list(D); idx = D[syms[0]].index
    ins = {s: {"qty_step": LOTS[s], "min_qty": LOTS[s], "min_notional": 5.0} for s in syms}
    vol1 = R.model_vol(D, W1, bpd=bpd) if mode[0] == "auto" else None
    qty = {s: 0.0 for s in syms}; cash = capital
    curve, ks, peak, liq, n_ord, fees = [], [], capital, None, 0, 0.0
    O = {s: D[s].Open.values for s in syms}; H = {s: D[s].High.values for s in syms}
    L = {s: D[s].Low.values for s in syms}; C = {s: D[s].Close.values for s in syms}
    Wv = W1.values; cols = list(W1.columns)
    for t in range(1, len(idx)):
        d = idx[t]
        if d < start: continue
        if d > end: break
        pc = {s: C[s][t - 1] for s in syms}
        eq_prev = cash + sum(qty[s] * pc[s] for s in syms)
        peak = max(peak, eq_prev)
        if mode[0] == "fixed":
            k = mode[1]
        else:
            k, _ = R.auto_multiplier(vol1.iloc[t - 1], 1 - eq_prev / peak, mode[1])
        ks.append(k)
        tgt = {cols[j]: Wv[t - 1, j] * k for j in range(len(cols))}
        cur = {s: qty[s] * pc[s] for s in syms if qty[s] != 0}
        for p in plan_orders(tgt, cur, pc, eq_prev, ins, cfg, halted=False):
            if p["action"] not in ("open", "reduce", "close"): continue
            s = p["symbol"]
            if p["action"] == "close":
                dq = -qty[s]
            else:
                q = np.floor(p["qty"] / LOTS[s] + 1e-9) * LOTS[s]
                if q <= 0: continue
                dq = q if p["side"] == "Buy" else -q
            px = O[s][t] * (1 + R.SLIP if dq > 0 else 1 - R.SLIP)
            fee = abs(dq) * px * R.FEE
            cash -= dq * px + fee; fees += fee + abs(dq) * O[s][t] * R.SLIP; n_ord += 1
            qty[s] += dq
            if abs(qty[s]) < 1e-12: qty[s] = 0.0
        cash -= sum(qty[s] * O[s][t] for s in syms) * R.FUND_D / bpd
        worst = cash + sum(qty[s] * (L[s][t] if qty[s] > 0 else H[s][t]) for s in syms)
        gross = sum(abs(qty[s]) * C[s][t] for s in syms)
        if gross > 0 and worst <= MAINT * gross:
            liq = str(d); curve.append((d, 1e-9)); break
        curve.append((d, cash + sum(qty[s] * C[s][t] for s in syms)))
    eq = pd.Series(dict(curve))
    daily = eq.resample("1D").last().dropna()           # 지표는 일 단위로 통일
    m = R.metrics(daily.clip(lower=1e-9))
    days = max((eq.index[-1] - eq.index[0]).days, 1)
    return {"최종USDT": round(eq.iloc[-1], 1), "수익%": round((eq.iloc[-1] / capital - 1) * 100, 1),
            "연복리%": round(m["연복리%"], 1), "MDD%": round(m["MDD%"], 1), "샤프": round(m["샤프"], 2),
            "주문/일": round(n_ord / days, 2), "비용USDT": round(fees, 1),
            "평균배수": round(float(np.mean(ks)), 2), "청산": liq}, daily


if __name__ == "__main__":
    base = yaml.safe_load(open(os.path.join(ROOT, "config", "ensemble.yaml"), encoding="utf-8"))
    cfg = dict(base, exposure_multiplier=1)
    capital = float(os.environ.get("CAPITAL", 140))
    D1 = R.load()
    D4 = R.load(os.path.join(HERE, "data_4h"))
    DH = R.load(os.path.join(HERE, "data_1h"))
    for D in (D1, D4, DH):                     # 심볼 이름 통일 (BTCUSDT → BTC 는 load가 처리)
        assert "BTC" in D
    LB7 = [10, 20, 30, 60, 90, 150, 250]
    setups = {
        "일봉 표준 (2일~8개월 보유)": (D1, 1, LB7),
        "일봉 단기 (1~4주)": (D1, 1, [5, 10, 20, 30]),
        "4h 표준 (2~42일)": (D4, 6, LB7),
        "4h 단기 (1~10일)": (D4, 6, [6, 12, 24, 36, 60]),
        "1h 표준 (0.5~10일)": (DH, 24, LB7),
        "1h 단기 (6시간~3일)": (DH, 24, [6, 12, 24, 48, 72]),
    }
    auto3 = dict(vol_target=0.30, dd_limit=0.20, k_min=1.0, k_max=3.0)
    auto10 = dict(vol_target=0.60, dd_limit=0.30, k_min=1.0, k_max=10.0)
    modes = {"고정3배": ("fixed", 3), "고정5배": ("fixed", 5), "고정10배": ("fixed", 10),
             "자동1~3배": ("auto", auto3), "자동1~10배": ("auto", auto10)}
    end = D1["BTC"].index[-1] + pd.Timedelta(hours=23)
    windows = {"W1 2025-01~": pd.Timestamp("2025-01-01"), "W2 2026-05-15~": pd.Timestamp("2026-05-15")}
    res = {w: {} for w in windows}
    for name, (D, bpd, lbs) in setups.items():
        bull = daily_bull_for(D["BTC"].index, D1) if bpd > 1 else None
        W1 = R.ensemble_weights(D, lbs, base["vol_target"], base["lev_cap"], base["short_frac"],
                                base["short_bear_only"], bpd=bpd, bull=bull)
        for wname, st in windows.items():
            first_valid = D["BTC"].index[min(len(D["BTC"]) - 1, 90 * bpd + max(lbs) + 2)]
            if st < first_valid:
                continue                        # 이 봉 크기로는 그 구간 데이터가 부족
            for mname, mode in modes.items():
                r, _ = run_account(D, W1, st, end, capital, cfg, mode, bpd)
                res[wname][f"{name} | {mname}"] = r
                print(f"{wname} | {name} | {mname}: {r}")
    json.dump(res, open(os.path.join(HERE, "timeframe_results.json"), "w"), ensure_ascii=False, indent=1, default=str)
    pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 40)
    for w in windows:
        print(f"\n== {w}")
        print(pd.DataFrame(res[w]).T.to_string())

"""
자동 레버리지(노출 배수) 조절 규칙 검토 — 140 USDT 계좌, 최소주문·강제청산 포함

D의 목표 비중은 배수에 정확히 비례한다 (W_k = k × W_1). 그래서 매일 k_t만 정하면 된다.
후보 규칙 (모두 '그날 이전' 정보만 사용):
  V  : 포트폴리오 변동성 목표   k = 목표변동성 / 최근 60일 실현변동성(1배 모델)
  DD : 낙폭 브레이크            k = k_max × (1 − 계좌낙폭 / 낙폭한도), 최소 k_min
  R  : BTC 추세                 BTC > 200일선이면 k_hi, 아니면 k_lo
  조합: min(V, DD), min(V, DD) × (R이 약세면 0.75)
과최적화 방지: 파라미터는 2022-05~2023-12(학습)에서만 고르고 2024-01~(검증)에 그대로 적용.
실행: python research/coin_rules_sim/adaptive_study.py
"""
import os, sys, json, itertools
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import numpy as np, pandas as pd, yaml
import backtester.rule_sim as R
from run_ensemble_trading import plan_orders, compute_targets
from tests.fake_broker import LOTS

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MAINT = 0.005


def model_vol(D, W1, win=60):
    """1배 모델 포트폴리오의 최근 실현 연변동성 (t일 값은 t일 종가까지 정보)."""
    C = pd.DataFrame({s: D[s].Close for s in D})
    r = (W1.shift(1) * C.pct_change()).sum(axis=1)
    return r.rolling(win, min_periods=30).std() * np.sqrt(365)


def make_k_fn(rule, p, vol1, btc_bull):
    """rule별 배수 함수 k(t인덱스, 현재 계좌낙폭)."""
    def f(t, dd):
        ks = []
        if "V" in rule:
            v = vol1.iloc[t]
            ks.append(p["k_max"] if not np.isfinite(v) or v <= 0 else p["vol_target"] / v)
        if "DD" in rule:
            ks.append(p["k_max"] * (1 - dd / p["dd_limit"]))
        if rule == "R":
            ks.append(p["k_hi"] if btc_bull.iloc[t] else p["k_lo"])
        if rule.startswith("FIX"):
            ks.append(p["k"])
        k = min(ks) if ks else 1.0
        if "R" in rule and rule != "R" and not btc_bull.iloc[t]:
            k *= p.get("bear_mult", 0.75)
        return float(np.clip(k, p.get("k_min", 1.0), p["k_max"]))
    return f


def run_adaptive(D, W1, start, end, capital, cfg, k_fn):
    syms = list(D); idx = D[syms[0]].index
    ins = {s: {"qty_step": LOTS[s], "min_qty": LOTS[s], "min_notional": 5.0} for s in syms}
    qty = {s: 0.0 for s in syms}; cash = capital
    curve, ks, peak, liq = [], [], capital, None
    for t in range(1, len(idx)):
        d = idx[t]
        if d < start: continue
        if d > end: break
        o = {s: D[s].Open.iloc[t] for s in syms}
        pc = {s: D[s].Close.iloc[t - 1] for s in syms}
        eq_prev = cash + sum(qty[s] * pc[s] for s in syms)
        peak = max(peak, eq_prev)
        dd = 1 - eq_prev / peak if peak > 0 else 0
        k = k_fn(t - 1, dd); ks.append(k)
        tgt = (W1.iloc[t - 1] * k).to_dict()
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
            px = o[s] * (1 + R.SLIP if dq > 0 else 1 - R.SLIP)
            cash -= dq * px + abs(dq) * px * R.FEE
            qty[s] += dq
            if abs(qty[s]) < 1e-12: qty[s] = 0.0
        cash -= sum(qty[s] * o[s] for s in syms) * R.FUND_D
        worst = cash + sum(qty[s] * (D[s].Low.iloc[t] if qty[s] > 0 else D[s].High.iloc[t]) for s in syms)
        gross = sum(abs(qty[s]) * D[s].Close.iloc[t] for s in syms)
        if gross > 0 and worst <= MAINT * gross:
            liq = str(d.date()); curve.append((d, 1e-9)); break
        curve.append((d, cash + sum(qty[s] * D[s].Close.iloc[t] for s in syms)))
    eq = pd.Series(dict(curve))
    m = R.metrics(eq.clip(lower=1e-9))
    return {"최종USDT": round(eq.iloc[-1], 1), "연복리%": round(m["연복리%"], 1), "MDD%": round(m["MDD%"], 1),
            "칼마": round(m["칼마"], 2), "샤프": round(m["샤프"], 2),
            "평균배수": round(float(np.mean(ks)), 2), "배수범위": f"{min(ks):.1f}~{max(ks):.1f}",
            "청산": liq}, eq


if __name__ == "__main__":
    D = R.load()
    base = yaml.safe_load(open(os.path.join(ROOT, "config", "ensemble.yaml"), encoding="utf-8"))
    base1 = dict(base, exposure_multiplier=1)
    W1 = compute_targets(D, base1)
    vol1 = model_vol(D, W1)
    btc = D["BTC"].Close; btc_bull = btc > btc.rolling(200).mean()
    capital = float(os.environ.get("CAPITAL", 140))
    end = D["BTC"].index[-1]
    TRAIN = (pd.Timestamp("2022-05-01"), pd.Timestamp("2023-12-31"))
    TEST = (pd.Timestamp("2024-01-01"), end)
    FULL = (pd.Timestamp("2022-05-01"), end)
    LIVE = (pd.Timestamp("2026-06-19"), end)

    # 1) 학습 구간에서 파라미터 선택 (칼마 기준, 작은 격자)
    grids = {
        "V": [dict(vol_target=vt, k_max=km, k_min=1.0) for vt in (0.15, 0.20, 0.25, 0.30) for km in (3, 4)],
        "DD": [dict(dd_limit=dl, k_max=km, k_min=1.0) for dl in (0.2, 0.3, 0.4) for km in (3, 4)],
        "R": [dict(k_hi=hi, k_lo=lo, k_max=4, k_min=1.0) for hi, lo in ((3, 1.5), (4, 2), (3, 1), (4, 1.5))],
        "V+DD": [dict(vol_target=vt, dd_limit=dl, k_max=km, k_min=1.0)
                 for vt in (0.20, 0.25, 0.30) for dl in (0.2, 0.3) for km in (3, 4)],
        "V+DD+R": [dict(vol_target=vt, dd_limit=dl, k_max=km, k_min=1.0, bear_mult=0.75)
                   for vt in (0.20, 0.25, 0.30) for dl in (0.2, 0.3) for km in (3, 4)],
    }
    chosen, train_tbl = {}, {}
    for rule, grid in grids.items():
        best = None
        for p in grid:
            r, _ = run_adaptive(D, W1, *TRAIN, capital, base1, make_k_fn(rule, p, vol1, btc_bull))
            if best is None or r["칼마"] > best[0]["칼마"]:
                best = (r, p)
        chosen[rule] = best[1]; train_tbl[rule] = best[0]
        print(f"[학습] {rule:7s} 선택 {best[1]} → {best[0]}")

    # 2) 고정 배수 비교군 + 선택된 규칙을 검증/전체/실매매 구간에 적용
    rules = {"FIX2": dict(k=2, k_max=2), "FIX3": dict(k=3, k_max=3), "FIX4": dict(k=4, k_max=4)}
    rules.update(chosen)
    out = {"chosen_params": chosen, "train": {}, "test": {}, "full": {}, "live": {}}
    curves = {}
    for name, p in rules.items():
        rule = "FIX" if name.startswith("FIX") else name
        kf = make_k_fn(rule if rule != "FIX" else "FIX", p, vol1, btc_bull)
        for key, rng in (("train", TRAIN), ("test", TEST), ("full", FULL), ("live", LIVE)):
            r, eq = run_adaptive(D, W1, *rng, capital, base1, kf)
            out[key][name] = r
            if key == "full": curves[name] = eq
    json.dump(out, open(os.path.join(HERE, "adaptive_results.json"), "w"), ensure_ascii=False, indent=1, default=str)
    pd.DataFrame(curves).to_csv(os.path.join(HERE, "adaptive_curves.csv"))
    pd.set_option("display.width", 220)
    for key, title in (("train", "학습 2022-05~2023"), ("test", "검증 2024~ (파라미터 고정)"),
                       ("full", "전체 2022-05~"), ("live", "실매매 기간 06-19~")):
        print(f"\n== {title}")
        print(pd.DataFrame(out[key]).T.to_string())

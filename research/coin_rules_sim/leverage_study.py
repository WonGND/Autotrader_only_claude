"""
레버리지(노출 배수) 검토 — 소액 계좌(기본 140 USDT)에서 규칙 D·B2를 키우면 어떻게 되나

핵심 구분
  · 거래소 '레버리지 설정'은 같은 포지션에 묶이는 증거금만 바꾼다 (손익·최소주문은 그대로).
  · 수익/위험을 바꾸는 건 '노출 배수' = 자산 대비 포지션 명목 크기.
    D: vol_target·lev_cap을 k배, B2: 거래당 리스크를 k배.

현실화
  · 실매매 봇과 똑같은 주문 로직(run_ensemble_trading.plan_orders): 20% 밴드, 최소주문 미달 건너뜀/올림
  · 다음 날 시가 체결, 수수료+슬리피지, 펀딩비
  · 강제청산: 그날 모든 포지션이 동시에 최악가(롱=저가, 숏=고가)에 닿았다고 가정했을 때
    자산이 유지증거금(명목의 0.5%) 이하가 되면 '전액 청산'으로 처리 (보수적)
실행: python research/coin_rules_sim/leverage_study.py
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import numpy as np, pandas as pd, yaml
import backtester.rule_sim as R
from run_ensemble_trading import plan_orders, compute_targets
from tests.fake_broker import LOTS

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MAINT = 0.005


def run_d_account(D, W, start, end, capital, cfg, ideal=False):
    """실제 수량 단위로 계좌를 굴리는 D 시뮬레이션. ideal=True면 최소주문 제약 없음."""
    syms = list(D); idx = D[syms[0]].index
    ins = {s: ({"qty_step": 1e-9, "min_qty": 1e-9, "min_notional": 0.0} if ideal else
               {"qty_step": LOTS[s], "min_qty": LOTS[s], "min_notional": 5.0}) for s in syms}
    qty = {s: 0.0 for s in syms}
    cash = capital
    curve, n_skip, n_ord, liq_day, max_gross = [], 0, 0, None, 0.0
    for t in range(1, len(idx)):
        d = idx[t]
        if d < start: continue
        if d > end: break
        o = {s: D[s].Open.iloc[t] for s in syms}
        prev_c = {s: D[s].Close.iloc[t - 1] for s in syms}
        # 어제 종가 기준 자산·목표 → 오늘 시가에 체결
        eq_prev = cash + sum(qty[s] * prev_c[s] for s in syms)
        cur = {s: qty[s] * prev_c[s] for s in syms if qty[s] != 0}
        plans = plan_orders(W.iloc[t - 1].to_dict(), cur, prev_c, eq_prev, ins, cfg, halted=False)
        for p in plans:
            if p["action"] == "skip_min_order": n_skip += 1
            if p["action"] not in ("open", "reduce", "close"): continue
            s = p["symbol"]; n_ord += 1
            if p["action"] == "close":
                dq = -qty[s]
            else:
                step = ins[s]["qty_step"]
                q = np.floor(p["qty"] / step + 1e-9) * step if not ideal else p["qty"]
                if q <= 0: continue
                dq = q if p["side"] == "Buy" else -q
            px = o[s] * (1 + R.SLIP if dq > 0 else 1 - R.SLIP)
            cash -= dq * px + abs(dq) * px * R.FEE      # 선물: 현금흐름 표기(명목) 방식
            qty[s] += dq
            if abs(qty[s]) < 1e-12: qty[s] = 0.0
        # 펀딩 (롱 지불 / 숏 수취)
        cash -= sum(qty[s] * o[s] for s in syms) * R.FUND_D
        # 장중 최악 시나리오 강제청산 점검
        worst = cash + sum(qty[s] * (D[s].Low.iloc[t] if qty[s] > 0 else D[s].High.iloc[t]) for s in syms)
        gross = sum(abs(qty[s]) * D[s].Close.iloc[t] for s in syms)
        if gross > 0 and worst <= MAINT * gross:
            liq_day = d; curve.append((d, 0.0)); break
        eq = cash + sum(qty[s] * D[s].Close.iloc[t] for s in syms)
        if eq > 0: max_gross = max(max_gross, gross / eq)
        curve.append((d, eq))
    eq = pd.Series(dict(curve))
    return eq, dict(skipped=n_skip, orders=n_ord, liquidated=str(liq_day.date()) if liq_day else None,
                    max_gross_x=round(max_gross, 2))


def summarize(eq, capital, extra=None):
    if len(eq) < 2:
        return {}
    m = R.metrics(eq.clip(lower=1e-9))
    r = eq.pct_change().dropna()
    out = {"최종USDT": round(eq.iloc[-1], 1), "총수익%": round(m["총수익%"], 1), "연복리%": round(m["연복리%"], 1),
           "MDD%": round(m["MDD%"], 1), "샤프": round(m["샤프"], 2), "최악의날%": round(r.min() * 100, 1)}
    if extra: out.update(extra)
    return out


if __name__ == "__main__":
    D = R.load()
    base = yaml.safe_load(open(os.path.join(ROOT, "config", "ensemble.yaml"), encoding="utf-8"))
    capital = float(os.environ.get("CAPITAL", 140))
    end = D["BTC"].index[-1]
    periods = {"전체 2022-05~": pd.Timestamp("2022-05-01"), "2024~": pd.Timestamp("2024-01-01"),
               "실매매 기간 06-19~": pd.Timestamp("2026-06-19")}
    K = [1, 2, 3, 4, 6, 8]
    res = {"capital": capital, "D": {}, "D_ideal": {}, "B2": {}}
    Wk = {}
    for k in K:
        cfg = dict(base, vol_target=base["vol_target"] * k, lev_cap=base["lev_cap"] * k)
        Wk[k] = compute_targets(D, cfg)
    for pname, st in periods.items():
        res["D"][pname], res["D_ideal"][pname], res["B2"][pname] = {}, {}, {}
        for k in K:
            cfg = dict(base, vol_target=base["vol_target"] * k, lev_cap=base["lev_cap"] * k)
            eq, info = run_d_account(D, Wk[k], st, end, capital, cfg)
            res["D"][pname][f"{k}x"] = summarize(eq, capital, info)
            eqi, infoi = run_d_account(D, Wk[k], st, end, capital, cfg, ideal=True)
            res["D_ideal"][pname][f"{k}x"] = summarize(eqi, capital, {"liquidated": infoi["liquidated"],
                                                                       "max_gross_x": infoi["max_gross_x"]})
        for risk in (0.01, 0.02, 0.03, 0.05, 0.08):
            cfgb = dict(R.RULE_B2, risk=risk, max_notional_x=1.5 * max(1, risk / 0.01) / 1.0,
                        capital=capital, lots=LOTS)
            eq, tr = R.run_discrete(D, cfgb, st, end)
            m = R.metrics(eq, tr) if len(tr) else R.metrics(eq)
            r = eq.pct_change().dropna()
            res["B2"][pname][f"리스크{int(risk*100)}%"] = {
                "최종USDT": round(eq.iloc[-1] * capital, 1), "총수익%": round(m["총수익%"], 1),
                "연복리%": round(m["연복리%"], 1), "MDD%": round(m["MDD%"], 1), "샤프": round(m["샤프"], 2),
                "최악의날%": round(r.min() * 100, 1), "거래수": int(m.get("거래수", 0))}
    # 켈리 기준 (전체 기간, 이상적 1x 일간 수익)
    eq1, _ = run_d_account(D, Wk[1], periods["전체 2022-05~"], end, capital,
                           dict(base), ideal=True)
    r1 = eq1.pct_change().dropna()
    mu, var = r1.mean() * 365, r1.var() * 365
    res["kelly"] = {"연수익_1x": round(mu * 100, 2), "연변동성_1x": round(np.sqrt(var) * 100, 2),
                    "풀켈리_배수": round(mu / var, 1), "하프켈리_배수": round(mu / var / 2, 1)}
    json.dump(res, open(os.path.join(HERE, "leverage_results.json"), "w"), ensure_ascii=False, indent=1, default=str)
    pd.set_option("display.width", 220)
    for key, title in (("D", f"D — {capital:.0f} USDT 계좌, 최소주문 제약 포함"),
                       ("D_ideal", "D — 최소주문 제약 없음(이상적)"),
                       ("B2", f"B2 — {capital:.0f} USDT, 거래당 리스크별")):
        for pname in periods:
            print(f"\n== {title} | {pname}")
            print(pd.DataFrame(res[key][pname]).T.to_string())
    print("\n== 켈리", res["kelly"])

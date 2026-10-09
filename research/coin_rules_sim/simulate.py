"""
코인 선물 규칙 비교 시뮬레이션 (연구용 실행 스크립트)
엔진은 backtester/rule_sim.py (라이브 봇과 공용). 실행: python research/coin_rules_sim/simulate.py
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import numpy as np, pandas as pd
import backtester.rule_sim as R
from backtester.rule_sim import *  # noqa

if __name__ == "__main__":
    D = load()
    start, end = pd.Timestamp("2022-05-01"), D["BTC"].index[-1]
    periods = {
        "전체 2022-05~현재": (start, end),
        "2022 약세장(5~12월)": (pd.Timestamp("2022-05-01"), pd.Timestamp("2022-12-31")),
        "2023": (pd.Timestamp("2023-01-01"), pd.Timestamp("2023-12-31")),
        "2024": (pd.Timestamp("2024-01-01"), pd.Timestamp("2024-12-31")),
        "2025": (pd.Timestamp("2025-01-01"), pd.Timestamp("2025-12-31")),
        "최근 2024~현재": (pd.Timestamp("2024-01-01"), end),
        "2026 YTD": (pd.Timestamp("2026-01-01"), end),
        "실매매 기간(06-19~)": (pd.Timestamp("2026-06-19"), end),
    }
    LB = [10, 20, 30, 60, 90, 150, 250]
    W_C = ensemble_weights(D, LB, 0.25, 2.0)
    W_D = ensemble_weights(D, LB, 0.25, 2.0, short_frac=0.43, short_bear_only=True)  # 롱:숏 ≈ 70:30
    W_E = ensemble_weights(D, LB, 0.25, 2.0, short_frac=1.0, short_bear_only=False)  # 대칭 롱숏
    C_all = pd.DataFrame({s: D[s].Close for s in D})
    results = {}
    for pname, (a, b) in periods.items():
        row = {}
        for cfg in (RULE_A, RULE_A1, RULE_A2, RULE_B, RULE_B2):
            eq, tr = run_discrete(D, cfg, a, b)
            row[cfg["name"]] = metrics(eq, tr)
        for nm, W in (("C. 앙상블 롱전용(논문)", W_C), ("D. 앙상블 롱70/숏30(약세장만 숏)", W_D),
                      ("E. 앙상블 대칭 롱숏", W_E)):
            eq, to = run_weights(D, W, a, b)
            row[nm] = metrics(eq)
        bh = C_all.loc[a:b]
        row["BTC 보유"] = metrics(bh.BTC / bh.BTC.iloc[0])
        row["10종목 동일비중 보유"] = metrics((bh / bh.iloc[0]).mean(axis=1))
        results[pname] = row
    # 비용 민감도 (전체 기간, C/D)
    sens = {}
    for slip in (0.0005, 0.001, 0.002):
        R.COST = FEE + slip
        sens[f"슬리피지 {slip*100:.2f}%"] = {
            "C": metrics(run_weights(D, W_C, start, end)[0]),
            "D": metrics(run_weights(D, W_D, start, end)[0]),
        }
    R.COST = FEE + SLIP
    # 파라미터 민감도 (전체, D): 룩백 세트 / 변동성 타깃
    psens = {}
    for nm, lbs in (("짧은 [5,10,20,30]", [5, 10, 20, 30]), ("중간 [20,30,60,90]", [20, 30, 60, 90]),
                    ("긴 [60,90,150,250]", [60, 90, 150, 250]), ("전체 7개", LB)):
        psens[nm] = metrics(run_weights(D, ensemble_weights(D, lbs, 0.25, 2.0, 0.43, True), start, end)[0])
    for vt in (0.15, 0.25, 0.40):
        psens[f"변동성타깃 {int(vt*100)}%"] = metrics(run_weights(D, ensemble_weights(D, LB, vt, 2.0, 0.43, True), start, end)[0])
    # 곡선 저장
    curves = {}
    for cfg in (RULE_A, RULE_A2, RULE_B, RULE_B2):
        curves[cfg["name"]] = run_discrete(D, cfg, start, end)[0]
    curves["C. 앙상블 롱전용(논문)"] = run_weights(D, W_C, start, end)[0]
    curves["D. 앙상블 롱70/숏30(약세장만 숏)"] = run_weights(D, W_D, start, end)[0]
    bh = C_all.loc[start:end]; curves["BTC 보유"] = bh.BTC / bh.BTC.iloc[0]
    HERE = os.path.dirname(os.path.abspath(__file__))
    pd.DataFrame(curves).to_csv(os.path.join(HERE, "curves.csv"))
    json.dump({"periods": results, "cost_sens": sens, "param_sens": psens,
               "data": {"start": str(D["BTC"].index[0].date()), "end": str(end.date()), "symbols": list(D)}},
              open(os.path.join(HERE, "results.json"), "w"), ensure_ascii=False, indent=1, default=float)
    pd.set_option("display.width", 250)
    for pname, row in results.items():
        print(f"\n== {pname}")
        print(pd.DataFrame(row).T.round(2).to_string())
    print("\n== 비용 민감도"); print(pd.DataFrame({k: {kk: round(vv['연복리%'], 1) for kk, vv in v.items()} for k, v in sens.items()}))
    print("\n== 파라미터 민감도 (D)"); print(pd.DataFrame(psens).T.round(2))

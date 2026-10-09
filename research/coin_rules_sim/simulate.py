"""
코인 선물 규칙 비교 시뮬레이션 (일봉, 10종목, 포트폴리오 단위)

공통 현실화 가정
  - 신호는 일봉 종가에서 판단, 체결은 '다음 날 시가' (look-ahead 제거)
  - 수수료 0.055%(테이커) + 슬리피지 0.05% (편도)
  - 펀딩비 0.01%/8h: 롱은 지불, 숏은 수취 (강세장 평균 가정)
  - 손절은 장중 고가/저가로 판정, 시가가 손절선을 넘어 갭이 나면 시가에 체결
  - 한 종목·한 방향 동시 1포지션 (규칙 A·B), 포트폴리오 공유 자본
"""
import glob, os, json
import numpy as np, pandas as pd

FEE, SLIP, FUND_D = 0.00055, 0.0005, 0.0001 * 3
COST = FEE + SLIP
DATA = os.path.join(os.path.dirname(__file__), "data")

def load():
    d = {}
    for f in sorted(glob.glob(f"{DATA}/*.csv")):
        s = os.path.basename(f)[:-4].replace("USDT", "")
        d[s] = pd.read_csv(f, index_col=0, parse_dates=True)
    idx = sorted(set.intersection(*[set(v.index) for v in d.values()]))
    return {k: v.loc[idx] for k, v in d.items()}

def atr(df, n=14):
    pc = df.Close.shift(1)
    tr = pd.concat([df.High - df.Low, (df.High - pc).abs(), (df.Low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(span=n, min_periods=n).mean()

def metrics(eq, trades=None):
    eq = eq.dropna()
    r = eq.pct_change().dropna()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    out = {
        "총수익%": (eq.iloc[-1] / eq.iloc[0] - 1) * 100,
        "연복리%": ((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1) * 100 if eq.iloc[-1] > 0 else -100,
        "MDD%": ((eq / eq.cummax()) - 1).min() * 100,
        "칼마": 0,
        "샤프": r.mean() / r.std() * np.sqrt(365) if r.std() > 0 else 0,
    }
    out["칼마"] = out["연복리%"] / abs(out["MDD%"]) if out["MDD%"] < 0 else 0
    if trades is not None and len(trades):
        p = np.array([t["pnl"] for t in trades])
        out["거래수"] = len(p)
        out["승률%"] = (p > 0).mean() * 100
        out["PF"] = p[p > 0].sum() / -p[p < 0].sum() if (p < 0).any() else np.inf
    return out

# ─────────────────────────────────────────────────────────────────────
# 이산 포지션 엔진 (규칙 A: 현재 봇 / 규칙 B: 터틀형)
# ─────────────────────────────────────────────────────────────────────
def run_discrete(D, cfg, start, end):
    syms = list(D)
    idx = D[syms[0]].index
    pre = {}
    for s in syms:
        df = D[s].copy()
        df["atr"] = atr(df, 14) if cfg["atr_n"] == 14 else atr(df, cfg["atr_n"])
        df["sma200"] = df.Close.rolling(200).mean()
        pre[s] = df
    btc200 = pre["BTC"].Close > pre["BTC"].sma200
    eq_cash = 1.0
    pos = {}      # (s, side) -> dict
    cooldown = {}
    trades, curve = [], []
    days = [d for d in idx if start <= d <= end]
    pending = []  # orders to fill at next open
    for di, d in enumerate(idx):
        if d < start:
            continue
        if d > end:
            break
        i = idx.get_loc(d)
        # 1) fill pending entries at today's open
        for (s, side, risk_pct, stop_mult) in pending:
            row = pre[s].iloc[i]
            if (s, side) in pos:
                continue
            a = pre[s].atr.iloc[i - 1]
            px = row.Open * (1 + SLIP if side == 1 else 1 - SLIP)
            stop = px - side * stop_mult * a
            dist = abs(px - stop) / px
            equity = eq_cash + sum(p["upnl"] for p in pos.values())
            notional = equity * risk_pct / dist
            notional = min(notional, equity * cfg["max_notional_x"])
            eq_cash -= notional * FEE
            tp = px + side * cfg["tp_mult"] * a if cfg["tp_mult"] else None
            pos[(s, side)] = dict(s=s, side=side, entry=px, stop=stop, tp=tp, notional=notional,
                                  qty=notional / px, upnl=0.0, i0=i, best=px)
        pending = []
        # 2) manage open positions intraday
        for key in list(pos):
            p = pos[key]; s, side = key; row = pre[s].iloc[i]
            exit_px = None
            if side == 1:
                if row.Open <= p["stop"]: exit_px = row.Open
                elif row.Low <= p["stop"]: exit_px = p["stop"]
                elif p["tp"] and row.High >= p["tp"]: exit_px = max(p["tp"], row.Open)
            else:
                if row.Open >= p["stop"]: exit_px = row.Open
                elif row.High >= p["stop"]: exit_px = p["stop"]
                elif p["tp"] and row.Low <= p["tp"]: exit_px = min(p["tp"], row.Open)
            # funding
            eq_cash -= side * p["notional"] * FUND_D
            if exit_px is not None:
                exit_px *= (1 - SLIP if side == 1 else 1 + SLIP)
                pnl = p["qty"] * (exit_px - p["entry"]) * side - p["qty"] * exit_px * FEE
                eq_cash += pnl
                trades.append(dict(s=s, side=side, pnl=pnl, d0=idx[p["i0"]], d1=d))
                cooldown[key] = i + cfg["cooldown_bars"]
                del pos[key]
            else:
                p["upnl"] = p["qty"] * (row.Close - p["entry"]) * side
        # 3) end-of-day: trailing exits (channel exit) → next open
        exits = []
        for key, p in pos.items():
            s, side = key; df = pre[s]
            if cfg.get("exit_channel"):
                n = cfg["exit_channel"]
                if side == 1 and df.Close.iloc[i] < df.Low.iloc[i - n:i].min(): exits.append(key)
                if side == -1 and df.Close.iloc[i] > df.High.iloc[i - n:i].max(): exits.append(key)
        # exit at next open handled by setting stop to +inf / -inf
        for key in exits:
            pos[key]["stop"] = np.inf if key[1] == 1 else -np.inf
        # 4) signals at close
        equity = eq_cash + sum(p["upnl"] for p in pos.values())
        open_risk = len(pos)
        for s in syms:
            df = pre[s]
            if i < 210: continue
            for side in (1, -1):
                if side == -1 and not cfg["allow_short"]: continue
                if side == 1 and not cfg["allow_long"]: continue
                if cfg.get("short_only_btc_bear") and side == -1 and btc200.iloc[i]: continue
                if cfg.get("long_only_btc_bull") and side == 1 and not btc200.iloc[i] and s != "BTC": continue
                key = (s, side)
                if key in pos or cooldown.get(key, -1) > i: continue
                n = cfg["ch"](s, side)
                c = df.Close.iloc[i]
                if side == 1:
                    lvl = df.High.iloc[i - n:i].max(); prev_lvl = df.High.iloc[i - n - 1:i - 1].max()
                    sig = c > lvl and (not cfg["event"] or df.Close.iloc[i - 1] <= prev_lvl)
                else:
                    lvl = df.Low.iloc[i - n:i].min(); prev_lvl = df.Low.iloc[i - n - 1:i - 1].min()
                    sig = c < lvl and (not cfg["event"] or df.Close.iloc[i - 1] >= prev_lvl)
                if not sig: continue
                same_dir = sum(1 for k in pos if k[1] == side) + sum(1 for o in pending if o[1] == side)
                if open_risk + len(pending) >= cfg["max_pos"]: continue
                if same_dir >= cfg["max_same_dir"]: continue
                pending.append((s, side, cfg["risk"], cfg["stop_mult"]))
        curve.append((d, equity))
    eq = pd.Series(dict(curve))
    return eq, trades

CUR_CH = {"BTC": (20, 10), "ETH": (20, 20), "BNB": (40, 20), "SOL": (40, 10), "XRP": (20, 10),
          "DOGE": (30, 20), "ADA": (30, 10), "AVAX": (20, 10), "LINK": (18, 5), "DOT": (20, 20)}

RULE_A = dict(name="A. 현재 봇 규칙", ch=lambda s, side: CUR_CH[s][0 if side == 1 else 1],
              event=False, stop_mult=1.0, tp_mult=8.0, risk=0.03, max_pos=3, max_same_dir=3,
              allow_long=True, allow_short=True, cooldown_bars=0, exit_channel=None,
              max_notional_x=1.5, atr_n=14)
RULE_A1 = dict(RULE_A, name="A1. 현재+이벤트신호+쿨다운", event=True, cooldown_bars=1)
RULE_A2 = dict(RULE_A1, name="A2. A1+2ATR손절+익절제거+10일채널청산+리스크1%", stop_mult=2.0, tp_mult=None,
               exit_channel=10, risk=0.01, max_pos=4, max_same_dir=3)
RULE_B = dict(name="B. 터틀 20/10", ch=lambda s, side: 20, event=True, stop_mult=2.0, tp_mult=None,
              risk=0.01, max_pos=4, max_same_dir=4, allow_long=True, allow_short=True,
              cooldown_bars=1, exit_channel=10, max_notional_x=1.5, atr_n=20)
RULE_B2 = dict(RULE_B, name="B2. 터틀 55/20 + 롱우위", ch=lambda s, side: 55, exit_channel=20,
               short_only_btc_bear=True, long_only_btc_bull=True, max_same_dir=3)

# ─────────────────────────────────────────────────────────────────────
# 앙상블 + 변동성 타깃 (규칙 C: Zarattini et al. 2025)
# ─────────────────────────────────────────────────────────────────────
def ensemble_weights(D, lookbacks, vol_target, lev_cap, short_frac=0.0, short_bear_only=True):
    syms = list(D); idx = D[syms[0]].index
    W = pd.DataFrame(0.0, index=idx, columns=syms)
    btc = D["BTC"].Close; bull = btc > btc.rolling(200).mean()
    for s in syms:
        c = D[s].Close.values; n = len(c)
        vol = pd.Series(c).pct_change().rolling(90).std().values * np.sqrt(365)
        expo = np.zeros(n)
        for side in ([1, -1] if short_frac > 0 else [1]):
            for L in lookbacks:
                inpos, stop = False, np.nan
                hi = pd.Series(c).rolling(L).max().shift(1).values
                lo = pd.Series(c).rolling(L).min().shift(1).values
                hiL = pd.Series(c).rolling(L).max().values
                loL = pd.Series(c).rolling(L).min().values
                for t in range(L + 1, n):
                    mid = (hiL[t] + loL[t]) / 2
                    if not inpos:
                        ok = (c[t] > hi[t]) if side == 1 else (c[t] < lo[t])
                        if side == -1 and short_bear_only and bull.iloc[t]: ok = False
                        if ok: inpos, stop = True, mid
                    else:
                        stop = max(stop, mid) if side == 1 else min(stop, mid)
                        if (side == 1 and c[t] < stop) or (side == -1 and c[t] > stop):
                            inpos = False
                    if inpos:
                        expo[t] += side * (1.0 if side == 1 else short_frac) / len(lookbacks)
        scale = np.clip(vol_target / np.where(vol > 0, vol, np.nan), 0, lev_cap)
        W[s] = np.nan_to_num(expo * scale)
    W = W / len(syms)
    gross = W.abs().sum(axis=1)
    W = W.div(np.maximum(gross / lev_cap, 1.0), axis=0)
    return W

def run_weights(D, W, start, end, band=0.2):
    syms = list(D); idx = D[syms[0]].index
    O = pd.DataFrame({s: D[s].Open for s in syms}); C = pd.DataFrame({s: D[s].Close for s in syms})
    held = pd.Series(0.0, index=syms); eq = 1.0; curve = []; turnover = 0
    for t in range(1, len(idx)):
        d = idx[t]
        if d < start: continue
        if d > end: break
        tgt = W.iloc[t - 1]
        # 리밸런스 밴드: 목표와 20% 이상 차이 날 때만 조정 (거래비용 절감)
        new = held.copy()
        for s in syms:
            if abs(tgt[s] - held[s]) > band * max(abs(held[s]), 1e-9) or (tgt[s] == 0) != (held[s] == 0):
                new[s] = tgt[s]
        gap = (O.iloc[t] / C.iloc[t - 1] - 1)
        intra = (C.iloc[t] / O.iloc[t] - 1)
        r = (held * gap).sum() + (new * intra).sum()
        tc = (new - held).abs().sum() * COST
        fund = (new.clip(lower=0).sum() - new.clip(upper=0).abs().sum()) * FUND_D
        turnover += (new - held).abs().sum()
        eq *= (1 + r - tc - fund)
        held = new; curve.append((d, eq))
    return pd.Series(dict(curve)), turnover

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
        globals()["COST"] = FEE + slip
        sens[f"슬리피지 {slip*100:.2f}%"] = {
            "C": metrics(run_weights(D, W_C, start, end)[0]),
            "D": metrics(run_weights(D, W_D, start, end)[0]),
        }
    COST = FEE + SLIP
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
    pd.DataFrame(curves).to_csv("curves.csv")
    json.dump({"periods": results, "cost_sens": sens, "param_sens": psens,
               "data": {"start": str(D["BTC"].index[0].date()), "end": str(end.date()), "symbols": list(D)}},
              open("results.json", "w"), ensure_ascii=False, indent=1, default=float)
    pd.set_option("display.width", 250)
    for pname, row in results.items():
        print(f"\n== {pname}")
        print(pd.DataFrame(row).T.round(2).to_string())
    print("\n== 비용 민감도"); print(pd.DataFrame({k: {kk: round(vv['연복리%'], 1) for kk, vv in v.items()} for k, v in sens.items()}))
    print("\n== 파라미터 민감도 (D)"); print(pd.DataFrame(psens).T.round(2))

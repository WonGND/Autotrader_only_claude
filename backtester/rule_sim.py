"""
규칙 시뮬레이션 엔진 (라이브 봇과 연구용 백테스트가 '같은 코드'를 쓰도록 공용화)

  - ensemble_weights : 규칙 D(앙상블 추세추종) 목표 비중 — run_ensemble_trading.py가 실매매에 사용
  - run_discrete + RULE_B2 : 규칙 B2(터틀 55/20+롱우위) — 매일 가상(섀도) 기준선 계산에 사용
  - run_weights : 비중 기반 포트폴리오 백테스트 (D 모델 곡선)

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
DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research", "coin_rules_sim", "data")

def load(data_dir=None):
    d = {}
    for f in sorted(glob.glob(f"{data_dir or DATA}/*.csv")):
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
            # 소액 계좌 최소 주문 제약 (cfg["capital"]=시작 자본 USDT, cfg["lots"]=수량 단위)
            if cfg.get("capital"):
                min_n = max(cfg["lots"][s] * px, 5.0) / cfg["capital"]   # 정규화(시작=1.0) 단위
                if notional < min_n:
                    if notional >= 0.6 * min_n and min_n <= 2.0 * notional:
                        notional = min_n
                    else:
                        continue
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
def ensemble_weights(D, lookbacks, vol_target, lev_cap, short_frac=0.0, short_bear_only=True,
                     bpd=1, bull=None):
    """규칙 D 목표 비중.

    bpd : 하루당 봉 수 (일봉 1, 4시간봉 6, 1시간봉 24). lookbacks는 '봉 개수' 단위.
          변동성은 최근 90일(=90·bpd봉)로 계산해 연율화(√(365·bpd)).
    bull: BTC 강세 여부(bool Series, D와 같은 인덱스). None이면 BTC 200일선(=200·bpd봉)으로 계산.
          단기봉에서는 일봉 200일선을 넘겨주는 것이 정확하다 (데이터가 짧아도 됨).
    """
    syms = list(D); idx = D[syms[0]].index
    W = pd.DataFrame(0.0, index=idx, columns=syms)
    if bull is None:
        btc = D["BTC"].Close; bull = btc > btc.rolling(200 * bpd).mean()
    bull_arr = np.asarray(pd.Series(bull).reindex(idx).fillna(False).values, dtype=bool) \
        if isinstance(bull, pd.Series) else np.asarray(bull, dtype=bool)
    for s in syms:
        c = D[s].Close.values; n = len(c)
        vol = pd.Series(c).pct_change().rolling(90 * bpd).std().values * np.sqrt(365 * bpd)
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
                        if side == -1 and short_bear_only and bull_arr[t]: ok = False
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

def run_weights(D, W, start, end, band=0.2, bpd=1):
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
        fund = (new.clip(lower=0).sum() - new.clip(upper=0).abs().sum()) * FUND_D / bpd
        turnover += (new - held).abs().sum()
        eq *= (1 + r - tc - fund)
        held = new; curve.append((d, eq))
    return pd.Series(dict(curve)), turnover


# ─────────────────────────────────────────────────────────────────────
# 자동 노출 배수 (2026-10-09 adaptive_study.py 검토 결과: 변동성 목표 + 낙폭 브레이크)
# D의 비중은 배수에 정비례(W_k = k·W_1)하므로 매일 k만 정하면 된다.
# ─────────────────────────────────────────────────────────────────────
def model_vol(D, W1, win=60, bpd=1):
    """1배 모델 포트폴리오의 최근 60일 실현 연변동성. t봉 값은 t봉 종가까지의 정보만 사용."""
    C = pd.DataFrame({s: D[s].Close for s in D})
    r = (W1.shift(1) * C.pct_change()).sum(axis=1)
    return r.rolling(win * bpd, min_periods=30 * bpd).std() * np.sqrt(365 * bpd)


def auto_multiplier(vol, drawdown, p):
    """k = min(목표변동성/실현변동성, k_max·(1−낙폭/낙폭한도)) 를 [k_min, k_max]로 제한.

    반환: (k, 구성요소 dict) — 로그에 근거를 남기기 위해 구성요소도 돌려준다.
    """
    k_vol = p["k_max"] if not np.isfinite(vol) or vol <= 0 else p["vol_target"] / vol
    k_dd = p["k_max"] * (1 - drawdown / p["dd_limit"])
    k = float(np.clip(min(k_vol, k_dd), p["k_min"], p["k_max"]))
    return k, {"k_vol": round(float(k_vol), 3), "k_dd": round(float(k_dd), 3),
               "model_vol": round(float(vol), 4) if np.isfinite(vol) else None,
               "drawdown": round(float(drawdown), 4)}


def run_weights_auto(D, W1, start, end, p, band=0.2, bpd=1):
    """자동 배수를 적용한 비중 백테스트 (모델 D 가상 곡선용). 낙폭은 자기 자산곡선 기준."""
    vol1 = model_vol(D, W1, bpd=bpd)
    syms = list(D); idx = D[syms[0]].index
    O = pd.DataFrame({s: D[s].Open for s in syms}); C = pd.DataFrame({s: D[s].Close for s in syms})
    held = pd.Series(0.0, index=syms); eq, peak = 1.0, 1.0; curve, ks = [], []
    for t in range(1, len(idx)):
        d = idx[t]
        if d < start: continue
        if d > end: break
        k, _ = auto_multiplier(vol1.iloc[t - 1], 1 - eq / peak, p)
        ks.append(k)
        tgt = W1.iloc[t - 1] * k
        new = held.copy()
        for s in syms:
            if abs(tgt[s] - held[s]) > band * max(abs(held[s]), 1e-9) or (tgt[s] == 0) != (held[s] == 0):
                new[s] = tgt[s]
        r = (held * (O.iloc[t] / C.iloc[t - 1] - 1)).sum() + (new * (C.iloc[t] / O.iloc[t] - 1)).sum()
        tc = (new - held).abs().sum() * COST
        fund = (new.clip(lower=0).sum() - new.clip(upper=0).abs().sum()) * FUND_D / bpd
        eq *= (1 + r - tc - fund); peak = max(peak, eq)
        held = new; curve.append((d, eq))
    return pd.Series(dict(curve)), ks

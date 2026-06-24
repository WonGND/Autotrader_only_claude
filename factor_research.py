# factor_research.py — 멀티팩터 종목선정 리서치 프레임워크
#
# 사용자 요청 5가지를 모두 구현:
#   1. 가격/거래량 패턴   → 모멘텀·변동성·거래량변화 팩터
#   2. 공개 팩터 활용     → 모멘텀(12-1)·단기반전·저변동성·거래량 등 고전 팩터
#   3. 기술지표 조합      → RSI·볼린저·MACD 여러 기간
#   4. 새 공식 생성       → 위험조정모멘텀, 거래량가중모멘텀 등 합성팩터
#   5. AI 학습           → 팩터행렬로 미래상승확률 학습(GBM) + IC 예측력 분석
#
# 핵심 산출물:
#   - compute_factors(df): 한 종목의 최신 팩터 dict (실시간 종목선정용)
#   - ic_analysis(): 팩터별 정보계수(IC) — 미래수익 예측력 검증
#   - train_factor_model(): 팩터→미래상승 ML 모델 + 팩터 중요도

import numpy as np
import pandas as pd


def _col(df, name):
    """OHLCV 컬럼 대소문자 무관 조회."""
    for c in (name, name.lower(), name.capitalize(), name.upper()):
        if c in df.columns:
            return df[c]
    return None


def _rsi(close, period):
    d = close.diff()
    g = d.clip(lower=0).rolling(period).mean()
    l = (-d.clip(upper=0)).rolling(period).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))


def factor_timeseries(df: pd.DataFrame) -> pd.DataFrame:
    """OHLCV로 팩터 시계열을 생성 (IC/ML 학습용)."""
    close = _col(df, "close").astype(float)
    high = _col(df, "high"); low = _col(df, "low")
    vol = _col(df, "volume")
    f = pd.DataFrame(index=df.index)

    # ── 1·2. 모멘텀 / 반전 (가격 패턴, 공개 팩터) ──
    f["mom_1m"] = close.pct_change(21)
    f["mom_3m"] = close.pct_change(63)
    f["mom_6m"] = close.pct_change(126)
    f["mom_12m"] = close.pct_change(252)
    f["mom_12_1"] = close.shift(21).pct_change(231)      # 12-1 모멘텀(고전)
    f["reversal_1w"] = -close.pct_change(5)              # 단기반전
    f["dist_52w_high"] = close / close.rolling(252).max() - 1

    # ── 1. 변동성 (저변동성 팩터) ──
    ret = close.pct_change()
    f["vol_20d"] = ret.rolling(20).std()
    f["vol_60d"] = ret.rolling(60).std()

    # ── 1. 거래량 ──
    if vol is not None:
        f["vol_change"] = vol.rolling(5).mean() / vol.rolling(60).mean()
        f["dollar_vol"] = (close * vol).rolling(20).mean()

    # ── 3. 기술지표 (RSI·BB·MACD 여러 기간) ──
    f["rsi_14"] = _rsi(close, 14)
    f["rsi_7"] = _rsi(close, 7)
    bb_mid = close.rolling(20).mean(); bb_std = close.rolling(20).std()
    f["bb_pos"] = (close - bb_mid) / (2 * bb_std).replace(0, np.nan)
    ema12 = close.ewm(span=12).mean(); ema26 = close.ewm(span=26).mean()
    macd = ema12 - ema26
    f["macd_hist"] = macd - macd.ewm(span=9).mean()
    f["macd_hist"] = f["macd_hist"] / close                # 가격정규화
    f["px_sma50"] = close / close.rolling(50).mean() - 1
    f["px_sma200"] = close / close.rolling(200).mean() - 1

    # ── 4. 새 합성 팩터 (수학적 조합) ──
    f["risk_adj_mom"] = f["mom_3m"] / f["vol_20d"].replace(0, np.nan)   # 위험조정 모멘텀
    if vol is not None:
        f["volwt_mom"] = f["mom_3m"] * f["vol_change"]                  # 거래량가중 모멘텀
    dd = close / close.rolling(126).max() - 1
    f["mom_quality"] = f["mom_6m"] / dd.abs().replace(0, np.nan)        # 낙폭대비 모멘텀
    f["trend_consist"] = (ret.rolling(20).apply(lambda x: (x > 0).mean()))  # 상승일 비율

    return f


def compute_factors(df: pd.DataFrame) -> dict:
    """최신 시점 팩터값 dict (실시간 종목선정/랭킹용)."""
    f = factor_timeseries(df)
    if f.empty:
        return {}
    last = f.iloc[-1]
    return {k: (float(v) if pd.notna(v) else None) for k, v in last.items()}


# IC 분석(2026-06-14, 대형주 40종목)으로 검증된 상위 팩터와 방향(부호).
# 저변동성 이상현상(-vol), 12-1 모멘텀(+), 단기반전(-1m), 52주고점근접(+) 등.
# 각 팩터를 방향에 맞춰 더해 합성 점수를 만든다. (IC≈0.05~0.08 = 약한 예측력 →
# 단독 신호가 아니라 게이트 통과 종목들의 '우선순위 랭킹'으로 사용 권장.)
FACTOR_WEIGHTS = {
    "vol_20d": -1.0,        # 저변동성 선호
    "mom_12_1": +1.0,       # 모멘텀
    "mom_1m": -1.0,         # 단기반전
    "dist_52w_high": +1.0,  # 고점 근접(추세)
    "bb_pos": -1.0,         # 밴드 하단 선호(눌림)
    "vol_change": +1.0,     # 거래량 증가
}


def factor_score(universe_data: dict) -> pd.Series:
    """
    여러 종목의 최신 팩터를 횡단면 z-score로 표준화하고 가중합한 합성점수.
    높을수록 (검증된 팩터 기준) 매력적. 게이트 통과 종목 우선순위에 사용.

    반환: {symbol: score} 내림차순 Series
    """
    rows = {}
    for sym, df in universe_data.items():
        fac = compute_factors(df)
        if fac:
            rows[sym] = fac
    if len(rows) < 3:
        return pd.Series(dtype=float)
    fdf = pd.DataFrame(rows).T
    score = pd.Series(0.0, index=fdf.index)
    for fac, w in FACTOR_WEIGHTS.items():
        if fac not in fdf.columns:
            continue
        col = pd.to_numeric(fdf[fac], errors="coerce")
        z = (col - col.mean()) / col.std()
        score = score.add(w * z.fillna(0), fill_value=0)
    return score.sort_values(ascending=False)


def _build_panel(universe_data: dict, forward_days: int = 20, sample_every: int = 10):
    """여러 종목의 팩터 + 미래수익 라벨을 패널로 합친다."""
    rows = []
    for sym, df in universe_data.items():
        if df is None or len(df) < 260:
            continue
        f = factor_timeseries(df)
        close = _col(df, "close").astype(float)
        fwd = close.shift(-forward_days) / close - 1     # 미래 N일 수익
        f = f.iloc[::sample_every].copy()
        f["fwd_ret"] = fwd.reindex(f.index)
        f["symbol"] = sym
        rows.append(f)
    if not rows:
        return pd.DataFrame()
    panel = pd.concat(rows).dropna(subset=["fwd_ret"])
    return panel


def ic_analysis(universe_data: dict, forward_days: int = 20) -> pd.DataFrame:
    """
    팩터별 정보계수(IC) = 팩터값과 미래수익의 순위상관(스피어만).
    |IC|>0.03 이면 약한 예측력, >0.05 면 유의미(퀀트 통념).
    """
    panel = _build_panel(universe_data, forward_days)
    if panel.empty:
        return pd.DataFrame()
    factor_cols = [c for c in panel.columns if c not in ("fwd_ret", "symbol")]
    out = []
    for fc in factor_cols:
        sub = panel[[fc, "fwd_ret"]].dropna()
        if len(sub) < 50:
            continue
        # 스피어만 = 순위 변환 후 피어슨 (scipy 의존 제거)
        ic = sub[fc].rank().corr(sub["fwd_ret"].rank())
        out.append({"factor": fc, "IC": round(float(ic), 4), "n": len(sub)})
    res = pd.DataFrame(out).sort_values("IC", key=lambda s: s.abs(), ascending=False)
    return res.reset_index(drop=True)


def train_factor_model(universe_data: dict, forward_days: int = 20):
    """
    AI 팩터 모델(5): 팩터행렬 → 미래 상승(수익>0) 확률 학습.
    시간순 train/test 분할, GradientBoosting, AUC + 팩터중요도 반환.
    """
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.metrics import roc_auc_score

    panel = _build_panel(universe_data, forward_days)
    if panel.empty or len(panel) < 200:
        return {"error": "표본 부족"}
    factor_cols = [c for c in panel.columns if c not in ("fwd_ret", "symbol")]
    panel = panel.sort_index()
    panel[factor_cols] = panel[factor_cols].replace([np.inf, -np.inf], np.nan)
    panel = panel.dropna(subset=factor_cols)
    X = panel[factor_cols].values
    y = (panel["fwd_ret"] > 0).astype(int).values
    split = int(len(panel) * 0.7)              # 시간순 분할 (미래누수 방지)
    Xtr, Xte, ytr, yte = X[:split], X[split:], y[:split], y[split:]
    if len(np.unique(yte)) < 2:
        return {"error": "테스트 라벨 단일"}
    model = GradientBoostingClassifier(n_estimators=100, max_depth=3, random_state=42)
    model.fit(Xtr, ytr)
    auc = roc_auc_score(yte, model.predict_proba(Xte)[:, 1])
    imp = sorted(zip(factor_cols, model.feature_importances_),
                 key=lambda x: -x[1])
    return {"auc": round(float(auc), 4), "n_train": len(Xtr), "n_test": len(Xte),
            "base_up_rate": round(float(y.mean()), 3),
            "top_factors": [(f, round(float(i), 3)) for f, i in imp[:8]],
            "model": model, "factor_cols": factor_cols}

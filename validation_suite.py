# validation_suite.py — 나머지 검증 항목 통합 (13종)
#
# 거래내역/자산곡선/OHLC/벤치마크수익률 또는 run_fn 콜러블로 계산한다.
# 엔진 독립적이므로 주식·코인 양쪽에서 동일하게 사용한다.
#
#   6  스트레스 테스트       — stress_test
#   7  거래비용 시뮬레이션    — cost_sensitivity   (26 Execution / 27 유동성 포함)
#   8  용량 분석            — capacity_analysis
#   17 regime 분석          — regime_analysis
#   19 팩터 익스포저         — factor_exposure
#   22 파라미터 드리프트      — parameter_drift
#   28 갭 테스트 (주식)      — gap_analysis
#   29 Regime Shift Test    — regime_shift_test
#   30 크로스마켓           — cross_market   (여러 심볼 run_fn 집계)
#   31 크로스타임프레임       — cross_timeframe
#   32 리얼리티 체크         — reality_check
#   34 PBO                 — pbo

import itertools
import numpy as np
import pandas as pd


# ── 17. Regime 분석 ──────────────────────────────────────────────
def regime_analysis(stra_returns, bench_returns, ma_window: int = 50) -> dict:
    """벤치마크의 N일 MA 기준 강세/약세 국면별 전략 성과를 본다."""
    s = pd.Series(np.asarray(stra_returns, dtype=float)).reset_index(drop=True)
    b = pd.Series(np.asarray(bench_returns, dtype=float)).reset_index(drop=True)
    n = min(len(s), len(b))
    if n < ma_window + 5:
        return {"error": "데이터 부족"}
    s, b = s.iloc[-n:].reset_index(drop=True), b.iloc[-n:].reset_index(drop=True)
    bench_price = (1 + b).cumprod()
    bull = bench_price > bench_price.rolling(ma_window).mean()
    out = {}
    for label, mask in [("강세장", bull), ("약세장", ~bull)]:
        rr = s[mask.fillna(False)]
        if len(rr) > 0:
            out[label] = {"기간수": int(len(rr)),
                          "평균수익%": round(float(rr.mean()) * 100, 3),
                          "누적%": round(float((1 + rr).prod() - 1) * 100, 2)}
    return out


# ── 19. 팩터 익스포저 ────────────────────────────────────────────
def factor_exposure(stra_returns, bench_returns) -> dict:
    """전략수익을 벤치마크에 회귀: 알파/베타/R². 베타↓면 시장중립적."""
    s = np.asarray(stra_returns, dtype=float)
    b = np.asarray(bench_returns, dtype=float)
    n = min(len(s), len(b))
    if n < 10:
        return {"error": "데이터 부족"}
    s, b = s[-n:], b[-n:]
    beta, alpha = np.polyfit(b, s, 1)
    pred = alpha + beta * b
    ss_res = np.sum((s - pred) ** 2)
    ss_tot = np.sum((s - s.mean()) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return {"beta": round(float(beta), 3), "alpha_bp": round(float(alpha) * 1e4, 2),
            "r2": round(float(r2), 3),
            "해석": "시장중립적" if abs(beta) < 0.3 else ("시장순응" if beta > 0 else "역방향")}


# ── 22. 파라미터 드리프트 ────────────────────────────────────────
def parameter_drift(fold_params: list) -> dict:
    """워크포워드 fold별 선택 파라미터의 변동성. 클수록 불안정(과최적화)."""
    if not fold_params:
        return {"error": "fold 없음"}
    keys = fold_params[0].keys()
    drift = {}
    for k in keys:
        vals = [float(f[k]) for f in fold_params if k in f]
        if len(vals) >= 2 and np.mean(vals) != 0:
            drift[k] = {"평균": round(np.mean(vals), 1),
                        "변동계수": round(float(np.std(vals) / abs(np.mean(vals))), 3),
                        "값들": vals}
    avg_cv = np.mean([d["변동계수"] for d in drift.values()]) if drift else 0
    return {"per_param": drift, "평균변동계수": round(float(avg_cv), 3),
            "안정성": "안정" if avg_cv < 0.3 else "드리프트 큼(과최적화 의심)"}


# ── 28. 갭 테스트 (주식) ─────────────────────────────────────────
def gap_analysis(ohlc: pd.DataFrame, stop_pct: float = 0.05) -> dict:
    """전일종가 대비 시가 갭 분포. 스탑을 갭으로 건너뛰는 위험 측정."""
    df = ohlc.copy()
    cols = {c.lower(): c for c in df.columns}
    o, c = cols.get("open"), cols.get("close")
    if not o or not c:
        return {"error": "OHLC 없음"}
    gap = df[o] / df[c].shift(1) - 1
    gap = gap.dropna()
    if len(gap) < 10:
        return {"error": "데이터 부족"}
    down_gap_beyond_stop = (gap <= -stop_pct).mean()
    return {"평균갭%": round(float(gap.mean()) * 100, 3),
            "갭표준편차%": round(float(gap.std()) * 100, 3),
            "최대하락갭%": round(float(gap.min()) * 100, 2),
            f"스탑초과하락갭빈도%": round(float(down_gap_beyond_stop) * 100, 2),
            "해석": "갭리스크 큼" if down_gap_beyond_stop > 0.02 else "갭리스크 양호"}


# ── 32. 리얼리티 체크 (White's RC 간이) ──────────────────────────
def reality_check(trade_returns, simulations: int = 5000) -> dict:
    """거래수익 평균이 0(무가치)보다 유의하게 큰지 부트스트랩 검정."""
    r = np.asarray(trade_returns, dtype=float)
    if len(r) < 5:
        return {"error": "거래 부족"}
    rng = np.random.default_rng(32)
    centered = r - r.mean()  # 귀무가설: 평균=0
    boot = [rng.choice(centered, len(r), replace=True).mean() for _ in range(simulations)]
    p_value = float((np.array(boot) >= r.mean()).mean())
    return {"평균거래수익%": round(float(r.mean()) * 100, 3), "p_value": round(p_value, 4),
            "유의성": "유의(우연 아님)" if p_value < 0.05 else "유의하지 않음(우연 가능)"}


# ── 34. PBO (백테스트 과최적화 확률) ─────────────────────────────
def pbo(is_oos_pairs: list) -> dict:
    """
    Probability of Backtest Overfitting (간이).
    is_oos_pairs: [(is_metric, oos_metric), ...] — 여러 파라미터/분할의 IS·OOS 쌍.
    IS 최고 구성이 OOS에서 중앙값 미만으로 떨어지는 빈도를 본다.
    """
    if len(is_oos_pairs) < 4:
        return {"error": "표본 부족"}
    arr = np.array(is_oos_pairs, dtype=float)
    is_v, oos_v = arr[:, 0], arr[:, 1]
    best_is_idx = int(is_v.argmax())
    oos_median = np.median(oos_v)
    # IS 최고 구성의 OOS 순위(백분위) — 낮을수록 과최적화
    oos_rank = float((oos_v < oos_v[best_is_idx]).mean())
    underperforms = oos_v[best_is_idx] < oos_median
    return {"is_best_oos_percentile": round(oos_rank * 100, 1),
            "pbo_estimate": round(float((oos_v < oos_median).mean()) if underperforms else 1 - oos_rank, 3),
            "과최적화": "의심" if underperforms else "양호"}


# ── 6. 스트레스 테스트 ───────────────────────────────────────────
def stress_test(run_fn, windows: dict, metric: str = "total_return_pct") -> dict:
    """위기 구간별 성과. run_fn(start,end)->dict, windows={"라벨":(start,end)}."""
    out = {}
    for label, (s, e) in windows.items():
        try:
            r = run_fn(s, e)
            out[label] = {metric: round(float(r.get(metric, 0)), 2),
                          "mdd%": round(float(r.get("max_drawdown_pct", 0)), 2)}
        except Exception as ex:
            out[label] = {"error": str(ex)[:40]}
    return out


# ── 8. 용량 분석 ─────────────────────────────────────────────────
def capacity_analysis(avg_daily_volume_usd: float, max_participation: float = 0.01,
                      max_position_pct: float = 0.10) -> dict:
    """일평균 거래대금의 max_participation(기본1%)까지만 체결 가능하다고 보고
    종목당 최대 포지션·포트폴리오 수용 자본을 추정한다."""
    max_position_usd = avg_daily_volume_usd * max_participation
    max_portfolio = max_position_usd / max_position_pct if max_position_pct > 0 else 0
    return {"일평균거래대금$": round(avg_daily_volume_usd),
            "종목당최대$": round(max_position_usd),
            "수용가능시드$": round(max_portfolio)}


# ── 7/26/27. 거래비용·실행·유동성 민감도 ─────────────────────────
def cost_sensitivity(trade_returns, cost_levels=(0.0, 0.0005, 0.001, 0.002, 0.005)) -> dict:
    """거래당 비용(수수료+슬리피지+스프레드)을 차감했을 때 누적수익 변화.
    유동성 악화(높은 비용)에서도 살아남는지(26 Execution/27 유동성 포함)."""
    r = np.asarray(trade_returns, dtype=float)
    if len(r) < 2:
        return {"error": "거래 부족"}
    out = {}
    for c in cost_levels:
        # 진입+청산 2회 비용 차감
        net = r - 2 * c
        out[f"비용{c*100:.2f}%"] = round(float(np.prod(1 + net) - 1) * 100, 2)
    base = np.prod(1 + r) - 1
    # 손익분기 비용: 누적수익이 0이 되는 거래당 비용 근사
    return {"누적수익_비용별%": out,
            "해석": "비용 견딤" if out[f"비용0.10%"] > 0 else "비용에 취약"}


# ── 29. Regime Shift Test ────────────────────────────────────────
def regime_shift_test(stra_returns, bench_returns, ma_window: int = 50) -> dict:
    """국면 '전환' 직후(강→약, 약→강) 구간의 전략 성과. 전환에 취약한지."""
    s = pd.Series(np.asarray(stra_returns, dtype=float)).reset_index(drop=True)
    b = pd.Series(np.asarray(bench_returns, dtype=float)).reset_index(drop=True)
    n = min(len(s), len(b))
    if n < ma_window + 10:
        return {"error": "데이터 부족"}
    s, b = s.iloc[-n:].reset_index(drop=True), b.iloc[-n:].reset_index(drop=True)
    bench_price = (1 + b).cumprod()
    bull = (bench_price > bench_price.rolling(ma_window).mean()).fillna(False)
    shift = bull != bull.shift(1)
    # 전환 후 5거래일 윈도우
    shift_idx = shift[shift].index
    rets = []
    for i in shift_idx:
        window = s.iloc[i:i + 5]
        if len(window) > 0:
            rets.append(float((1 + window).prod() - 1))
    if not rets:
        return {"error": "전환 없음"}
    return {"전환횟수": len(rets), "전환후평균수익%": round(float(np.mean(rets)) * 100, 2),
            "해석": "전환 견딤" if np.mean(rets) >= 0 else "전환에 취약"}


# ── 30/31. 크로스마켓 / 크로스타임프레임 ─────────────────────────
def cross_market(run_fn, symbols: list, metric: str = "total_return_pct") -> dict:
    """여러 심볼에서 동일 전략 성과. 특정 종목 의존인지 분산 검증."""
    res = {}
    for sym in symbols:
        try:
            res[sym] = round(float(run_fn(sym).get(metric, 0)), 2)
        except Exception:
            res[sym] = None
    vals = [v for v in res.values() if v is not None]
    pos = sum(1 for v in vals if v > 0)
    return {"per_symbol": res,
            "양수비율": f"{pos}/{len(vals)}",
            "해석": "범용적" if vals and pos >= len(vals) * 0.6 else "종목의존적"}


def cross_timeframe(run_fn_tf, timeframes: list, metric: str = "total_return_pct") -> dict:
    """여러 타임프레임에서 성과. run_fn_tf(tf)->dict. 특정 TF 의존인지."""
    res = {}
    for tf in timeframes:
        try:
            res[tf] = round(float(run_fn_tf(tf).get(metric, 0)), 2)
        except Exception as e:
            res[tf] = None
    vals = [v for v in res.values() if v is not None]
    pos = sum(1 for v in vals if v > 0)
    return {"per_timeframe": res, "양수비율": f"{pos}/{len(vals)}",
            "해석": "TF 견고" if vals and pos >= len(vals) * 0.6 else "특정 TF 의존"}

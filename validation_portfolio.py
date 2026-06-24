# validation_portfolio.py — 포트폴리오/상관/무작위 진입 검증
#
# 단일종목 지표(validation.py)와 달리 여러 종목을 묶었을 때의 분산효과·
# 상관위험과, 전략의 진입로직이 무작위보다 나은지를 검증한다.
#
#   18 상관관계        — correlation_matrix
#   37 포트폴리오 테스트 — portfolio_test
#   25 Random Entry    — random_entry_test
#
# ⚠️ 전체기간 단일 계산은 과최적화 전략을 미화할 수 있다(2026-06-14 교훈).
#    상관·분산 효과는 그래도 유효하나, 수익지표는 워크포워드와 병행 해석할 것.

import numpy as np
import pandas as pd


def _equity_returns(equity):
    e = np.asarray(equity, dtype=float)
    e = e[e > 0]
    return np.diff(e) / e[:-1] if len(e) >= 2 else np.array([])


def correlation_matrix(returns_by_symbol: dict) -> dict:
    """
    18 상관관계. 종목별 (날짜정렬) 수익률 시리즈 dict를 받아 상관행렬과
    평균 상관(분산효과 가늠)을 반환한다. 상관 낮을수록 포트 분산효과 큼.
    """
    df = pd.DataFrame(returns_by_symbol).dropna()
    if df.shape[1] < 2 or len(df) < 3:
        return {"error": "종목/데이터 부족"}
    corr = df.corr()
    # 대각 제외 평균 상관
    n = corr.shape[0]
    off = (corr.values.sum() - n) / (n * n - n)
    return {
        "matrix": corr.round(2).to_dict(),
        "avg_correlation": round(float(off), 3),
        "diversification": "양호(저상관)" if off < 0.5 else "제한적(고상관)",
    }


def portfolio_test(equity_by_symbol: dict, periods: int = 365) -> dict:
    """
    37 포트폴리오 테스트. 종목별 자산곡선을 동일비중 결합해 포트폴리오
    수익/MDD/샤프를 계산하고, 개별 평균과 비교해 분산효과를 본다.
    """
    series = {}
    for sym, eq in equity_by_symbol.items():
        r = _equity_returns(eq)
        if len(r) > 0:
            series[sym] = pd.Series(r)
    if len(series) < 2:
        return {"error": "종목 부족"}
    df = pd.DataFrame(series).dropna()
    if len(df) < 3:
        return {"error": "정렬 데이터 부족"}

    port_ret = df.mean(axis=1)   # 동일비중 일별 수익률
    port_eq = (1 + port_ret).cumprod()
    total = float(port_eq.iloc[-1] - 1) * 100
    peak = port_eq.cummax()
    mdd = float(((port_eq - peak) / peak).min()) * 100
    sharpe = float(port_ret.mean() / port_ret.std() * np.sqrt(periods)) if port_ret.std() > 0 else 0.0

    # 개별 종목 평균 지표
    ind_sharpes = []
    for sym in df.columns:
        s = df[sym]
        ind_sharpes.append(s.mean() / s.std() * np.sqrt(periods) if s.std() > 0 else 0)
    avg_ind_sharpe = float(np.mean(ind_sharpes))

    return {
        "portfolio_return_pct": round(total, 2),
        "portfolio_mdd_pct": round(mdd, 2),
        "portfolio_sharpe": round(sharpe, 3),
        "avg_individual_sharpe": round(avg_ind_sharpe, 3),
        "diversification_gain": round(sharpe - avg_ind_sharpe, 3),  # 양수면 분산효과
    }


def random_entry_test(trade_returns, n_trades: int, win_rate_hint: float = None,
                      simulations: int = 2000) -> dict:
    """
    25 Random Entry Test. 전략의 거래 손익률 분포에서 '무작위로 같은 횟수
    진입했을 때'의 누적수익 분포를 만들고, 실제 전략 누적수익이 그 분포의
    상위 몇 %인지 본다. 상위(>80%)면 진입 타이밍이 무작위보다 유의미.

    여기서는 거래손익 부호를 섞는 대신, 실제 거래수익을 무작위 복원추출해
    '진입 시점 정보가 없을 때'의 베이스라인을 만든다(진입 엣지 근사 검정).
    """
    r = np.asarray(trade_returns, dtype=float)
    if len(r) < 3:
        return {"error": "거래 부족"}
    actual = float(np.prod(1 + r) - 1) * 100
    rng = np.random.default_rng(21)
    # 베이스라인: 같은 분포에서 무작위 추출(진입 타이밍 정보 제거) → 부호 셔플
    sims = []
    for _ in range(simulations):
        shuffled = rng.permutation(r)
        # 무작위로 절반만 진입(진입 선택 엣지 제거 근사)
        mask = rng.random(len(shuffled)) < 0.5
        sub = shuffled[mask] if mask.any() else shuffled
        sims.append(np.prod(1 + sub) - 1)
    sims = np.array(sims) * 100
    pct = float((sims < actual).mean()) * 100
    return {
        "actual_return_pct": round(actual, 2),
        "random_median_pct": round(float(np.median(sims)), 2),
        "actual_percentile": round(pct, 1),
        "beats_random": pct > 80,
    }

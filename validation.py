# validation.py — 백테스트 결과 검증 지표 모음 (퀀트 표준 검증 항목)
#
# 입력: 자산곡선(equity curve)과 거래별 손익률(trade returns) 두 가지면
#       아래 항목 대부분을 계산할 수 있다. 재실행이 필요한 항목(워크포워드,
#       파라미터 안정성 등)은 validation_advanced.py를 참고.
#
# 이 모듈이 커버하는 검증 항목 (사용자 37개 목록 기준):
#   9  켈리공식 (Kelly)            — kelly_fraction
#   10 MDD 제한                    — max_drawdown
#   11 샤프 비율                   — sharpe_ratio
#   12 소르티노 비율               — sortino_ratio
#   13 Calmar Ratio               — calmar_ratio
#   14 수익 팩터                   — profit_factor
#   15 Recovery Factor            — recovery_factor
#   16 파산확률 (Risk of Ruin)     — risk_of_ruin
#   5  몬테카를로 시뮬레이션       — monte_carlo
#   20 Bootstrap Test             — bootstrap_metric_ci
#   23 Trade Sequence Test        — trade_sequence_test
#   33 Deflated 샤프비율          — deflated_sharpe
#   35 Tail Risk Analysis         — tail_risk
#   36 CVaR (조건부 VaR)          — value_at_risk / cvar

import math
import numpy as np
import pandas as pd

# 연율화 계수: 미국 주식 일봉 기준 252 거래일. 코인은 365로 호출 시 지정.
TRADING_DAYS = 252


# ─────────────────────────────────────────────────────────────────────
# 기본 위험조정 수익 지표
# ─────────────────────────────────────────────────────────────────────

def _to_returns(equity: np.ndarray) -> np.ndarray:
    """자산곡선 → 기간 수익률 배열."""
    equity = np.asarray(equity, dtype=float)
    equity = equity[equity > 0]
    if len(equity) < 2:
        return np.array([])
    return np.diff(equity) / equity[:-1]


def sharpe_ratio(equity, risk_free: float = 0.03, periods: int = TRADING_DAYS) -> float:
    """샤프 비율 (11). 초과수익 평균 / 변동성, 연율화."""
    r = _to_returns(equity)
    if len(r) < 2 or r.std() == 0:
        return 0.0
    rf_period = risk_free / periods
    return float((r.mean() - rf_period) / r.std() * math.sqrt(periods))


def sortino_ratio(equity, risk_free: float = 0.03, periods: int = TRADING_DAYS) -> float:
    """소르티노 비율 (12). 하방 변동성만으로 나눔 — 기관 선호 지표."""
    r = _to_returns(equity)
    if len(r) < 2:
        return 0.0
    rf_period = risk_free / periods
    downside = r[r < 0]
    dd = downside.std() if len(downside) > 0 else 0.0
    if dd == 0:
        return 0.0
    return float((r.mean() - rf_period) / dd * math.sqrt(periods))


def max_drawdown(equity) -> float:
    """최대 낙폭 MDD (10). 음수 비율로 반환 (-0.25 = -25%)."""
    equity = np.asarray(equity, dtype=float)
    if len(equity) < 2:
        return 0.0
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    return float(dd.min())


def calmar_ratio(equity, periods: int = TRADING_DAYS) -> float:
    """Calmar (13). 연환산 수익 / |MDD|."""
    equity = np.asarray(equity, dtype=float)
    if len(equity) < 2:
        return 0.0
    total_return = equity[-1] / equity[0] - 1
    years = len(equity) / periods
    cagr = (1 + total_return) ** (1 / years) - 1 if years > 0 and (1 + total_return) > 0 else 0.0
    mdd = abs(max_drawdown(equity))
    return float(cagr / mdd) if mdd > 0 else 0.0


def profit_factor(trade_returns) -> float:
    """수익 팩터 (14). 총이익 / 총손실. 1.5↑ 양호, 2↑ 우수."""
    r = np.asarray(trade_returns, dtype=float)
    gains = r[r > 0].sum()
    losses = abs(r[r < 0].sum())
    if losses == 0:
        return float("inf") if gains > 0 else 0.0
    return float(gains / losses)


def recovery_factor(equity) -> float:
    """Recovery Factor (15). 총수익 / |MDD| — 손실 회복 속도."""
    equity = np.asarray(equity, dtype=float)
    if len(equity) < 2:
        return 0.0
    total_return = equity[-1] / equity[0] - 1
    mdd = abs(max_drawdown(equity))
    return float(total_return / mdd) if mdd > 0 else 0.0


# ─────────────────────────────────────────────────────────────────────
# 켈리공식 (9, ●●●●) — 최적 베팅 비율
# ─────────────────────────────────────────────────────────────────────

def kelly_fraction(trade_returns) -> dict:
    """
    켈리 공식 (9). 거래별 손익률로 최적 자본 투입 비율을 계산한다.

    f* = W - (1-W)/R   (W=승률, R=평균이익/평균손실 비율)

    반환: {kelly, half_kelly, win_rate, payoff_ratio}
      - kelly:      이론적 최적 비율 (변동성 큼 → 보통 half-kelly 사용)
      - half_kelly: 실무 권장 (켈리의 절반, 파산위험 大폭 감소)
    """
    r = np.asarray(trade_returns, dtype=float)
    if len(r) == 0:
        return {"kelly": 0.0, "half_kelly": 0.0, "win_rate": 0.0, "payoff_ratio": 0.0}

    wins = r[r > 0]
    losses = r[r < 0]
    n = len(r)
    win_rate = len(wins) / n if n else 0.0
    avg_win = wins.mean() if len(wins) > 0 else 0.0
    avg_loss = abs(losses.mean()) if len(losses) > 0 else 0.0

    if avg_loss == 0:
        payoff = float("inf") if avg_win > 0 else 0.0
        kelly = win_rate          # 손실 없음 → 풀 베팅
    else:
        payoff = avg_win / avg_loss
        kelly = win_rate - (1 - win_rate) / payoff

    kelly = max(0.0, kelly)       # 음수 = 진입 금지 신호 (기대값 음수)
    return {
        "kelly": round(kelly, 4),
        "half_kelly": round(kelly / 2, 4),
        "win_rate": round(win_rate, 4),
        "payoff_ratio": round(payoff, 3) if payoff != float("inf") else None,
    }


# ─────────────────────────────────────────────────────────────────────
# 파산확률 (16) / 꼬리위험 (35) / CVaR (36)
# ─────────────────────────────────────────────────────────────────────

def risk_of_ruin(trade_returns, risk_per_trade: float = None,
                 ruin_threshold: float = 0.5, simulations: int = 5000,
                 horizon: int = 200) -> float:
    """
    파산확률 (16). 거래 손익률 분포에서 자본이 ruin_threshold(기본 -50%)까지
    빠질 확률을 몬테카를로로 추정한다.
    """
    r = np.asarray(trade_returns, dtype=float)
    if len(r) < 2:
        return 0.0
    rng = np.random.default_rng(42)
    ruined = 0
    for _ in range(simulations):
        sample = rng.choice(r, size=horizon, replace=True)
        equity = 1.0
        hit = False
        for x in sample:
            equity *= (1 + x)
            if equity <= (1 - ruin_threshold):
                hit = True
                break
        if hit:
            ruined += 1
    return round(ruined / simulations, 4)


def value_at_risk(trade_returns, confidence: float = 0.95) -> float:
    """VaR. confidence(95%) 신뢰수준에서 단일 거래 최대 예상 손실 (음수)."""
    r = np.asarray(trade_returns, dtype=float)
    if len(r) == 0:
        return 0.0
    return float(np.percentile(r, (1 - confidence) * 100))


def cvar(trade_returns, confidence: float = 0.95) -> float:
    """
    CVaR / 기대손실 (36). VaR을 초과하는 꼬리 손실들의 평균.
    VaR보다 꼬리위험을 더 잘 포착 — 기관 표준.
    """
    r = np.asarray(trade_returns, dtype=float)
    if len(r) == 0:
        return 0.0
    var = value_at_risk(r, confidence)
    tail = r[r <= var]
    return float(tail.mean()) if len(tail) > 0 else float(var)


def tail_risk(trade_returns) -> dict:
    """
    꼬리위험 분석 (35). 분포의 비대칭/뚱뚱한 꼬리 정도를 본다.
    반환: skew(왜도), kurtosis(첨도), worst_trade, best_trade, var95, cvar95
    """
    r = np.asarray(trade_returns, dtype=float)
    if len(r) < 2:
        return {"skew": 0.0, "kurtosis": 0.0, "worst": 0.0, "best": 0.0,
                "var95": 0.0, "cvar95": 0.0}
    mean, std = r.mean(), r.std()
    if std == 0:
        skew = kurt = 0.0
    else:
        skew = float(((r - mean) ** 3).mean() / std ** 3)
        kurt = float(((r - mean) ** 4).mean() / std ** 4 - 3)  # 초과첨도
    return {
        "skew": round(skew, 3),
        "kurtosis": round(kurt, 3),
        "worst": round(float(r.min()), 4),
        "best": round(float(r.max()), 4),
        "var95": round(value_at_risk(r, 0.95), 4),
        "cvar95": round(cvar(r, 0.95), 4),
    }


# ─────────────────────────────────────────────────────────────────────
# 몬테카를로 (5, ●●) / Bootstrap (20) / Trade Sequence (23)
# ─────────────────────────────────────────────────────────────────────

def monte_carlo(trade_returns, simulations: int = 2000,
                initial: float = 1.0) -> dict:
    """
    몬테카를로 시뮬레이션 (5). 거래 손익률을 무작위 복원추출로 재배열해
    수천 개의 가능한 자산곡선을 생성하고, 최종수익/MDD 분포를 본다.

    실제 거래순서가 운이 좋았던 것인지(과최적화의 일종) 판별하는 데 쓴다.
    반환: 최종수익률·MDD의 5/50/95 백분위 + 손실확률.
    """
    r = np.asarray(trade_returns, dtype=float)
    if len(r) < 2:
        return {}
    rng = np.random.default_rng(7)
    finals, mdds = [], []
    n = len(r)
    for _ in range(simulations):
        sample = rng.choice(r, size=n, replace=True)
        eq = initial * np.cumprod(1 + sample)
        finals.append(eq[-1] / initial - 1)
        mdds.append(max_drawdown(np.concatenate([[initial], eq])))
    finals = np.array(finals)
    mdds = np.array(mdds)
    return {
        "return_p5": round(float(np.percentile(finals, 5)) * 100, 2),
        "return_p50": round(float(np.percentile(finals, 50)) * 100, 2),
        "return_p95": round(float(np.percentile(finals, 95)) * 100, 2),
        "mdd_p50": round(float(np.percentile(mdds, 50)) * 100, 2),
        "mdd_p95": round(float(np.percentile(mdds, 95)) * 100, 2),  # 최악권 MDD
        "prob_loss": round(float((finals < 0).mean()), 4),
    }


def bootstrap_metric_ci(trade_returns, metric_fn=None,
                        simulations: int = 2000, ci: float = 0.95) -> dict:
    """
    Bootstrap Test (20). 거래 표본을 복원추출해 지표의 신뢰구간을 추정한다.
    기본 지표는 평균 거래수익률. metric_fn으로 교체 가능.
    """
    r = np.asarray(trade_returns, dtype=float)
    if len(r) < 2:
        return {}
    if metric_fn is None:
        metric_fn = lambda x: float(np.mean(x))
    rng = np.random.default_rng(11)
    vals = [metric_fn(rng.choice(r, size=len(r), replace=True))
            for _ in range(simulations)]
    lo = (1 - ci) / 2 * 100
    hi = (1 + ci) / 2 * 100
    return {
        "point": round(metric_fn(r), 5),
        "ci_low": round(float(np.percentile(vals, lo)), 5),
        "ci_high": round(float(np.percentile(vals, hi)), 5),
        "prob_positive": round(float((np.array(vals) > 0).mean()), 4),
    }


def trade_sequence_test(trade_returns, simulations: int = 2000) -> dict:
    """
    Trade Sequence Test (23). 거래 순서를 무작위로 섞어 MDD가 순서에
    얼마나 민감한지 본다. 실제 MDD가 분포의 최악권이면 운이 나빴던 것이고,
    최선권이면 실제 운영 시 더 큰 MDD를 각오해야 한다는 뜻.
    """
    r = np.asarray(trade_returns, dtype=float)
    if len(r) < 2:
        return {}
    rng = np.random.default_rng(13)
    actual_eq = np.concatenate([[1.0], np.cumprod(1 + r)])
    actual_mdd = max_drawdown(actual_eq)
    mdds = []
    for _ in range(simulations):
        shuffled = r.copy()
        rng.shuffle(shuffled)
        eq = np.concatenate([[1.0], np.cumprod(1 + shuffled)])
        mdds.append(max_drawdown(eq))
    mdds = np.array(mdds)
    pct = float((mdds < actual_mdd).mean())   # 실제보다 깊은 MDD 비율
    return {
        "actual_mdd": round(actual_mdd * 100, 2),
        "mdd_p50": round(float(np.percentile(mdds, 50)) * 100, 2),
        "mdd_p95": round(float(np.percentile(mdds, 95)) * 100, 2),
        "actual_percentile": round(pct * 100, 1),
    }


# ─────────────────────────────────────────────────────────────────────
# Deflated 샤프 비율 (33) — 다중검정 보정
# ─────────────────────────────────────────────────────────────────────

def deflated_sharpe(equity, num_trials: int = 1,
                    periods: int = TRADING_DAYS) -> dict:
    """
    Deflated 샤프 비율 (33). 여러 파라미터 조합을 시도해 best를 고르면
    샤프가 운으로 부풀려진다. 시도 횟수(num_trials)를 반영해 '진짜 0보다
    큰가'의 확률을 보정한다. (Bailey & López de Prado 근사)

    반환: {sharpe, deflated_prob} — deflated_prob>0.95면 통계적으로 유의.
    """
    r = _to_returns(equity)
    if len(r) < 3:
        return {"sharpe": 0.0, "deflated_prob": 0.0}
    sr = sharpe_ratio(equity, periods=periods)
    n = len(r)
    # 수익률 왜도·첨도 반영한 샤프 표준오차
    mean, std = r.mean(), r.std()
    if std == 0:
        return {"sharpe": 0.0, "deflated_prob": 0.0}
    skew = ((r - mean) ** 3).mean() / std ** 3
    kurt = ((r - mean) ** 4).mean() / std ** 4
    sr_period = sr / math.sqrt(periods)   # 비연율 샤프
    se = math.sqrt((1 - skew * sr_period + (kurt - 1) / 4 * sr_period ** 2) / (n - 1))

    # 다중검정 기대 최대 샤프 (num_trials 중 best의 기대값)
    if num_trials > 1:
        from math import log
        e_max = (1 - 0.5772) * _norm_ppf(1 - 1.0 / num_trials) + \
                0.5772 * _norm_ppf(1 - 1.0 / (num_trials * math.e))
        sr0 = se * e_max
    else:
        sr0 = 0.0

    z = (sr_period - sr0) / se if se > 0 else 0.0
    return {"sharpe": round(sr, 3), "deflated_prob": round(_norm_cdf(z), 4)}


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _norm_ppf(p: float) -> float:
    """표준정규 역누적분포 (Acklam 근사)."""
    if p <= 0:
        return -10.0
    if p >= 1:
        return 10.0
    a = [-39.6968302866538, 220.946098424521, -275.928510446969,
         138.357751867269, -30.6647980661472, 2.50662827745924]
    b = [-54.4760987982241, 161.585836858041, -155.698979859887,
         66.8013118877197, -13.2806815528857]
    c = [-0.00778489400243029, -0.322396458041136, -2.40075827716184,
         -2.54973253934373, 4.37466414146497, 2.93816398269878]
    d = [0.00778469570904146, 0.32246712907004, 2.445134137143, 3.75440866190742]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


# ─────────────────────────────────────────────────────────────────────
# 통합 리포트
# ─────────────────────────────────────────────────────────────────────

def full_report(equity, trade_returns, periods: int = TRADING_DAYS,
                num_trials: int = 1, run_resampling: bool = True) -> dict:
    """
    자산곡선 + 거래손익률로 검증 지표 전체를 계산해 dict로 반환한다.
    run_resampling=False면 무거운 몬테카를로/부트스트랩을 건너뛴다.
    """
    equity = np.asarray(equity, dtype=float)
    r = np.asarray(trade_returns, dtype=float)
    rep = {
        # 위험조정 수익
        "sharpe": round(sharpe_ratio(equity, periods=periods), 3),
        "sortino": round(sortino_ratio(equity, periods=periods), 3),
        "calmar": round(calmar_ratio(equity, periods=periods), 3),
        "profit_factor": round(profit_factor(r), 3) if len(r) else 0.0,
        "recovery_factor": round(recovery_factor(equity), 3),
        "max_drawdown_pct": round(max_drawdown(equity) * 100, 2),
        # 베팅/파산
        "kelly": kelly_fraction(r),
        "risk_of_ruin": risk_of_ruin(r) if run_resampling else None,
        # 꼬리위험
        "tail_risk": tail_risk(r),
        # 견고성 (리샘플링)
        "monte_carlo": monte_carlo(r) if run_resampling else None,
        "bootstrap_mean_return": bootstrap_metric_ci(r) if run_resampling else None,
        "trade_sequence": trade_sequence_test(r) if run_resampling else None,
        "deflated_sharpe": deflated_sharpe(equity, num_trials=num_trials, periods=periods),
    }
    return rep


def format_report(rep: dict) -> str:
    """full_report 결과를 사람이 읽는 텍스트로 변환."""
    k = rep["kelly"]
    mc = rep.get("monte_carlo") or {}
    ts = rep.get("trade_sequence") or {}
    tr = rep["tail_risk"]
    bs = rep.get("bootstrap_mean_return") or {}
    ds = rep["deflated_sharpe"]
    lines = [
        "─" * 56,
        "  위험조정 수익 지표",
        f"    샤프(11): {rep['sharpe']:.2f}   소르티노(12): {rep['sortino']:.2f}   Calmar(13): {rep['calmar']:.2f}",
        f"    수익팩터(14): {rep['profit_factor']:.2f}   Recovery(15): {rep['recovery_factor']:.2f}   MDD(10): {rep['max_drawdown_pct']:.1f}%",
        "  켈리공식 (9)",
        f"    승률 {k['win_rate']*100:.1f}%  손익비 {k['payoff_ratio']}  →  켈리 {k['kelly']*100:.1f}%  (실무권장 half-kelly {k['half_kelly']*100:.1f}%)",
        "  파산/꼬리위험",
        f"    파산확률(16): {rep['risk_of_ruin']}   VaR95: {tr['var95']*100:.1f}%   CVaR95(36): {tr['cvar95']*100:.1f}%",
        f"    왜도 {tr['skew']}  첨도 {tr['kurtosis']}  최악거래 {tr['worst']*100:.1f}%  (35 Tail Risk)",
    ]
    if mc:
        lines += [
            "  몬테카를로 (5, 2000회)",
            f"    수익률 5%분위 {mc['return_p5']:+.1f}% / 중앙 {mc['return_p50']:+.1f}% / 95%분위 {mc['return_p95']:+.1f}%",
            f"    최악권 MDD(95%분위): {mc['mdd_p95']:.1f}%   손실확률: {mc['prob_loss']*100:.1f}%",
        ]
    if bs:
        lines += [
            "  Bootstrap (20)",
            f"    평균 거래수익률 {bs['point']*100:.2f}%  95% CI [{bs['ci_low']*100:.2f}%, {bs['ci_high']*100:.2f}%]  양수확률 {bs['prob_positive']*100:.0f}%",
        ]
    if ts:
        lines += [
            "  Trade Sequence (23)",
            f"    실제 MDD {ts['actual_mdd']:.1f}%  (셔플 중앙 {ts['mdd_p50']:.1f}% / 최악권 {ts['mdd_p95']:.1f}%)",
        ]
    lines += [
        "  Deflated 샤프 (33)",
        f"    유의확률 {ds['deflated_prob']*100:.1f}%  (>95%면 통계적으로 유의)",
        "─" * 56,
    ]
    return "\n".join(lines)

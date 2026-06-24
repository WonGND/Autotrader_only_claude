# validation_advanced.py — 백테스트 재실행이 필요한 고급 검증
#
# validation.py가 단일 백테스트의 거래/자산곡선으로 계산하는 지표라면,
# 이 모듈은 파라미터·기간·실행지연을 바꿔가며 백테스트를 여러 번 돌려야 하는
# 검증을 담당한다. 각 함수는 엔진에 독립적이도록 run_fn 콜러블을 받는다.
#
# 커버하는 검증 항목:
#   2  과최적화 검증 (Overfitting)        — overfitting_test
#   21 파라미터 안정성 (Parameter Stability) — parameter_stability_test
#   24 Time Delay Test                    — time_delay_test

import itertools
from datetime import datetime, timedelta
import numpy as np


# ─────────────────────────────────────────────────────────────────────
# 3. Walk-Forward Analysis — 과최적화 편향 없는 진짜 OOS 검증
# ─────────────────────────────────────────────────────────────────────

def walk_forward_analysis(run_fn, param_grid: dict, full_start: str, full_end: str,
                          n_folds: int = 4, train_ratio: float = 0.6,
                          metric: str = "total_return_pct") -> dict:
    """
    워크포워드 분석 (3). 항목 2/4/21의 편향을 제거하는 keystone 검증.

    기간을 n_folds 구간으로 굴리며, 각 fold마다 train 구간에서 격자 최적
    파라미터를 고르고 → 바로 다음 test 구간(미래)에 적용해 성과를 측정한다.
    test 구간은 파라미터 선택에 전혀 쓰이지 않으므로 진짜 OOS다.

    핵심 산출물 WFE(Walk-Forward Efficiency) = 평균 test성과 / 평균 train성과.
      WFE가 0.5↑면 실전 견고, 0 이하면 과최적화(미래에 무너짐).

    Args:
        run_fn:     run_fn(params, start, end) -> dict (metric 키)
        param_grid: 탐색 격자
        full_start, full_end: 전체 기간 (YYYY-MM-DD)
        n_folds:    워크포워드 구간 수
        train_ratio: 각 윈도우에서 train 비율 (나머지가 test)
    """
    keys = list(param_grid.keys())
    combos = list(itertools.product(*[param_grid[k] for k in keys]))

    d0 = datetime.strptime(full_start, "%Y-%m-%d")
    d1 = datetime.strptime(full_end, "%Y-%m-%d")
    total_days = (d1 - d0).days
    # 겹치지 않는 test 구간으로 분할, 각 fold는 앞쪽 train + 뒤쪽 test
    fold_days = total_days // n_folds
    test_days = int(fold_days * (1 - train_ratio))
    train_days = fold_days - test_days

    folds = []
    for i in range(n_folds):
        tr_start = d0 + timedelta(days=i * fold_days)
        tr_end = tr_start + timedelta(days=train_days)
        te_end = tr_end + timedelta(days=test_days)
        if te_end > d1:
            break
        fmt = lambda d: d.strftime("%Y-%m-%d")

        # train 구간 격자 최적화
        best_p, best_v = None, -1e18
        for combo in combos:
            params = dict(zip(keys, combo))
            try:
                v = run_fn(params, fmt(tr_start), fmt(tr_end)).get(metric)
            except Exception:
                v = None
            if v is not None and np.isfinite(v) and v > best_v:
                best_v, best_p = v, params
        if best_p is None:
            continue
        # test 구간(미래)에 적용 — 진짜 OOS
        try:
            te_v = float(run_fn(best_p, fmt(tr_end), fmt(te_end)).get(metric))
        except Exception:
            te_v = 0.0
        folds.append({"fold": i + 1, "train": [fmt(tr_start), fmt(tr_end)],
                      "test": [fmt(tr_end), fmt(te_end)], "params": best_p,
                      "train_value": round(best_v, 2), "test_value": round(te_v, 2)})

    if not folds:
        return {"error": "유효한 fold 없음"}

    train_avg = np.mean([f["train_value"] for f in folds])
    test_avg = np.mean([f["test_value"] for f in folds])
    test_pos = sum(1 for f in folds if f["test_value"] > 0)
    wfe = float(test_avg / train_avg) if train_avg != 0 else 0.0
    # 판정: 절반 이상 fold에서 test 양수 AND WFE>0.3
    robust = test_pos >= len(folds) / 2 and wfe > 0.3
    return {
        "metric": metric,
        "n_folds": len(folds),
        "train_avg": round(float(train_avg), 2),
        "test_avg": round(float(test_avg), 2),
        "wfe": round(wfe, 3),
        "test_positive_folds": f"{test_pos}/{len(folds)}",
        "robust": robust,
        "folds": folds,
    }


def format_walk_forward(wf: dict) -> str:
    if "error" in wf:
        return f"  [3] 워크포워드 — {wf['error']}"
    verdict = "✅ 견고 (미래구간서도 유지)" if wf["robust"] else "⚠️ 과최적화 (미래구간서 무너짐)"
    lines = [
        "═" * 56,
        f"  [3] 워크포워드 분석 — {verdict}",
        f"     {wf['n_folds']}fold  지표={wf['metric']}",
        f"     train평균 {wf['train_avg']}  →  test평균(진짜OOS) {wf['test_avg']}  WFE {wf['wfe']}",
        f"     test 양수 fold {wf['test_positive_folds']}",
    ]
    for f in wf["folds"]:
        lines.append(f"       fold{f['fold']} {f['params']}: train {f['train_value']} → test {f['test_value']}")
    lines.append("═" * 56)
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────
# 21. 파라미터 안정성 테스트 (●●●)
# ─────────────────────────────────────────────────────────────────────

def parameter_stability_test(run_fn, param_grid: dict, base_params: dict,
                             metric: str = "sharpe") -> dict:
    """
    파라미터 안정성 검증 (21).

    파라미터를 격자(grid)로 바꿔가며 백테스트하고, 성과가 특정 값에서만
    튀는 '뾰족한 봉우리'인지, 넓은 영역에서 고른 '고원(plateau)'인지 본다.
    뾰족하면 과최적화 위험 — 실전에서 파라미터가 조금만 어긋나도 무너진다.

    Args:
        run_fn:      run_fn(params: dict) -> dict (metric 키 포함)
        param_grid:  {"short_window": [5,10,15], "long_window": [20,30,40]} 형태
        base_params: 현재 사용 중인 파라미터 (봉우리 위치 판정 기준)
        metric:      평가 지표 키 (예: "sharpe", "total_return_pct")

    Returns:
        격자 통계 + 안정성 판정.
    """
    keys = list(param_grid.keys())
    combos = list(itertools.product(*[param_grid[k] for k in keys]))

    results = []
    for combo in combos:
        params = dict(zip(keys, combo))
        try:
            m = run_fn(params).get(metric)
        except Exception:
            m = None
        if m is not None and np.isfinite(m):
            results.append((params, float(m)))

    if len(results) < 3:
        return {"error": "유효한 격자 결과 부족", "n": len(results)}

    vals = np.array([m for _, m in results])
    mean, std = vals.mean(), vals.std()
    best_i = int(vals.argmax())
    best_params, best_val = results[best_i]

    # 현재 파라미터의 성과
    base_val = None
    for p, m in results:
        if all(p.get(k) == base_params.get(k) for k in keys):
            base_val = m
            break

    # 고원 비율: 최고값의 50% 이상을 내는 격자점 비율 (양수 지표 가정)
    if best_val > 0:
        plateau_ratio = float((vals >= best_val * 0.5).mean())
    else:
        plateau_ratio = float((vals >= best_val).mean())

    # 변동계수(CV): 격자 전반의 흩어짐. 낮을수록 안정적
    cv = float(std / abs(mean)) if mean != 0 else float("inf")

    # 봉우리 뾰족함: best가 나머지 평균보다 얼마나 튀는가
    others = np.delete(vals, best_i)
    peak_sharpness = float((best_val - others.mean()) / abs(others.mean())) \
        if len(others) > 0 and others.mean() != 0 else 0.0

    # 판정: 고원 넓고(≥0.6) 변동 작으면(CV<0.5) 안정
    stable = plateau_ratio >= 0.6 and cv < 0.5 and best_val > 0
    return {
        "metric": metric,
        "grid_points": len(results),
        "best_params": best_params,
        "best_value": round(best_val, 3),
        "base_value": round(base_val, 3) if base_val is not None else None,
        "mean": round(mean, 3),
        "std": round(std, 3),
        "cv": round(cv, 3),
        "plateau_ratio": round(plateau_ratio, 3),
        "peak_sharpness": round(peak_sharpness, 3),
        "stable": stable,
        "all_results": [(p, round(m, 3)) for p, m in results],
    }


# ─────────────────────────────────────────────────────────────────────
# 2. 과최적화 검증 (●●) — In-Sample / Out-of-Sample 성과 격차
# ─────────────────────────────────────────────────────────────────────

def overfitting_test(run_fn, param_grid: dict, is_period: tuple,
                     oos_period: tuple, metric: str = "total_return_pct") -> dict:
    """
    과최적화 검증 (2). 항목 4(Out-of-Sample)도 함께 검증한다.

    전반부(In-Sample)에서 격자 최적 파라미터를 고르고, 그 파라미터를
    후반부(Out-of-Sample)에 적용해 성과가 얼마나 무너지는지 본다.
    IS에서만 좋고 OOS에서 무너지면 과최적화.

    Args:
        run_fn:      run_fn(params, start, end) -> dict (metric 키 포함)
        param_grid:  탐색할 파라미터 격자
        is_period:   (start, end) 인샘플 기간
        oos_period:  (start, end) 아웃오브샘플 기간
    """
    keys = list(param_grid.keys())
    combos = list(itertools.product(*[param_grid[k] for k in keys]))

    # 1) In-Sample 최적화
    is_results = []
    for combo in combos:
        params = dict(zip(keys, combo))
        try:
            m = run_fn(params, is_period[0], is_period[1]).get(metric)
        except Exception:
            m = None
        if m is not None and np.isfinite(m):
            is_results.append((params, float(m)))

    if not is_results:
        return {"error": "In-Sample 결과 없음"}

    best_params, is_best = max(is_results, key=lambda x: x[1])

    # 2) 그 파라미터로 Out-of-Sample 평가
    try:
        oos_best = float(run_fn(best_params, oos_period[0], oos_period[1]).get(metric))
    except Exception:
        oos_best = 0.0

    # 3) OOS에서의 진짜 최적도 구해 '파라미터 일치' 여부 확인
    oos_results = []
    for combo in combos:
        params = dict(zip(keys, combo))
        try:
            m = run_fn(params, oos_period[0], oos_period[1]).get(metric)
        except Exception:
            m = None
        if m is not None and np.isfinite(m):
            oos_results.append((params, float(m)))
    oos_true_best_params, oos_true_best = max(oos_results, key=lambda x: x[1]) \
        if oos_results else (None, 0.0)

    # 성과 저하율: 1 - OOS/IS (IS가 양수일 때)
    if is_best > 0:
        degradation = 1 - (oos_best / is_best)
    else:
        degradation = 0.0

    params_match = (best_params == oos_true_best_params)
    # 판정: OOS가 IS의 절반 이상 유지하고 OOS도 양수면 통과
    overfit = (degradation > 0.5) or (oos_best <= 0 < is_best)
    return {
        "metric": metric,
        "is_best_params": best_params,
        "is_best_value": round(is_best, 3),
        "oos_value_same_params": round(oos_best, 3),
        "oos_true_best_params": oos_true_best_params,
        "oos_true_best_value": round(oos_true_best, 3),
        "degradation_pct": round(degradation * 100, 1),
        "params_match": params_match,
        "overfit": overfit,
    }


# ─────────────────────────────────────────────────────────────────────
# 24. Time Delay Test (●●)
# ─────────────────────────────────────────────────────────────────────

def time_delay_test(run_fn, delays=(0, 1, 2, 3),
                    metric: str = "total_return_pct") -> dict:
    """
    Time Delay Test (24).

    신호 발생 후 N봉 지연 실행했을 때 성과가 얼마나 무너지는지 본다.
    같은 봉 즉시 체결이라는 비현실적 가정에 의존하면 1봉 지연만으로
    수익이 사라진다. 견고한 전략은 완만하게만 감소해야 한다.

    Args:
        run_fn: run_fn(delay: int) -> dict (metric 키 포함)
        delays: 시험할 지연 봉 수
    """
    results = {}
    for d in delays:
        try:
            results[d] = float(run_fn(d).get(metric))
        except Exception:
            results[d] = None

    base = results.get(0)
    degr = {}
    if base not in (None, 0):
        for d in delays:
            if d == 0 or results.get(d) is None:
                continue
            degr[d] = round((1 - results[d] / base) * 100, 1)

    # 판정: 1봉 지연 시 성과의 50% 이상 유지하면 견고
    delay1 = results.get(1)
    robust = (base is not None and base > 0 and delay1 is not None
              and delay1 >= base * 0.5)
    return {
        "metric": metric,
        "by_delay": {d: (round(v, 3) if v is not None else None)
                     for d, v in results.items()},
        "degradation_pct": degr,
        "robust": robust,
    }


# ─────────────────────────────────────────────────────────────────────
# 통합 포맷
# ─────────────────────────────────────────────────────────────────────

def format_advanced(stability=None, overfit=None, delay=None) -> str:
    lines = ["═" * 56]
    if stability and "error" not in stability:
        s = stability
        verdict = "✅ 안정 (고원형)" if s["stable"] else "⚠️ 불안정 (뾰족한 봉우리 — 과최적화 위험)"
        lines += [
            f"  [21] 파라미터 안정성 — {verdict}",
            f"     격자 {s['grid_points']}점  지표={s['metric']}",
            f"     최적 {s['best_params']} = {s['best_value']}  (현재값 {s['base_value']})",
            f"     평균 {s['mean']}  변동계수 {s['cv']}  고원비율 {s['plateau_ratio']*100:.0f}%  봉우리돌출 {s['peak_sharpness']}",
        ]
    if overfit and "error" not in overfit:
        o = overfit
        verdict = "⚠️ 과최적화 의심" if o["overfit"] else "✅ 통과 (OOS 견고)"
        lines += [
            f"  [2] 과최적화 / OOS — {verdict}",
            f"     IS 최적 {o['is_best_params']} = {o['is_best_value']}",
            f"     동일 파라미터 OOS = {o['oos_value_same_params']}  (저하율 {o['degradation_pct']}%)",
            f"     OOS 실제최적 = {o['oos_true_best_value']} {o['oos_true_best_params']}  파라미터일치 {o['params_match']}",
        ]
    if delay:
        d = delay
        verdict = "✅ 견고" if d["robust"] else "⚠️ 즉시체결 의존 (1봉 지연에 취약)"
        by = "  ".join(f"{k}봉:{v}" for k, v in d["by_delay"].items())
        lines += [
            f"  [24] Time Delay — {verdict}",
            f"     {d['metric']} → {by}",
            f"     지연 저하율 {d['degradation_pct']}",
        ]
    lines.append("═" * 56)
    return "\n".join(lines)

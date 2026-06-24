# run_advanced_validation.py — 고급 검증(21/2/24) 실행 데모
# 코인 선물 백테스터에 validation_advanced의 세 테스트를 연결한다.
# 데이터는 한 번만 받아 모든 재실행에서 재사용한다.

import logging, warnings, sys
logging.basicConfig(level=logging.ERROR)
warnings.filterwarnings("ignore")
from dotenv import load_dotenv; load_dotenv()
from datetime import datetime, timedelta

from data.fetcher import DataFetcher
from strategies.futures_strategy import FuturesStrategy
from trader.leverage_manager import LeverageManager
from backtester.futures_backtester import FuturesBacktester
import validation_advanced as va

SYMBOL = "BNB-USD"
END = datetime.now().strftime("%Y-%m-%d")
START = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")
# 지표 워밍업 버퍼 포함해 넉넉히 다운로드 (한 번만)
DL_START = (datetime.now() - timedelta(days=730 + 300)).strftime("%Y-%m-%d")

print(f"데이터 다운로드: {SYMBOL} {DL_START}~{END} (1회)")
FULL = DataFetcher().get_ohlcv(SYMBOL, DL_START, END)
print(f"  {len(FULL)}일치 로드 완료\n")

LM = LeverageManager()


def _bt(params=None, delay=0):
    return FuturesBacktester(FuturesStrategy(params), LM,
                             initial_capital=10000, taker_fee=0.0005,
                             signal_delay_bars=delay)


def _metrics(res):
    return {"sharpe": res.sharpe_ratio, "total_return_pct": res.total_return_pct,
            "max_drawdown_pct": res.max_drawdown_pct}


# ── 21. 파라미터 안정성 ───────────────────────────────────────────
def run_one_params(params):
    res = _bt(params).run(SYMBOL, START, END, raw_data=FULL)
    return _metrics(res)

grid = {"short_window": [5, 10, 15], "long_window": [20, 30, 45]}
base = {"short_window": 10, "long_window": 30}
print("[21] 파라미터 안정성 테스트 실행 중 (9개 격자)...")
stability = va.parameter_stability_test(run_one_params, grid, base, metric="total_return_pct")


# ── 2. 과최적화 / OOS ─────────────────────────────────────────────
# 전반부 IS / 후반부 OOS 로 분할
mid = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

def run_one_period(params, start, end):
    sub = FULL[FULL.index <= end]            # end까지 잘라 워밍업 확보
    res = _bt(params).run(SYMBOL, start, end, raw_data=sub)
    return _metrics(res)

print("[2] 과최적화/OOS 테스트 실행 중 (IS 9 + OOS 9 격자)...")
overfit = va.overfitting_test(run_one_period, grid,
                              is_period=(START, mid), oos_period=(mid, END),
                              metric="total_return_pct")


# ── 24. Time Delay ────────────────────────────────────────────────
def run_one_delay(delay):
    res = _bt(delay=delay).run(SYMBOL, START, END, raw_data=FULL)
    return _metrics(res)

print("[24] Time Delay 테스트 실행 중 (지연 0/1/2/3봉)...\n")
delay = va.time_delay_test(run_one_delay, delays=(0, 1, 2, 3), metric="total_return_pct")


print(va.format_advanced(stability=stability, overfit=overfit, delay=delay))

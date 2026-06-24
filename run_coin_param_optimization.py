# run_coin_param_optimization.py — 코인 글로벌 SMA 파라미터 재최적화 분석
#
# 코인 봇은 8종목 전체가 FuturesStrategy.DEFAULT_PARAMS(short10/long30) 하나를
# 공유한다. 이 글로벌 (short,long)이 8종목에 걸쳐 견고한지 IS/OOS로 검증하고,
# 종목 전반에서 min(IS,OOS)가 가장 높은 글로벌 파라미터를 추천한다.

import logging, warnings, itertools
logging.basicConfig(level=logging.ERROR); warnings.filterwarnings("ignore")
from dotenv import load_dotenv; load_dotenv()
from datetime import datetime, timedelta

from data.fetcher import DataFetcher
from strategies.futures_strategy import FuturesStrategy
from trader.leverage_manager import LeverageManager
from backtester.futures_backtester import FuturesBacktester

SYMBOLS = ["BTC-USD", "ETH-USD", "BNB-USD", "ATOM-USD", "DOGE-USD", "DOT-USD", "TRX-USD", "SOL-USD"]
END = datetime.now().strftime("%Y-%m-%d")
DL_START = (datetime.now() - timedelta(days=730 + 300)).strftime("%Y-%m-%d")
IS_START = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")
MID = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

SHORTS = [5, 10, 15]
LONGS = [20, 30, 40, 50]
GRID = [(s, l) for s, l in itertools.product(SHORTS, LONGS) if s < l]
LM = LeverageManager()

print(f"코인 글로벌 SMA 재최적화 (IS {IS_START}~{MID} / OOS {MID}~{END}, 격자 {len(GRID)})")
print("데이터 다운로드 중 (종목당 1회)...")
DATA = {}
for sym in SYMBOLS:
    DATA[sym] = DataFetcher().get_ohlcv(sym, DL_START, END)
print(f"  {len(DATA)}종목 로드 완료\n")


def bt_run(sym, s, l, start, end):
    sub = DATA[sym][DATA[sym].index <= end]
    strat = FuturesStrategy({"short_window": s, "long_window": l})
    res = FuturesBacktester(strat, LM, initial_capital=10000, taker_fee=0.0005)\
        .run(sym, start, end, raw_data=sub)
    return res.total_return_pct


# 격자별로 8종목 IS/OOS 평균 min 수익 집계
grid_scores = []
for s, l in GRID:
    mins = []
    for sym in SYMBOLS:
        try:
            is_r = bt_run(sym, s, l, IS_START, MID)
            oos_r = bt_run(sym, s, l, MID, END)
            mins.append(min(is_r, oos_r))
        except Exception:
            pass
    if mins:
        avg_min = sum(mins) / len(mins)
        positive = sum(1 for m in mins if m > 0)
        grid_scores.append({"params": (s, l), "avg_min": avg_min, "positive": positive, "n": len(mins)})

grid_scores.sort(key=lambda x: x["avg_min"], reverse=True)

print(f"{'SMA':>10}{'평균min(IS,OOS)%':>18}{'OOS+IS양수종목':>16}")
print("─" * 46)
for g in grid_scores:
    mark = "  ← 현재" if g["params"] == (10, 30) else ""
    print(f"{str(g['params']):>10}{g['avg_min']:>18.2f}{g['positive']:>10}/{g['n']}{mark}")

best = grid_scores[0]
cur = next((g for g in grid_scores if g["params"] == (10, 30)), None)
print(f"\n현재 글로벌: (10, 30)  평균min {cur['avg_min']:.2f}%  견고종목 {cur['positive']}/{cur['n']}")
print(f"추천 글로벌: {best['params']}  평균min {best['avg_min']:.2f}%  견고종목 {best['positive']}/{best['n']}")
print(f"{'→ 변경 권고' if best['params'] != (10,30) else '→ 현재값 유지 (이미 최적)'}")

# run_coin_per_symbol_opt.py — 코인 종목별 SMA 재최적화 + 유니버스 선별
#
# 8종목 각각에 대해 IS/OOS 격자분석으로 견고한 (short,long)을 찾고,
# IS·OOS 양쪽 수익>0인 종목만 유지(KEEP), 아니면 DROP 권고한다.
# 결과로 코인 봇에 넣을 종목별 파라미터 dict를 출력한다.

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

SHORTS = [5, 10, 15, 20]
LONGS = [20, 30, 40, 50, 60]
GRID = [(s, l) for s, l in itertools.product(SHORTS, LONGS) if s < l]
LM = LeverageManager()

print(f"코인 종목별 SMA 재최적화 (IS {IS_START}~{MID} / OOS {MID}~{END}, 격자 {len(GRID)})")
DATA = {sym: DataFetcher().get_ohlcv(sym, DL_START, END) for sym in SYMBOLS}
print(f"{len(DATA)}종목 로드 완료\n")


def bt_run(sym, s, l, start, end):
    sub = DATA[sym][DATA[sym].index <= end]
    strat = FuturesStrategy({"short_window": s, "long_window": l})
    return FuturesBacktester(strat, LM, initial_capital=10000, taker_fee=0.0005)\
        .run(sym, start, end, raw_data=sub)


print(f"{'종목':<10}{'추천SMA':>10}{'IS%':>9}{'OOS%':>9}{'샤프':>7}  판정")
print("─" * 56)
keep, drop = {}, []
for sym in SYMBOLS:
    rows = []
    for s, l in GRID:
        try:
            ir = bt_run(sym, s, l, IS_START, MID).total_return_pct
            ores = bt_run(sym, s, l, MID, END)
            rows.append({"sl": (s, l), "is": ir, "oos": ores.total_return_pct,
                         "sharpe": ores.sharpe_ratio, "min": min(ir, ores.total_return_pct)})
        except Exception:
            pass
    robust = [r for r in rows if r["is"] > 0 and r["oos"] > 0]
    if robust:
        best = max(robust, key=lambda r: r["min"])
        keep[sym] = best["sl"]
        verdict = "KEEP"
    else:
        best = max(rows, key=lambda r: r["oos"]) if rows else {"sl": (10, 40), "is": 0, "oos": 0, "sharpe": 0}
        drop.append(sym)
        verdict = "DROP"
    print(f"{sym:<10}{str(best['sl']):>10}{best['is']:>9.1f}{best['oos']:>9.1f}{best['sharpe']:>7.2f}  {verdict}")

print("\n── 코인 종목별 파라미터 (KEEP) ──")
for sym, p in keep.items():
    print(f'    "{sym}": {{"short_window": {p[0]}, "long_window": {p[1]}}},')
print(f"\n유지 {len(keep)}종목 / DROP {len(drop)}종목: {drop}")

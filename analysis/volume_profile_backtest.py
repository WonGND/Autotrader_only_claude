# -*- coding: utf-8 -*-
"""
매물대(Volume Profile) 진입 필터 백테스트

가설: "POC(Point of Control, 최대 거래량 가격대) 대비 종가 위치"를 진입 필터로 추가하면
      (롱은 POC 위에서만, 숏은 POC 아래에서만 진입) 승률·손익비가 개선될까?

방법:
  1. 기존 FuturesStrategy 신호 생성 (MA/RSI/BB 복합)
  2. 직전 N일 일봉 거래량으로 매물대(Volume Profile)를 구성해 POC 계산 (lookahead 방지: 과거 데이터만 사용)
  3. "POC 필터" 통과 신호만 남긴 변형 전략을 만들어 동일한 FuturesBacktester로 비교
  4. 원본 vs 필터 적용 결과(승률/손익비/거래수)를 종목별·종합으로 비교

실행 방법:
  python -X utf8 -u analysis/volume_profile_backtest.py
"""

import os
import sys
import json
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# SSL 인증서 경로 수정 (Windows 한글 사용자명 우회) — yfinance 사용 전 반드시 import
from data.fetcher import DataFetcher

from strategies.futures_strategy import FuturesStrategy
from trader.leverage_manager import LeverageManager
from backtester.futures_backtester import FuturesBacktester
from utils.logger import get_logger

logger = get_logger(__name__)

SYMBOLS = [
    "BTC-USD", "ETH-USD", "BNB-USD", "ATOM-USD",
    "DOGE-USD", "DOT-USD", "TRX-USD", "SOL-USD",
]

START_DATE = "2023-01-01"
END_DATE   = pd.Timestamp.now().strftime("%Y-%m-%d")

VP_LOOKBACK = 30   # 매물대 계산에 사용할 과거 일수
VP_BINS     = 24   # 가격 구간 분할 개수

INITIAL_CAPITAL = 10_000_000
RESULT_DIR = os.path.join(os.path.dirname(__file__), "volume_profile_results")


# ─────────────────────────────────────────────────────────────────
# 매물대(Volume Profile) POC 계산
# ─────────────────────────────────────────────────────────────────

def compute_poc_series(df: pd.DataFrame, lookback: int = VP_LOOKBACK, n_bins: int = VP_BINS) -> pd.Series:
    """
    각 날짜 시점에서 '직전 lookback일' 데이터만으로 POC(최대 거래량 가격대)를 계산.
    당일 데이터는 사용하지 않아 lookahead bias를 방지한다.
    """
    typical = (df["High"] + df["Low"] + df["Close"]) / 3.0
    volume  = df["Volume"].values
    typical_v = typical.values

    poc = np.full(len(df), np.nan)

    for i in range(lookback, len(df)):
        w_typical = typical_v[i - lookback:i]
        w_volume  = volume[i - lookback:i]

        lo, hi = w_typical.min(), w_typical.max()
        if hi <= lo or np.isnan(lo) or np.isnan(hi):
            continue

        bins = np.linspace(lo, hi, n_bins + 1)
        bin_idx = np.clip(np.digitize(w_typical, bins) - 1, 0, n_bins - 1)

        vol_per_bin = np.zeros(n_bins)
        for b, v in zip(bin_idx, w_volume):
            vol_per_bin[b] += v

        poc_bin = int(np.argmax(vol_per_bin))
        poc[i] = (bins[poc_bin] + bins[poc_bin + 1]) / 2.0

    return pd.Series(poc, index=df.index, name="poc")


class POCFilteredStrategy(FuturesStrategy):
    """
    기존 전략 신호 중 'POC 대비 위치' 조건을 통과한 신호만 남기는 래퍼 전략.
      - 롱 신호: 종가가 POC 위에 있을 때만 유지 (매물대 돌파 후 지지 전환 가정)
      - 숏 신호: 종가가 POC 아래에 있을 때만 유지 (매물대 이탈 후 저항 전환 가정)
    FuturesBacktester는 strategy.generate_signals() 결과만 사용하므로,
    이 래퍼만 교체하면 기존 백테스터를 그대로 재사용해 공정 비교가 가능하다.
    """

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        df = super().generate_signals(data)
        poc = compute_poc_series(df)
        df["poc"] = poc

        long_ok  = (df["signal"] == 1)  & (df["Close"] > df["poc"])
        short_ok = (df["signal"] == -1) & (df["Close"] < df["poc"])
        keep = long_ok | short_ok | (df["signal"] == 0) | df["poc"].isna()

        # POC 데이터가 없는 구간(lookback 부족)은 원본 신호 유지 (공정성을 위해 걸러내지 않음)
        drop_mask = (~keep) & df["poc"].notna()
        df.loc[drop_mask, "signal"] = 0
        df.loc[drop_mask, "signal_strength"] = 0
        df.loc[drop_mask, "entry_basis"] = ""
        return df


# ─────────────────────────────────────────────────────────────────
# 백테스트 실행 및 비교
# ─────────────────────────────────────────────────────────────────

def run_pair(symbol: str) -> dict:
    lm = LeverageManager()

    base_bt = FuturesBacktester(
        strategy=FuturesStrategy(),
        leverage_manager=lm,
        initial_capital=INITIAL_CAPITAL,
        risk_per_trade_pct=0.02,
    )
    filt_bt = FuturesBacktester(
        strategy=POCFilteredStrategy(),
        leverage_manager=lm,
        initial_capital=INITIAL_CAPITAL,
        risk_per_trade_pct=0.02,
    )

    base = base_bt.run(symbol, START_DATE, END_DATE)
    filt = filt_bt.run(symbol, START_DATE, END_DATE)

    def summarize(res):
        trades = res.trades_log
        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]
        gross_profit = sum(t["pnl"] for t in wins)
        gross_loss   = -sum(t["pnl"] for t in losses)
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0
        avg_pnl_pct = (sum(t["pnl_pct"] for t in trades) / len(trades)) if trades else 0.0
        return {
            "trades": res.total_trades,
            "win_rate": res.win_rate * 100,  # FuturesBacktestResult.win_rate는 0~1 비율값
            "avg_pnl_pct": avg_pnl_pct,
            "profit_factor": profit_factor,
            "total_return_pct": res.total_return_pct,
            "max_drawdown_pct": res.max_drawdown_pct,
            "liquidations": res.liquidations,
        }

    return {"symbol": symbol, "baseline": summarize(base), "filtered": summarize(filt)}


def main():
    os.makedirs(RESULT_DIR, exist_ok=True)

    print("=" * 76)
    print("  매물대(Volume Profile) POC 진입 필터 백테스트")
    print(f"  기간: {START_DATE} ~ {END_DATE}  |  매물대 lookback: {VP_LOOKBACK}일 / {VP_BINS}구간")
    print(f"  종목: {len(SYMBOLS)}개")
    print("=" * 76)

    all_results = []
    for symbol in SYMBOLS:
        print(f"\n  [{symbol}] 백테스트 중...", end="", flush=True)
        try:
            r = run_pair(symbol)
            all_results.append(r)
            b, f = r["baseline"], r["filtered"]
            print(
                f"\n    원본    : 거래 {b['trades']:3d}건 | 승률 {b['win_rate']:5.1f}% | "
                f"평균PnL {b['avg_pnl_pct']:+6.2f}% | PF {b['profit_factor']:.2f} | "
                f"누적수익 {b['total_return_pct']:+7.2f}%"
            )
            print(
                f"    필터적용: 거래 {f['trades']:3d}건 | 승률 {f['win_rate']:5.1f}% | "
                f"평균PnL {f['avg_pnl_pct']:+6.2f}% | PF {f['profit_factor']:.2f} | "
                f"누적수익 {f['total_return_pct']:+7.2f}%"
            )
        except Exception as e:
            print(f"  실패: {e}")
            logger.error(f"{symbol} 백테스트 실패: {e}", exc_info=True)

    # ── 종합 집계 ─────────────────────────────────────────────────
    print("\n" + "=" * 76)
    print("  [종합 비교]")
    print("=" * 76)

    def agg(key_results, field):
        vals = [r[key_results][field] for r in all_results if r[key_results]["trades"] > 0]
        return (sum(vals) / len(vals)) if vals else 0.0

    n_base_trades = sum(r["baseline"]["trades"] for r in all_results)
    n_filt_trades = sum(r["filtered"]["trades"] for r in all_results)

    print(f"  {'지표':<14} {'원본':>14} {'필터 적용':>14}")
    print(f"  {'-'*14} {'-'*14} {'-'*14}")
    print(f"  {'총 거래수':<14} {n_base_trades:>14d} {n_filt_trades:>14d}")
    print(f"  {'평균 승률':<14} {agg('baseline','win_rate'):>13.1f}% {agg('filtered','win_rate'):>13.1f}%")
    print(f"  {'평균 PnL/거래':<14} {agg('baseline','avg_pnl_pct'):>+13.2f}% {agg('filtered','avg_pnl_pct'):>+13.2f}%")
    print(f"  {'평균 손익비(PF)':<14} {agg('baseline','profit_factor'):>14.2f} {agg('filtered','profit_factor'):>14.2f}")
    print(f"  {'평균 누적수익':<14} {agg('baseline','total_return_pct'):>+13.2f}% {agg('filtered','total_return_pct'):>+13.2f}%")
    print(f"  {'평균 최대낙폭':<14} {agg('baseline','max_drawdown_pct'):>13.2f}% {agg('filtered','max_drawdown_pct'):>13.2f}%")

    out_path = os.path.join(RESULT_DIR, "comparison.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n  결과 저장: {out_path}")
    print("=" * 76)


if __name__ == "__main__":
    main()

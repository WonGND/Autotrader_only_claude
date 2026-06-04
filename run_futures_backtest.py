"""
선물 거래 백테스트 실행 스크립트

10개 암호화폐 종목에 대해 다양한 레버리지 모드를 비교합니다:
  - 1x (레버리지 없음)
  - 3x (고정)
  - 5x (고정)
  - 10x (고정)
  - 동적 (ATR + 신호강도 기반 자동 조정)

실행 방법:
  python run_futures_backtest.py
  python run_futures_backtest.py --start 2023-01-01 --end 2024-01-01
  python run_futures_backtest.py --symbol BTC-USD --start 2023-01-01
"""

import sys
import os
import argparse
from datetime import datetime, timedelta
from typing import List, Optional

# 프로젝트 루트를 Python 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategies.futures_strategy import FuturesStrategy
from trader.leverage_manager import LeverageManager
from backtester.futures_backtester import FuturesBacktester, FuturesBacktestResult
from utils.logger import get_logger

logger = get_logger(__name__)


# ─── 설정 ────────────────────────────────────────────────────────

DEFAULT_SYMBOLS = [
    "BTC-USD",
    "ETH-USD",
    "SOL-USD",
    "BNB-USD",
    "XRP-USD",
    "DOGE-USD",
    "ADA-USD",
    "AVAX-USD",
    "DOT-USD",
    "MATIC-USD",
]

LEVERAGE_MODES = [
    ("1x", 1),
    ("3x", 3),
    ("5x", 5),
    ("10x", 10),
    ("동적", None),  # None = 동적 레버리지
]

INITIAL_CAPITAL = 10_000_000  # USD (백테스트 기준금액)


# ─── 백테스트 실행 ────────────────────────────────────────────────

def run_single_backtest(
    symbol: str,
    start_date: str,
    end_date: str,
    fixed_leverage: Optional[int],
) -> FuturesBacktestResult:
    """단일 종목·레버리지 백테스트 실행"""
    strategy = FuturesStrategy()
    leverage_manager = LeverageManager()

    backtester = FuturesBacktester(
        strategy=strategy,
        leverage_manager=leverage_manager,
        initial_capital=INITIAL_CAPITAL,
        maker_fee=0.0002,
        taker_fee=0.0005,
        funding_rate_8h=0.0001,
        fixed_leverage=fixed_leverage,
        risk_per_trade_pct=0.02,
    )

    return backtester.run(symbol, start_date, end_date)


def run_all_backtests(
    symbols: List[str],
    start_date: str,
    end_date: str,
) -> List[FuturesBacktestResult]:
    """모든 종목 × 레버리지 조합 백테스트"""
    results = []
    total = len(symbols) * len(LEVERAGE_MODES)
    done = 0

    for symbol in symbols:
        for label, leverage in LEVERAGE_MODES:
            done += 1
            print(f"  [{done:2d}/{total}] {symbol} {label:5s} 백테스트 중...", end="", flush=True)
            try:
                result = run_single_backtest(symbol, start_date, end_date, leverage)
                result.leverage_mode = label
                results.append(result)
                print(
                    f" 완료 → 수익={result.total_return_pct:+.1f}%  "
                    f"MDD={result.max_drawdown_pct:.1f}%  "
                    f"청산={result.liquidations}"
                )
            except Exception as e:
                print(f" 실패: {e}")
                logger.error(f"{symbol} {label} 백테스트 실패: {e}", exc_info=True)

    return results


# ─── 결과 출력 ────────────────────────────────────────────────────

def print_results_table(results: List[FuturesBacktestResult]) -> None:
    """결과를 표 형식으로 출력"""
    if not results:
        print("백테스트 결과가 없습니다.")
        return

    # 헤더
    print()
    print("=" * 90)
    print(
        f"{'심볼':<12} | {'레버리지':^8} | {'수익률':>8} | {'MDD':>8} | "
        f"{'샤프':>6} | {'청산':>4} | {'평균레버':>7} | {'승률':>6} | {'거래수':>5}"
    )
    print("-" * 90)

    # 이전 심볼 추적 (구분선용)
    prev_symbol = None

    for r in results:
        if prev_symbol and r.symbol != prev_symbol:
            print("-" * 90)
        prev_symbol = r.symbol

        # 동적 레버리지는 ★ 표시
        is_dynamic = r.leverage_mode == "동적"
        marker = " ★" if is_dynamic else "  "

        # 수익률 색상 (터미널 ANSI)
        ret_str = f"{r.total_return_pct:+.2f}%"
        mdd_str = f"{r.max_drawdown_pct:.2f}%"

        print(
            f"{r.symbol:<12} | {r.leverage_mode:^8} | {ret_str:>8} | {mdd_str:>8} | "
            f"{r.sharpe_ratio:>6.2f} | {r.liquidations:>4} | {r.avg_leverage:>7.1f} | "
            f"{r.win_rate*100:>5.1f}% | {r.total_trades:>5}{marker}"
        )

    print("=" * 90)
    print("  ★ = 동적 레버리지 (ATR + 신호강도 기반 자동 조정)")
    print()


def print_summary(results: List[FuturesBacktestResult]) -> None:
    """레버리지 모드별 평균 성과 요약"""
    if not results:
        return

    print("── 레버리지 모드별 평균 성과 ─────────────────────────────────")
    print(f"{'모드':^8} | {'평균수익률':>10} | {'평균MDD':>8} | {'평균샤프':>8} | {'총청산수':>8}")
    print("-" * 55)

    for label, _ in LEVERAGE_MODES:
        mode_results = [r for r in results if r.leverage_mode == label]
        if not mode_results:
            continue
        avg_ret = sum(r.total_return_pct for r in mode_results) / len(mode_results)
        avg_mdd = sum(r.max_drawdown_pct for r in mode_results) / len(mode_results)
        avg_sharpe = sum(r.sharpe_ratio for r in mode_results) / len(mode_results)
        total_liq = sum(r.liquidations for r in mode_results)
        print(
            f"{label:^8} | {avg_ret:>+9.2f}% | {avg_mdd:>7.2f}% | "
            f"{avg_sharpe:>8.2f} | {total_liq:>8}"
        )

    print()

    # 리스크 조정 수익 기준 최고 모드
    best = max(results, key=lambda r: r.sharpe_ratio if r.liquidations == 0 else r.sharpe_ratio - r.liquidations * 0.5)
    print(f"  리스크 조정 수익 최고: {best.symbol} {best.leverage_mode} "
          f"(샤프={best.sharpe_ratio:.2f}, 수익={best.total_return_pct:+.2f}%)")
    print()


def export_csv(results: List[FuturesBacktestResult], filepath: str) -> None:
    """결과를 CSV로 저장"""
    try:
        import csv
        with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
            fieldnames = [
                "symbol", "leverage_mode", "start_date", "end_date",
                "initial_capital", "final_capital", "total_return_pct",
                "max_drawdown_pct", "sharpe_ratio", "win_rate",
                "total_trades", "long_trades", "short_trades",
                "liquidations", "avg_leverage", "avg_hold_hours",
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in results:
                writer.writerow({k: getattr(r, k, "") for k in fieldnames})
        print(f"  CSV 저장 완료: {filepath}")
    except Exception as e:
        print(f"  CSV 저장 실패: {e}")


# ─── 메인 ────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="선물 거래 레버리지 비교 백테스트",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python run_futures_backtest.py
  python run_futures_backtest.py --start 2023-01-01 --end 2024-01-01
  python run_futures_backtest.py --symbol BTC-USD ETH-USD
  python run_futures_backtest.py --symbol BTC-USD --start 2022-01-01 --csv results.csv
        """,
    )
    parser.add_argument(
        "--symbol", nargs="+", default=None,
        help="백테스트할 종목 코드 (기본값: 10개 전체)",
    )
    parser.add_argument(
        "--start",
        default=(datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d"),
        help="시작일 YYYY-MM-DD (기본값: 1년 전)",
    )
    parser.add_argument(
        "--end",
        default=datetime.now().strftime("%Y-%m-%d"),
        help="종료일 YYYY-MM-DD (기본값: 오늘)",
    )
    parser.add_argument(
        "--capital", type=float, default=INITIAL_CAPITAL,
        help=f"초기 자본금 (기본값: {INITIAL_CAPITAL:,.0f})",
    )
    parser.add_argument(
        "--csv", default=None,
        help="결과를 CSV 파일로 저장 (선택 사항)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    symbols = args.symbol if args.symbol else DEFAULT_SYMBOLS
    start_date = args.start
    end_date = args.end

    print()
    print("┌─────────────────────────────────────────────────────────────┐")
    print("│         선물 거래 레버리지 비교 백테스트 시스템              │")
    print("└─────────────────────────────────────────────────────────────┘")
    print(f"  기간: {start_date} ~ {end_date}")
    print(f"  종목: {', '.join(symbols)}")
    print(f"  초기 자본: ${args.capital:,.0f}")
    print(f"  레버리지 모드: {', '.join(label for label, _ in LEVERAGE_MODES)}")
    print()

    # 백테스트 실행
    print("백테스트 실행 중...")
    results = run_all_backtests(symbols, start_date, end_date)

    # 결과 출력
    print_results_table(results)
    print_summary(results)

    # CSV 저장
    if args.csv:
        export_csv(results, args.csv)
    else:
        # 기본 CSV 저장
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_csv = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            f"futures_backtest_{ts}.csv"
        )
        export_csv(results, default_csv)

    return results


if __name__ == "__main__":
    main()

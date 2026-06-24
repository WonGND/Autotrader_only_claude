"""
소액 실거래 테스트 스크립트

8~10 USDT 소액으로 실제 거래가 정상 동작하는지 검증합니다.
신호가 있으면 DOGE/TRX/XRP 같은 소액 친화 종목부터 시도합니다.
"""

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from broker.bybit_futures_broker import BybitFuturesBroker, LOT_SIZE
from strategies.futures_strategy import FuturesStrategy
from trader.leverage_manager import LeverageManager
from data.fetcher import DataFetcher
from utils.logger import get_logger

logger = get_logger(__name__)

# 소액 테스트용 설정 (순서 = 우선순위, 단가 낮은 것부터)
TEST_SYMBOLS = [
    "DOGE-USD",   # ~$0.08 / 1 DOGE lot
    "TRX-USD",    # ~$0.07 / 1 TRX lot
    "XRP-USD",    # ~$0.50 / 1 XRP lot
    "ADA-USD",    # ~$0.30 / 1 ADA lot
    "MATIC-USD",  # ~$0.40 / 1 MATIC lot
    "DOT-USD",    # ~$4.00 / 0.1 DOT lot
    "SOL-USD",    # ~$61  / 0.1 SOL lot
    "ETH-USD",    # ~$1556 / 0.01 ETH lot
    "BTC-USD",    # ~$60k  / 0.001 BTC lot
]

LEVERAGE    = 5       # 고정 5배
USE_RATIO   = 0.50    # 잔고의 50% 사용 (최소 주문 충족용)


def check_signal(symbol: str, fetcher: DataFetcher, strategy: FuturesStrategy):
    """전략 신호 확인"""
    end   = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d")
    data  = fetcher.get_ohlcv(symbol, start, end)

    if data.empty or len(data) < strategy.get_required_history():
        return 0, 0.0, 0.0, 0.0

    signals = strategy.generate_signals(data)
    latest  = signals.iloc[-1]
    signal  = int(latest.get("signal", 0))
    atr     = float(latest.get("atr", 0))
    close   = float(latest.get("Close", 0))
    strength = int(latest.get("signal_strength", 1))
    return signal, atr, close, strength


def main():
    print("\n" + "="*60)
    print("  소액 실거래 테스트")
    print("="*60)

    broker   = BybitFuturesBroker()
    strategy = FuturesStrategy()
    lm       = LeverageManager()
    fetcher  = DataFetcher()

    # 잔고 확인
    balance = broker.get_balance()
    equity  = broker.get_total_equity()
    print(f"\n  잔고:   {balance:.4f} USDT")
    print(f"  총자산: {equity:.4f} USDT")

    if balance < 2.0:
        print("\n  잔고가 너무 낮아 테스트 불가 (최소 2 USDT 필요)")
        return

    margin_budget = balance * USE_RATIO
    print(f"  사용 예산: {margin_budget:.4f} USDT (잔고의 {USE_RATIO*100:.0f}%)")
    print(f"  레버리지:  {LEVERAGE}x")
    print(f"  최대 명목: {margin_budget * LEVERAGE:.4f} USDT\n")

    # 현재 포지션 확인
    open_positions = broker.get_positions()
    print(f"  현재 포지션: {len(open_positions)}개")
    for p in open_positions:
        pnl_sign = "+" if p.unrealized_pnl >= 0 else ""
        print(f"    {p.symbol} {p.side} {p.size} | PnL: {pnl_sign}{p.unrealized_pnl:.4f} USDT")

    print(f"\n{'─'*60}")
    print("  신호 스캔 중...")
    print(f"{'─'*60}")

    candidates = []

    for symbol in TEST_SYMBOLS:
        signal, atr, close, strength = check_signal(symbol, fetcher, strategy)
        direction = "롱" if signal == 1 else ("숏" if signal == -1 else "없음")

        # 최소 주문 가능 금액 계산
        bybit_sym = symbol.replace("-USD", "USDT")
        lot = LOT_SIZE.get(bybit_sym, 0.001)
        min_notional = lot * close

        notional = margin_budget * LEVERAGE
        qty_raw  = notional / close if close > 0 else 0
        import math
        qty      = math.floor(qty_raw / lot) * lot
        actual_notional = qty * close

        status = "✓" if signal != 0 else " "
        feasible = actual_notional >= min_notional and qty > 0

        print(f"  {status} {symbol:<12} 신호={direction:<4} "
              f"가격={close:>10.4f}  명목={actual_notional:>7.2f}USDT  "
              f"수량={qty} {'[가능]' if feasible and signal != 0 else ''}")

        if signal != 0 and feasible:
            candidates.append({
                "symbol": symbol,
                "signal": signal,
                "direction": direction,
                "price": close,
                "atr": atr,
                "strength": strength,
                "qty": qty,
                "notional": actual_notional,
                "margin": actual_notional / LEVERAGE,
            })

    if not candidates:
        print(f"\n  현재 진입 신호 없음 - 거래 없이 종료합니다.")
        print(f"  (다음 체크 시 신호 발생 시 자동 진입)")
        return

    # 가장 강한 신호 선택
    best = max(candidates, key=lambda x: x["strength"])

    print(f"\n{'─'*60}")
    print(f"  선택된 거래:")
    print(f"    종목:      {best['symbol']}")
    print(f"    방향:      {best['direction']}")
    print(f"    가격:      ${best['price']:.4f}")
    print(f"    수량:      {best['qty']}")
    print(f"    명목:      {best['notional']:.4f} USDT")
    print(f"    마진:      {best['margin']:.4f} USDT")
    print(f"    레버리지:  {LEVERAGE}x")
    print(f"    신호 강도: {best['strength']}")

    # SL / TP 계산
    price = best["price"]
    atr   = best["atr"] if best["atr"] > 0 else price * 0.02
    side_str = "long" if best["signal"] == 1 else "short"
    sl = lm.calculate_stop_loss(price, atr, side_str)
    tp = lm.calculate_take_profit(price, atr, side_str)
    print(f"    손절:      ${sl:.4f} ({abs(price-sl)/price*100:.2f}%)")
    print(f"    익절:      ${tp:.4f} ({abs(price-tp)/price*100:.2f}%)")
    print(f"{'─'*60}")

    # 실시간 가격으로 주문 실행
    live_price = broker.get_price(best["symbol"])
    print(f"\n  실시간 가격: ${live_price:.4f}")
    print(f"  주문 실행 중...")

    try:
        if best["signal"] == 1:
            order = broker.open_long(
                best["symbol"],
                best["notional"],
                LEVERAGE,
                sl,
                tp,
            )
        else:
            order = broker.open_short(
                best["symbol"],
                best["notional"],
                LEVERAGE,
                sl,
                tp,
            )

        print(f"\n  ✓ 주문 성공!")
        print(f"    Order ID:  {order.order_id}")
        print(f"    심볼:      {order.symbol}")
        print(f"    방향:      {order.side}")
        print(f"    수량:      {order.qty}")
        print(f"    체결가:    ${order.price:.4f}")

    except Exception as e:
        print(f"\n  ✗ 주문 실패: {e}")
        return

    # 잠시 후 포지션 확인
    import time
    print(f"\n  2초 후 포지션 확인...")
    time.sleep(2)

    positions = broker.get_positions()
    balance_after = broker.get_balance()

    print(f"\n  포지션 ({len(positions)}개):")
    for p in positions:
        pnl_sign = "+" if p.unrealized_pnl >= 0 else ""
        print(f"    {p.symbol} {p.side} x{p.leverage} | "
              f"수량={p.size} | 진입가=${p.avg_price:.4f} | "
              f"현재가=${p.mark_price:.4f} | "
              f"미실현PnL={pnl_sign}{p.unrealized_pnl:.4f} USDT")
        if p.stop_loss:
            print(f"      SL=${p.stop_loss:.4f} | TP=${p.take_profit:.4f}")

    print(f"\n  잔고 변화: {balance:.4f} → {balance_after:.4f} USDT")
    print(f"\n  테스트 완료! Bybit 앱에서도 포지션을 확인하세요.")
    print(f"  포지션 청산: Bybit 앱 또는 아래 명령어")
    print(f"  python -c \"from broker.bybit_futures_broker import BybitFuturesBroker; "
          f"from dotenv import load_dotenv; load_dotenv('.env'); "
          f"b=BybitFuturesBroker(); print(b.close_position('{best['symbol']}', '{side_str}'))\"")


if __name__ == "__main__":
    main()

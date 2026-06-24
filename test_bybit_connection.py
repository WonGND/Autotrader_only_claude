"""
Bybit 연결 테스트 스크립트

실계좌 연동 전에 이 스크립트를 먼저 실행해서 API 연결을 확인하세요.

실행:
    $env:BYBIT_API_KEY='your_key'
    $env:BYBIT_API_SECRET='your_secret'
    $env:BYBIT_TESTNET='true'    # 테스트넷 (기본값)
    python test_bybit_connection.py
"""

import os, sys
sys.path.insert(0, os.path.dirname(__file__))

# .env 파일 자동 로드
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

def main():
    api_key    = os.environ.get("BYBIT_API_KEY", "")
    api_secret = os.environ.get("BYBIT_API_SECRET", "")
    testnet    = os.environ.get("BYBIT_TESTNET", "true").lower() != "false"

    if not api_key or not api_secret:
        print("[오류] 환경변수를 먼저 설정하세요:")
        print("  $env:BYBIT_API_KEY='your_key'")
        print("  $env:BYBIT_API_SECRET='your_secret'")
        return

    mode = "테스트넷" if testnet else "실계좌"
    print(f"\nBybit 연결 테스트 [{mode}]")
    print("="*50)

    from broker.bybit_futures_broker import BybitFuturesBroker

    try:
        broker = BybitFuturesBroker(api_key, api_secret, testnet=testnet)

        # 1. 연결 확인
        status = broker.ping()
        print(f"  ✓ 연결 성공")
        print(f"  ✓ 잔고:   {status['balance_usdt']:.2f} USDT")
        print(f"  ✓ 총자산: {status['equity_usdt']:.2f} USDT")

        # 2. 시세 확인
        print("\n시세 조회:")
        for sym in ["BTC-USD", "ETH-USD", "BNB-USD"]:
            price = broker.get_price(sym)
            print(f"  {sym}: ${price:,.2f}")

        # 3. 현재 포지션 확인
        positions = broker.get_positions()
        print(f"\n현재 오픈 포지션: {len(positions)}개")
        for p in positions:
            print(f"  {p.symbol} {p.side} x{p.leverage} | "
                  f"수량={p.size} | 진입가={p.avg_price:.4f} | "
                  f"미실현={p.unrealized_pnl:.2f}USDT")

        print("\n✓ 모든 테스트 통과. 실거래 준비 완료!")
        print("\n실거래 시작:")
        print("  python run_live_trading.py")

    except Exception as e:
        print(f"\n✗ 연결 실패: {e}")
        print("\n확인 사항:")
        print("  1. API 키/시크릿이 올바른지 확인")
        print("  2. API 키에 Read + Trade 권한이 있는지 확인")
        print("  3. 테스트넷 키는 테스트넷에서만, 실계좌 키는 실계좌에서만 작동")
        print("  4. IP 제한 설정 시 현재 IP가 허용 목록에 있는지 확인")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
AutoTrader - 자동 주식 거래 시스템
CLI 진입점

사용법:
    python main.py web                          # 웹 대시보드 시작
    python main.py backtest --strategy ma_crossover --symbol 005930 --start 2023-01-01 --end 2023-12-31
    python main.py trade --mode paper           # 자동 매매 시작
    python main.py status                       # 현재 상태 확인
"""

import argparse
import sys
import os

# 프로젝트 루트를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.logger import get_logger
from utils.helpers import load_settings, load_strategies_config, get_trading_symbols
from database.models import init_db

logger = get_logger(__name__)


def cmd_web(args):
    """웹 대시보드 시작"""
    import uvicorn
    from web.app import app, set_trading_engine

    settings = load_settings()
    port = settings.get("app", {}).get("web_port", 8000)

    # 브로커 초기화
    broker = _init_broker(settings)

    # 전략 초기화
    strategy = _init_strategy(settings)

    # 포트폴리오 매니저 초기화
    from trader.portfolio import PortfolioManager
    trading_cfg = settings.get("trading", {})
    pm = PortfolioManager(
        broker=broker,
        stop_loss_pct=trading_cfg.get("stop_loss_pct", 0.05),
        take_profit_pct=trading_cfg.get("take_profit_pct", 0.15),
        position_size_pct=trading_cfg.get("position_size_pct", 0.2),
        max_positions=trading_cfg.get("max_positions", 5),
    )

    # 데이터 수집기 초기화
    from data.fetcher import DataFetcher
    fetcher = DataFetcher(provider=settings.get("data", {}).get("provider", "fdr"))

    # 트레이딩 엔진 초기화
    from trader.engine import TradingEngine
    strategies_cfg = load_strategies_config()
    active_strategy_key = strategies_cfg.get("active_strategy", "ma_crossover")
    watchlist = get_trading_symbols(active_strategy_key)

    engine = TradingEngine(
        broker=broker,
        strategy=strategy,
        portfolio_manager=pm,
        data_fetcher=fetcher,
        watchlist=watchlist,
    )

    set_trading_engine(engine, broker)

    logger.info(f"웹 대시보드 시작: http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


def cmd_backtest(args):
    """백테스트 실행"""
    from backtester.engine import BacktestEngine
    from backtester.report import BacktestReport
    from strategies import STRATEGY_MAP

    settings = load_settings()
    strategies_cfg = load_strategies_config()

    strategy_key = args.strategy
    if strategy_key not in STRATEGY_MAP:
        logger.error(f"알 수 없는 전략: {strategy_key}")
        logger.error(f"사용 가능한 전략: {', '.join(STRATEGY_MAP.keys())}")
        sys.exit(1)

    strategy_params = strategies_cfg.get("strategies", {}).get(strategy_key, {})
    strategy_cls = STRATEGY_MAP[strategy_key]
    strategy = strategy_cls(params=strategy_params)

    initial_capital = settings.get("trading", {}).get("initial_capital", 10_000_000)

    engine = BacktestEngine(
        strategy=strategy,
        initial_capital=initial_capital,
    )

    logger.info(f"백테스트 시작: {args.symbol} | {args.start} ~ {args.end} | {strategy.name}")
    result = engine.run(args.symbol, args.start, args.end)

    report = BacktestReport.format_report(result)
    print(report)


def cmd_trade(args):
    """자동 매매 시작"""
    import time
    import signal

    settings = load_settings()
    mode = args.mode or settings.get("app", {}).get("mode", "paper")

    logger.info(f"자동 매매 시작 (모드: {mode})")

    # 브로커 초기화
    broker = _init_broker(settings, force_mode=mode)

    # 전략 초기화
    strategy = _init_strategy(settings)

    # 포트폴리오 매니저
    from trader.portfolio import PortfolioManager
    trading_cfg = settings.get("trading", {})
    pm = PortfolioManager(
        broker=broker,
        stop_loss_pct=trading_cfg.get("stop_loss_pct", 0.05),
        take_profit_pct=trading_cfg.get("take_profit_pct", 0.15),
        position_size_pct=trading_cfg.get("position_size_pct", 0.2),
        max_positions=trading_cfg.get("max_positions", 5),
    )

    # 데이터 수집기
    from data.fetcher import DataFetcher
    fetcher = DataFetcher(provider=settings.get("data", {}).get("provider", "fdr"))

    # 트레이딩 엔진
    from trader.engine import TradingEngine
    strategies_cfg = load_strategies_config()
    active_strategy_key = strategies_cfg.get("active_strategy", "ma_crossover")
    watchlist = get_trading_symbols(active_strategy_key)

    engine = TradingEngine(
        broker=broker,
        strategy=strategy,
        portfolio_manager=pm,
        data_fetcher=fetcher,
        watchlist=watchlist,
    )

    # 종료 신호 처리
    def handle_stop(signum, frame):
        logger.info("종료 신호를 받았습니다. 트레이딩 엔진을 중지합니다...")
        engine.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    # 즉시 한 번 실행 후 스케줄 시작
    logger.info("첫 번째 거래 사이클을 즉시 실행합니다...")
    engine.run_once()
    engine.start_scheduled()

    logger.info(f"자동 매매 실행 중. Ctrl+C로 중지하세요.")
    while True:
        time.sleep(60)


def cmd_status(args):
    """현재 상태 확인"""
    from database.models import Trade, BacktestResult

    settings = load_settings()
    broker = _init_broker(settings)

    print("\n" + "=" * 60)
    print("  AutoTrader 상태")
    print("=" * 60)

    # 계좌 정보
    try:
        account = broker.get_account_info()
        print(f"\n  계좌 정보:")
        print(f"    계좌번호   : {account.account_number}")
        print(f"    총 평가금액 : ₩{account.total_value:,.0f}")
        print(f"    현금 잔고  : ₩{account.cash:,.0f}")
        print(f"    손익       : ₩{account.profit_loss:,.0f} ({account.profit_loss_pct:.2%})")
    except Exception as e:
        print(f"\n  계좌 정보 조회 실패: {e}")

    # 보유 종목
    try:
        positions = broker.get_positions()
        if positions:
            print(f"\n  보유 종목 ({len(positions)}개):")
            for sym, pos in positions.items():
                pct = pos.unrealized_pnl_pct * 100
                sign = "+" if pct >= 0 else ""
                print(f"    {sym}: {pos.quantity}주 | 평균 ₩{pos.avg_price:,.0f} | "
                      f"현재 ₩{pos.current_price:,.0f} | {sign}{pct:.2f}%")
        else:
            print(f"\n  보유 종목: 없음")
    except Exception as e:
        print(f"\n  포지션 조회 실패: {e}")

    # DB 통계
    try:
        session_obj = get_session()
        from database.models import get_session
        session_obj = get_session()
        total_trades = session_obj.query(Trade).count()
        total_backtests = session_obj.query(BacktestResult).count()
        session_obj.close()
        print(f"\n  통계:")
        print(f"    총 거래 수      : {total_trades}건")
        print(f"    백테스트 실행 수 : {total_backtests}건")
    except Exception:
        pass

    # 설정
    print(f"\n  현재 설정:")
    print(f"    모드           : {settings.get('app', {}).get('mode', 'paper')}")
    print(f"    웹 포트        : {settings.get('app', {}).get('web_port', 8000)}")
    print(f"    초기 자본금    : ₩{settings.get('trading', {}).get('initial_capital', 0):,.0f}")

    strategies_cfg = load_strategies_config()
    print(f"    활성 전략      : {strategies_cfg.get('active_strategy', '-')}")
    print("=" * 60)


def _init_broker(settings: dict, force_mode: str = None):
    """브로커 초기화"""
    mode = force_mode or settings.get("app", {}).get("mode", "paper")
    broker_type = settings.get("broker", {}).get("type", "paper")

    if mode == "paper" or broker_type == "paper":
        from broker.paper_broker import PaperBroker
        initial_capital = settings.get("trading", {}).get("initial_capital", 10_000_000)
        logger.info(f"모의 투자 브로커 초기화 (초기 자본: ₩{initial_capital:,.0f})")
        return PaperBroker(initial_capital=initial_capital)
    else:
        logger.error(
            f"실제 브로커({broker_type})가 설정되었지만 구현이 없습니다. "
            "broker/base.py를 참고하여 증권사 API를 구현하세요."
        )
        logger.info("모의 투자 브로커로 대체합니다.")
        from broker.paper_broker import PaperBroker
        return PaperBroker()


def _init_strategy(settings: dict):
    """전략 초기화"""
    from strategies import STRATEGY_MAP
    strategies_cfg = load_strategies_config()
    active_key = strategies_cfg.get("active_strategy", "ma_crossover")
    strategy_params = strategies_cfg.get("strategies", {}).get(active_key, {})

    if active_key not in STRATEGY_MAP:
        logger.warning(f"알 수 없는 전략 '{active_key}', 기본 전략(ma_crossover) 사용")
        active_key = "ma_crossover"
        strategy_params = strategies_cfg.get("strategies", {}).get(active_key, {})

    strategy_cls = STRATEGY_MAP[active_key]
    strategy = strategy_cls(params=strategy_params)
    logger.info(f"전략 초기화: {strategy.name}")
    return strategy


def main():
    parser = argparse.ArgumentParser(
        description="AutoTrader - 자동 주식 거래 시스템",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  python main.py web
  python main.py backtest --strategy ma_crossover --symbol 005930 --start 2023-01-01 --end 2023-12-31
  python main.py trade --mode paper
  python main.py status
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="실행할 명령")

    # web 명령
    web_parser = subparsers.add_parser("web", help="웹 대시보드 시작")

    # backtest 명령
    bt_parser = subparsers.add_parser("backtest", help="백테스트 실행")
    bt_parser.add_argument("--strategy", required=True,
                           choices=["ma_crossover", "rsi", "bollinger_bands"],
                           help="사용할 전략")
    bt_parser.add_argument("--symbol", required=True, help="종목 코드 (예: 005930)")
    bt_parser.add_argument("--start", required=True, help="시작일 (YYYY-MM-DD)")
    bt_parser.add_argument("--end", required=True, help="종료일 (YYYY-MM-DD)")

    # trade 명령
    trade_parser = subparsers.add_parser("trade", help="자동 매매 시작")
    trade_parser.add_argument("--mode", choices=["paper", "live"], default="paper",
                              help="거래 모드 (paper: 모의 투자, live: 실제 거래)")

    # status 명령
    status_parser = subparsers.add_parser("status", help="현재 상태 확인")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    # DB 초기화
    init_db()

    # 명령 실행
    if args.command == "web":
        cmd_web(args)
    elif args.command == "backtest":
        cmd_backtest(args)
    elif args.command == "trade":
        cmd_trade(args)
    elif args.command == "status":
        cmd_status(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

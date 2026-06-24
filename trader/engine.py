"""
자동 매매 엔진

APScheduler를 사용하여 주기적으로 시장을 체크하고 거래를 실행합니다.
"""

import threading
from datetime import datetime
from typing import List, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import pytz

from broker.base import BaseBroker
from strategies.base import BaseStrategy
from trader.portfolio import PortfolioManager
from data.fetcher import DataFetcher
from database.models import Trade as TradeModel, get_session
from utils.logger import get_logger
from utils.helpers import get_market_status, get_trading_symbols, load_settings
from strategies.performance_monitor import get_monitor
from strategies.macro_filter import MacroFilter
from data.macro_fetcher import MacroDataFetcher
from data.news_fetcher import NewsFetcher

logger = get_logger(__name__)

KST = pytz.timezone("Asia/Seoul")


class TradingEngine:
    """
    자동 매매 엔진

    Args:
        broker: 브로커 객체
        strategy: 전략 객체
        portfolio_manager: 포트폴리오 매니저
        data_fetcher: 데이터 수집기
        watchlist: 감시 종목 목록 (None이면 전략 설정에서 로드)
    """

    def __init__(
        self,
        broker: BaseBroker,
        strategy: BaseStrategy,
        portfolio_manager: PortfolioManager,
        data_fetcher: DataFetcher,
        watchlist: Optional[List[str]] = None,
    ):
        self.broker = broker
        self.strategy = strategy
        self.portfolio_manager = portfolio_manager
        self.data_fetcher = data_fetcher
        self.watchlist = watchlist or []
        self._scheduler: Optional[BackgroundScheduler] = None
        self._is_running = False
        self._lock = threading.Lock()
        self._cycle_count = 0
        self._last_run: Optional[datetime] = None
        self._errors: List[str] = []
        # 매크로/뉴스 모듈
        self._macro_fetcher = MacroDataFetcher()
        self._news_fetcher = NewsFetcher()
        self._macro_filter = MacroFilter()
        self._last_macro_state: dict = {}  # 웹 대시보드용

        # 설정 로드
        settings = load_settings()
        self._check_interval = settings.get("scheduler", {}).get("check_interval_minutes", 5)

    def run_once(self):
        """
        한 번의 거래 사이클을 실행합니다.

        1. 시장 상태 확인
        2. 각 종목에 대해 데이터 수집 및 신호 생성
        3. 기존 포지션에 대한 손절/익절 확인
        4. 새 신호에 따른 매수/매도 실행
        """
        with self._lock:
            self._last_run = datetime.now(KST)
            self._cycle_count += 1

        if not get_market_status():
            logger.debug("시장이 닫혀있습니다. 거래 사이클 건너뜀.")
            return

        logger.info(f"거래 사이클 #{self._cycle_count} 시작")

        watchlist = self.watchlist
        if not watchlist:
            # 전략 설정에서 watchlist 로드 시도
            strategy_key = self.strategy.name.split("(")[0].strip().lower().replace(" ", "_")
            watchlist = get_trading_symbols(strategy_key) or []

        if not watchlist:
            logger.warning("감시 종목이 없습니다.")
            return

        positions = self.broker.get_positions()

        for symbol in watchlist:
            try:
                self._process_symbol(symbol, positions)
            except Exception as e:
                err_msg = f"{symbol} 처리 중 오류: {e}"
                logger.error(err_msg)
                self._errors.append(err_msg)

        logger.info(f"거래 사이클 #{self._cycle_count} 완료")

    def _process_symbol(self, symbol: str, positions: dict):
        """개별 종목 처리"""
        from datetime import timedelta
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

        data = self.data_fetcher.get_ohlcv(symbol, start_date, end_date)
        if data.empty or len(data) < self.strategy.get_required_history():
            logger.warning(f"{symbol} 데이터 부족")
            return

        # 현재가 업데이트
        current_price = float(data["Close"].iloc[-1])

        # paper broker라면 가격 업데이트
        if hasattr(self.broker, "set_price"):
            self.broker.set_price(symbol, current_price)

        # 포지션 현재가 업데이트
        if symbol in positions:
            positions[symbol].current_price = current_price

        # 손절/익절 확인 (기존 포지션)
        if symbol in positions:
            pos = positions[symbol]
            pos.current_price = current_price

            if self.portfolio_manager.check_stop_loss(pos):
                logger.info(f"손절 실행: {symbol} @ ₩{current_price:,.0f}")
                order = self.broker.sell(symbol, pos.quantity)
                self._save_trade(order, self.strategy.name, "손절")
                return

            if self.portfolio_manager.check_take_profit(pos):
                logger.info(f"익절 실행: {symbol} @ ₩{current_price:,.0f}")
                order = self.broker.sell(symbol, pos.quantity)
                self._save_trade(order, self.strategy.name, "익절")
                return

        # 승률 기반 비활성화 전략 건너뜀
        monitor = get_monitor()
        if not monitor.is_enabled(self.strategy.name):
            logger.info(f"전략 비활성화 상태 - 신호 생성 건너뜀: {self.strategy.name}")
            return

        # 신호 생성
        data_with_signals = self.strategy.generate_signals(data)
        if data_with_signals.empty:
            return

        latest_signal = int(data_with_signals["signal"].iloc[-1])

        # ── 매크로 + 뉴스 감성 확인 (실거래 보조) ─────────────────
        macro_modifier = 1.0
        macro_reason = ""
        if latest_signal != 0:
            try:
                today = datetime.now().strftime("%Y-%m-%d")
                fg_df = self._macro_fetcher.get_fear_greed(today, today)
                vix_df = self._macro_fetcher.get_vix(today, today)
                fg_val  = float(fg_df["fg_value"].iloc[-1]) if not fg_df.empty else 50
                vix_val = float(vix_df["vix_close"].iloc[-1]) if not vix_df.empty else 20
                msig = self._macro_filter.evaluate(fg_val, vix_val, 0.0, today)
                macro_modifier = msig.position_modifier
                macro_reason = msig.reason
                self._last_macro_state = {
                    "fg_value": fg_val,
                    "fg_class": fg_df["fg_class"].iloc[-1] if not fg_df.empty else "N/A",
                    "vix": vix_val,
                    "macro_modifier": macro_modifier,
                    "reason": macro_reason,
                    "updated": today,
                }
                # 방향 차단 확인
                if latest_signal == 1  and not msig.allow_long:
                    logger.info(f"{symbol} 매크로 필터: 롱 차단 ({macro_reason})")
                    return
                if latest_signal == -1 and not msig.allow_short:
                    logger.info(f"{symbol} 매크로 필터: 숏 차단 ({macro_reason})")
                    return
                # 뉴스 감성 체크
                sentiment = self._news_fetcher.get_sentiment(symbol)
                news_score = sentiment.get("score", 0)
                # 강한 악재(-50 이하) 시 진입 보류
                if news_score <= -50:
                    logger.warning(
                        f"{symbol} 뉴스 악재로 진입 보류 (score={news_score}, {sentiment.get('reasoning','')})"
                    )
                    return
                # 뉴스 점수를 포지션 배율에 반영
                if news_score >= 50:
                    macro_modifier = min(1.3, macro_modifier * 1.1)
                elif news_score <= -20:
                    macro_modifier = max(0.4, macro_modifier * 0.8)
                logger.info(
                    f"{symbol} 매크로OK: F&G={fg_val:.0f}, VIX={vix_val:.1f}, "
                    f"뉴스={news_score}, 포지션배율={macro_modifier:.2f}"
                )
            except Exception as e:
                logger.debug(f"매크로/뉴스 확인 실패 ({e}) — 기본값 사용")

        if latest_signal == 1:
            if self.portfolio_manager.can_open_position(symbol):
                quantity = self.portfolio_manager.calculate_position_size(symbol, current_price)
                if quantity > 0:
                    cash = self.broker.get_balance()
                    required = quantity * current_price * 1.001
                    if cash >= required:
                        note = f"전략 신호 | {macro_reason}" if macro_reason else "전략 신호"
                        logger.info(f"매수 신호: {symbol} {quantity}주 @ ₩{current_price:,.0f} [{note}]")
                        order = self.broker.buy(symbol, quantity)
                        self._save_trade(order, self.strategy.name, note)

        elif latest_signal == -1:
            if symbol in positions:
                pos = positions[symbol]
                note = f"전략 신호 | {macro_reason}" if macro_reason else "전략 신호"
                logger.info(f"매도 신호: {symbol} {pos.quantity}주 @ ₩{current_price:,.0f} [{note}]")
                order = self.broker.sell(symbol, pos.quantity)
                self._save_trade(order, self.strategy.name, note)
                # 매도 완료 시 성과 기록 (pnl > 0이면 승)
                if hasattr(order, "pnl") and order.pnl is not None:
                    get_monitor().record_trade(self.strategy.name, order.pnl > 0)

    def _save_trade(self, order, strategy_name: str, note: str = ""):
        """거래 내역 DB 저장"""
        try:
            if order.status != "filled":
                return
            session = get_session()
            trade = TradeModel(
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                price=order.price,
                filled_price=order.filled_price,
                strategy=strategy_name,
                note=f"{note} | {order.message}",
            )
            session.add(trade)
            session.commit()
            session.close()
        except Exception as e:
            logger.error(f"거래 저장 실패: {e}")

    def start_scheduled(self):
        """
        스케줄러를 시작하여 주기적으로 거래 사이클을 실행합니다.
        """
        if self._is_running:
            logger.warning("트레이딩 엔진이 이미 실행 중입니다.")
            return

        self._scheduler = BackgroundScheduler(timezone=KST)
        self._scheduler.add_job(
            func=self.run_once,
            trigger=IntervalTrigger(minutes=self._check_interval),
            id="trading_cycle",
            name="거래 사이클",
            replace_existing=True,
        )
        self._scheduler.start()
        self._is_running = True
        logger.info(f"트레이딩 엔진 시작. 실행 간격: {self._check_interval}분")

    def stop(self):
        """스케줄러를 중지합니다."""
        if self._scheduler and self._is_running:
            self._scheduler.shutdown(wait=False)
            self._is_running = False
            logger.info("트레이딩 엔진이 중지되었습니다.")

    @property
    def is_running(self) -> bool:
        return self._is_running

    def get_status(self) -> dict:
        """트레이딩 엔진 상태 반환"""
        return {
            "is_running": self._is_running,
            "cycle_count": self._cycle_count,
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "strategy": self.strategy.name,
            "watchlist": self.watchlist,
            "check_interval_minutes": self._check_interval,
            "market_open": get_market_status(),
            "recent_errors": self._errors[-5:],
        }

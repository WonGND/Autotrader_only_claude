"""
백테스팅 엔진

주어진 전략과 기간에 대해 과거 데이터로 거래를 시뮬레이션합니다.
"""

import numpy as np
import pandas as pd
from datetime import datetime
from typing import List, Dict, Optional

from strategies.base import BaseStrategy
from data.fetcher import DataFetcher
from database.models import BacktestResult, get_session
from utils.logger import get_logger

logger = get_logger(__name__)


class TradeRecord:
    """백테스트 중 거래 기록"""
    def __init__(self, date, symbol, side, quantity, price, pnl=0.0):
        self.date = date
        self.symbol = symbol
        self.side = side
        self.quantity = quantity
        self.price = price
        self.pnl = pnl


class BacktestEngine:
    """
    백테스팅 엔진

    Args:
        strategy: 실행할 전략 객체
        initial_capital: 초기 자본금 (원)
        commission_rate: 수수료율 (기본값: 0.00015 = 0.015%)
        sell_commission_rate: 매도 수수료율 (기본값: 0.003 = 0.3%)
        position_size_pct: 포지션 크기 비율 (기본값: 0.2 = 20%)
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        initial_capital: float = 10_000_000,
        commission_rate: float = 0.00015,
        sell_commission_rate: float = 0.003,
        position_size_pct: float = 0.2,
    ):
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.commission_rate = commission_rate
        self.sell_commission_rate = sell_commission_rate
        self.position_size_pct = position_size_pct
        self.data_fetcher = DataFetcher()

    def run(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        save_to_db: bool = True,
    ) -> BacktestResult:
        """
        백테스트 실행

        Args:
            symbol: 종목 코드
            start_date: 시작일 (YYYY-MM-DD)
            end_date: 종료일 (YYYY-MM-DD)
            save_to_db: DB에 결과 저장 여부

        Returns:
            BacktestResult 객체
        """
        logger.info(f"백테스트 시작: {symbol} | {start_date} ~ {end_date} | {self.strategy.name}")

        # 데이터 로드 (전략 계산을 위해 start_date보다 충분히 앞선 데이터 필요)
        buffer_days = self.strategy.get_required_history() * 2
        from datetime import timedelta
        extended_start = (
            datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=buffer_days)
        ).strftime("%Y-%m-%d")

        data = self.data_fetcher.get_ohlcv(symbol, extended_start, end_date)
        if data.empty:
            logger.error(f"{symbol} 데이터를 가져오지 못했습니다.")
            return self._empty_result(symbol, start_date, end_date)

        # 신호 생성
        data_with_signals = self.strategy.generate_signals(data)

        # start_date 이후 데이터만 사용
        backtest_data = data_with_signals[data_with_signals.index >= start_date]
        if backtest_data.empty:
            logger.error(f"{symbol} 백테스트 기간에 데이터가 없습니다.")
            return self._empty_result(symbol, start_date, end_date)

        # 시뮬레이션
        cash = self.initial_capital
        position = 0       # 보유 수량
        avg_price = 0.0    # 평균 매수가
        trades: List[TradeRecord] = []
        portfolio_values: List[float] = []
        dates: List[datetime] = []

        for idx, row in backtest_data.iterrows():
            price = row["Close"]
            signal = row.get("signal", 0)
            portfolio_value = cash + position * price
            portfolio_values.append(portfolio_value)
            dates.append(idx)

            if signal == 1 and position == 0:
                # 매수
                invest_amount = portfolio_value * self.position_size_pct
                quantity = int(invest_amount / price)
                if quantity > 0:
                    cost = quantity * price
                    commission = cost * self.commission_rate
                    total_cost = cost + commission
                    if cash >= total_cost:
                        cash -= total_cost
                        position = quantity
                        avg_price = price
                        trades.append(TradeRecord(idx, symbol, "buy", quantity, price))
                        logger.debug(f"{idx.date()} 매수: {quantity}주 @ {price:,.0f}")

            elif signal == -1 and position > 0:
                # 매도
                proceeds = position * price
                commission = proceeds * self.sell_commission_rate
                pnl = (price - avg_price) * position - commission
                cash += proceeds - commission
                trades.append(TradeRecord(idx, symbol, "sell", position, price, pnl))
                logger.debug(f"{idx.date()} 매도: {position}주 @ {price:,.0f}, 손익: {pnl:,.0f}")
                position = 0
                avg_price = 0.0

        # 보유 중인 포지션이 있으면 마지막 날 청산
        if position > 0 and not backtest_data.empty:
            last_price = backtest_data["Close"].iloc[-1]
            proceeds = position * last_price
            commission = proceeds * self.sell_commission_rate
            pnl = (last_price - avg_price) * position - commission
            cash += proceeds - commission
            trades.append(
                TradeRecord(backtest_data.index[-1], symbol, "sell", position, last_price, pnl)
            )

        final_capital = cash
        total_return = (final_capital - self.initial_capital) / self.initial_capital

        # 성과 지표 계산
        equity_series = pd.Series(portfolio_values, index=dates)
        max_drawdown = self._calculate_max_drawdown(equity_series)
        sharpe_ratio = self._calculate_sharpe_ratio(equity_series)

        sell_trades = [t for t in trades if t.side == "sell"]
        total_trades = len(sell_trades)
        winning_trades = sum(1 for t in sell_trades if t.pnl > 0)
        losing_trades = total_trades - winning_trades
        win_rate = winning_trades / total_trades if total_trades > 0 else 0.0

        logger.info(
            f"백테스트 완료: 총수익률={total_return:.2%}, "
            f"최대낙폭={max_drawdown:.2%}, 샤프={sharpe_ratio:.2f}, "
            f"승률={win_rate:.2%} ({winning_trades}/{total_trades})"
        )

        result = BacktestResult(
            strategy=self.strategy.name,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            initial_capital=self.initial_capital,
            final_capital=final_capital,
            total_return=total_return,
            max_drawdown=max_drawdown,
            sharpe_ratio=sharpe_ratio,
            win_rate=win_rate,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
        )

        # 추가 데이터를 result에 임시 저장 (리포트용)
        result._equity_series = equity_series
        result._trades = trades

        if save_to_db:
            try:
                session = get_session()
                session.add(result)
                session.commit()
                session.close()
            except Exception as e:
                logger.error(f"백테스트 결과 저장 실패: {e}")

        return result

    def _calculate_max_drawdown(self, equity: pd.Series) -> float:
        """최대 낙폭(MDD) 계산"""
        if equity.empty:
            return 0.0
        peak = equity.expanding().max()
        drawdown = (equity - peak) / peak
        return float(drawdown.min())

    def _calculate_sharpe_ratio(self, equity: pd.Series, risk_free_rate: float = 0.03) -> float:
        """샤프 비율 계산 (연율화)"""
        if len(equity) < 2:
            return 0.0
        daily_returns = equity.pct_change().dropna()
        if daily_returns.std() == 0:
            return 0.0
        annual_factor = np.sqrt(252)
        daily_rf = risk_free_rate / 252
        sharpe = (daily_returns.mean() - daily_rf) / daily_returns.std() * annual_factor
        return float(sharpe)

    def _empty_result(self, symbol: str, start_date: str, end_date: str) -> BacktestResult:
        """빈 결과 반환 (오류 시)"""
        result = BacktestResult(
            strategy=self.strategy.name,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            initial_capital=self.initial_capital,
            final_capital=self.initial_capital,
            total_return=0.0,
            max_drawdown=0.0,
            sharpe_ratio=0.0,
            win_rate=0.0,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
        )
        result._equity_series = pd.Series(dtype=float)
        result._trades = []
        return result

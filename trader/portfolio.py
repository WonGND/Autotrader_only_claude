"""
포트폴리오 & 포지션 관리 모듈
"""

import pandas as pd
from typing import Dict, Optional

from broker.base import BaseBroker, Position
from utils.logger import get_logger

logger = get_logger(__name__)


class PortfolioManager:
    """
    포트폴리오 및 포지션 관리 클래스

    Args:
        broker: 브로커 객체
        stop_loss_pct: 손절 기준 (기본값: 0.05 = -5%)
        take_profit_pct: 익절 기준 (기본값: 0.15 = +15%)
        position_size_pct: 포지션 크기 비율 (기본값: 0.2 = 20%)
        max_positions: 최대 동시 보유 종목 수
    """

    def __init__(
        self,
        broker: BaseBroker,
        stop_loss_pct: float = 0.05,
        take_profit_pct: float = 0.15,
        position_size_pct: float = 0.2,
        max_positions: int = 5,
    ):
        self.broker = broker
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.position_size_pct = position_size_pct
        self.max_positions = max_positions

    def calculate_position_size(self, symbol: str, price: float, pct: float = None) -> int:
        """
        포지션 크기(수량)를 계산합니다.

        Args:
            symbol: 종목 코드
            price: 현재 가격
            pct: 포지션 비율 (None이면 self.position_size_pct 사용)

        Returns:
            매수 수량 (정수)
        """
        if pct is None:
            pct = self.position_size_pct

        if price <= 0:
            return 0

        try:
            portfolio_value = self.get_portfolio_value()
            invest_amount = portfolio_value * pct
            quantity = int(invest_amount / price)
            return max(0, quantity)
        except Exception as e:
            logger.error(f"포지션 크기 계산 실패: {e}")
            return 0

    def check_stop_loss(self, position: Position) -> bool:
        """
        손절 조건을 확인합니다.

        Args:
            position: 현재 포지션

        Returns:
            True if 손절 조건 충족
        """
        if position.avg_price <= 0:
            return False
        loss_pct = (position.current_price - position.avg_price) / position.avg_price
        return loss_pct <= -self.stop_loss_pct

    def check_take_profit(self, position: Position) -> bool:
        """
        익절 조건을 확인합니다.

        Args:
            position: 현재 포지션

        Returns:
            True if 익절 조건 충족
        """
        if position.avg_price <= 0:
            return False
        gain_pct = (position.current_price - position.avg_price) / position.avg_price
        return gain_pct >= self.take_profit_pct

    def get_portfolio_value(self) -> float:
        """
        총 포트폴리오 가치를 계산합니다. (현금 + 보유 종목 평가금액)

        Returns:
            총 포트폴리오 가치 (원)
        """
        try:
            account = self.broker.get_account_info()
            return account.total_value
        except Exception as e:
            logger.error(f"포트폴리오 가치 조회 실패: {e}")
            return 0.0

    def get_positions_df(self) -> pd.DataFrame:
        """
        현재 보유 포지션을 DataFrame으로 반환합니다.

        Returns:
            포지션 DataFrame
        """
        try:
            positions = self.broker.get_positions()
            if not positions:
                return pd.DataFrame(columns=[
                    "symbol", "quantity", "avg_price",
                    "current_price", "unrealized_pnl", "unrealized_pnl_pct"
                ])
            rows = []
            for symbol, pos in positions.items():
                rows.append({
                    "symbol": pos.symbol,
                    "quantity": pos.quantity,
                    "avg_price": pos.avg_price,
                    "current_price": pos.current_price,
                    "unrealized_pnl": pos.unrealized_pnl,
                    "unrealized_pnl_pct": pos.unrealized_pnl_pct,
                })
            return pd.DataFrame(rows)
        except Exception as e:
            logger.error(f"포지션 조회 실패: {e}")
            return pd.DataFrame()

    def can_open_position(self, symbol: str) -> bool:
        """
        새 포지션을 열 수 있는지 확인합니다.

        - 최대 보유 종목 수 초과 여부 확인
        - 이미 해당 종목을 보유 중인지 확인

        Returns:
            True if 포지션 가능
        """
        try:
            positions = self.broker.get_positions()
            if symbol in positions:
                return False
            if len(positions) >= self.max_positions:
                logger.info(f"최대 포지션 수({self.max_positions}) 도달. {symbol} 매수 불가.")
                return False
            return True
        except Exception as e:
            logger.error(f"포지션 가능 여부 확인 실패: {e}")
            return False

    def get_summary(self) -> Dict:
        """포트폴리오 요약 반환"""
        try:
            account = self.broker.get_account_info()
            positions = self.broker.get_positions()
            return {
                "total_value": account.total_value,
                "cash": account.cash,
                "invested": account.invested,
                "profit_loss": account.profit_loss,
                "profit_loss_pct": account.profit_loss_pct,
                "num_positions": len(positions),
                "positions": [
                    {
                        "symbol": pos.symbol,
                        "quantity": pos.quantity,
                        "avg_price": pos.avg_price,
                        "current_price": pos.current_price,
                        "unrealized_pnl": pos.unrealized_pnl,
                        "unrealized_pnl_pct": pos.unrealized_pnl_pct,
                    }
                    for pos in positions.values()
                ],
            }
        except Exception as e:
            logger.error(f"포트폴리오 요약 조회 실패: {e}")
            return {}

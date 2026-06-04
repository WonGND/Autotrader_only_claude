"""
모의 투자 브로커 (Paper Trading Broker)

실제 증권사 API 연동 없이 가상의 계좌로 거래를 시뮬레이션합니다.
슬리피지(0.1%)와 거래 수수료(매수 0.015%, 매도 0.3%)를 반영합니다.
"""

import uuid
from datetime import datetime
from typing import Dict, List, Optional

from broker.base import BaseBroker, Order, Position, AccountInfo
from utils.logger import get_logger

logger = get_logger(__name__)


class PaperBroker(BaseBroker):
    """
    모의 투자 브로커

    실제 증권사 API 없이 전략을 테스트할 수 있습니다.
    모든 주문은 즉시 체결되며, 슬리피지와 수수료가 적용됩니다.
    """

    BUY_COMMISSION = 0.00015    # 매수 수수료 0.015%
    SELL_COMMISSION = 0.003     # 매도 수수료 0.3% (거래세 포함)
    SLIPPAGE = 0.001            # 슬리피지 0.1%

    def __init__(self, initial_capital: float = 10_000_000):
        super().__init__()
        self._cash = initial_capital
        self._initial_capital = initial_capital
        self._positions: Dict[str, dict] = {}   # symbol -> {quantity, avg_price}
        self._orders: Dict[str, Order] = {}
        self._trade_history: List[Order] = []
        self._price_cache: Dict[str, float] = {}
        self._is_connected = True
        logger.info(f"모의 투자 브로커 초기화 완료. 초기 자본: ₩{initial_capital:,.0f}")

    def _get_cached_price(self, symbol: str) -> float:
        """캐시된 가격 반환 (없으면 기본값)"""
        return self._price_cache.get(symbol, 0.0)

    def set_price(self, symbol: str, price: float):
        """시뮬레이션용 가격 설정"""
        self._price_cache[symbol] = price

    def get_balance(self) -> float:
        """현금 잔고 반환"""
        return self._cash

    def get_positions(self) -> Dict[str, Position]:
        """현재 보유 포지션 반환"""
        positions = {}
        for symbol, pos_data in self._positions.items():
            current_price = self._price_cache.get(symbol, pos_data["avg_price"])
            unrealized_pnl = (current_price - pos_data["avg_price"]) * pos_data["quantity"]
            unrealized_pnl_pct = (current_price - pos_data["avg_price"]) / pos_data["avg_price"]
            positions[symbol] = Position(
                symbol=symbol,
                quantity=pos_data["quantity"],
                avg_price=pos_data["avg_price"],
                current_price=current_price,
                unrealized_pnl=unrealized_pnl,
                unrealized_pnl_pct=unrealized_pnl_pct,
            )
        return positions

    def get_price(self, symbol: str) -> float:
        """현재가 반환 (캐시 사용)"""
        price = self._price_cache.get(symbol, 0.0)
        if price == 0.0:
            logger.warning(f"{symbol} 가격이 설정되지 않았습니다.")
        return price

    def buy(self, symbol: str, quantity: int, price: Optional[float] = None) -> Order:
        """
        매수 주문 실행

        슬리피지를 반영하여 요청 가격보다 약간 높게 체결됩니다.
        """
        if price is None:
            price = self._price_cache.get(symbol, 0.0)
            if price == 0.0:
                logger.error(f"{symbol} 현재가를 가져올 수 없습니다.")
                return self._create_failed_order(symbol, "buy", quantity, 0)

        # 슬리피지 적용 (매수는 가격이 올라감)
        fill_price = price * (1 + self.SLIPPAGE)
        fill_price = round(fill_price)

        total_cost = fill_price * quantity
        commission = total_cost * self.BUY_COMMISSION
        total_with_commission = total_cost + commission

        order_id = str(uuid.uuid4())[:8]

        if self._cash < total_with_commission:
            logger.warning(
                f"잔고 부족. 필요: ₩{total_with_commission:,.0f}, 보유: ₩{self._cash:,.0f}"
            )
            return self._create_failed_order(symbol, "buy", quantity, price, order_id)

        # 주문 체결
        self._cash -= total_with_commission

        if symbol in self._positions:
            existing = self._positions[symbol]
            total_qty = existing["quantity"] + quantity
            avg_price = (
                existing["avg_price"] * existing["quantity"] + fill_price * quantity
            ) / total_qty
            self._positions[symbol] = {"quantity": total_qty, "avg_price": avg_price}
        else:
            self._positions[symbol] = {"quantity": quantity, "avg_price": fill_price}

        order = Order(
            order_id=order_id,
            symbol=symbol,
            side="buy",
            quantity=quantity,
            price=price,
            status="filled",
            filled_price=fill_price,
            filled_quantity=quantity,
            timestamp=datetime.now(),
            message=f"수수료: ₩{commission:,.0f}",
        )
        self._orders[order_id] = order
        self._trade_history.append(order)

        logger.info(
            f"매수 체결: {symbol} {quantity}주 @ ₩{fill_price:,.0f} "
            f"(총 ₩{total_with_commission:,.0f}, 수수료 ₩{commission:,.0f})"
        )
        return order

    def sell(self, symbol: str, quantity: int, price: Optional[float] = None) -> Order:
        """
        매도 주문 실행

        슬리피지를 반영하여 요청 가격보다 약간 낮게 체결됩니다.
        """
        if symbol not in self._positions or self._positions[symbol]["quantity"] < quantity:
            held = self._positions.get(symbol, {}).get("quantity", 0)
            logger.warning(f"{symbol} 보유 수량 부족. 보유: {held}주, 매도 요청: {quantity}주")
            return self._create_failed_order(symbol, "sell", quantity, price or 0)

        if price is None:
            price = self._price_cache.get(symbol, 0.0)
            if price == 0.0:
                logger.error(f"{symbol} 현재가를 가져올 수 없습니다.")
                return self._create_failed_order(symbol, "sell", quantity, 0)

        # 슬리피지 적용 (매도는 가격이 내려감)
        fill_price = price * (1 - self.SLIPPAGE)
        fill_price = round(fill_price)

        total_proceeds = fill_price * quantity
        commission = total_proceeds * self.SELL_COMMISSION
        net_proceeds = total_proceeds - commission

        avg_price = self._positions[symbol]["avg_price"]
        pnl = (fill_price - avg_price) * quantity - commission

        # 포지션 업데이트
        remaining = self._positions[symbol]["quantity"] - quantity
        if remaining == 0:
            del self._positions[symbol]
        else:
            self._positions[symbol]["quantity"] = remaining

        self._cash += net_proceeds

        order_id = str(uuid.uuid4())[:8]
        order = Order(
            order_id=order_id,
            symbol=symbol,
            side="sell",
            quantity=quantity,
            price=price,
            status="filled",
            filled_price=fill_price,
            filled_quantity=quantity,
            timestamp=datetime.now(),
            message=f"손익: ₩{pnl:,.0f}, 수수료: ₩{commission:,.0f}",
        )
        self._orders[order_id] = order
        self._trade_history.append(order)

        logger.info(
            f"매도 체결: {symbol} {quantity}주 @ ₩{fill_price:,.0f} "
            f"(손익 ₩{pnl:,.0f}, 수수료 ₩{commission:,.0f})"
        )
        return order

    def get_order_status(self, order_id: str) -> Order:
        """주문 상태 조회"""
        if order_id not in self._orders:
            raise ValueError(f"주문 번호 {order_id}를 찾을 수 없습니다.")
        return self._orders[order_id]

    def cancel_order(self, order_id: str) -> bool:
        """
        주문 취소 (모의 투자에서는 이미 체결된 주문은 취소 불가)
        """
        if order_id not in self._orders:
            return False
        order = self._orders[order_id]
        if order.status == "pending":
            order.status = "cancelled"
            return True
        return False

    def get_account_info(self) -> AccountInfo:
        """계좌 정보 반환"""
        invested = sum(
            pos["quantity"] * pos["avg_price"]
            for pos in self._positions.values()
        )
        current_value = sum(
            pos["quantity"] * self._price_cache.get(symbol, pos["avg_price"])
            for symbol, pos in self._positions.items()
        )
        total_value = self._cash + current_value
        profit_loss = total_value - self._initial_capital
        profit_loss_pct = profit_loss / self._initial_capital if self._initial_capital > 0 else 0

        return AccountInfo(
            account_number="PAPER-0001",
            total_value=total_value,
            cash=self._cash,
            invested=invested,
            profit_loss=profit_loss,
            profit_loss_pct=profit_loss_pct,
        )

    def get_trade_history(self) -> List[Order]:
        """전체 거래 내역 반환"""
        return list(self._trade_history)

    def get_portfolio_value(self) -> float:
        """총 포트폴리오 가치 반환"""
        current_value = sum(
            pos["quantity"] * self._price_cache.get(symbol, pos["avg_price"])
            for symbol, pos in self._positions.items()
        )
        return self._cash + current_value

    def _create_failed_order(
        self, symbol: str, side: str, quantity: int, price: float, order_id: str = None
    ) -> Order:
        """실패한 주문 객체 생성"""
        if order_id is None:
            order_id = str(uuid.uuid4())[:8]
        return Order(
            order_id=order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            status="failed",
            filled_price=0,
            filled_quantity=0,
            timestamp=datetime.now(),
            message="주문 실패",
        )

    def reset(self, initial_capital: float = None):
        """계좌 초기화 (테스트용)"""
        if initial_capital is not None:
            self._initial_capital = initial_capital
        self._cash = self._initial_capital
        self._positions.clear()
        self._orders.clear()
        self._trade_history.clear()
        logger.info("모의 투자 계좌가 초기화되었습니다.")

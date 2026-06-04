"""
브로커 추상 기본 클래스

이 파일은 실제 증권사 API를 연동할 때 구현해야 하는 인터페이스를 정의합니다.
사용자는 이 클래스를 상속받아 자신의 증권사 API에 맞게 각 메서드를 구현하면 됩니다.

지원 예시 증권사:
- 한국투자증권 (KIS) Open API
- 이베스트투자증권 xingAPI
- 키움증권 OpenAPI+
- 대신증권 CybosPlus
- NH투자증권 API

구현 방법:
1. 이 파일을 참고하여 새 파일(예: broker/kis_broker.py)을 만드세요.
2. BaseBroker를 상속받아 아래의 모든 추상 메서드를 구현하세요.
3. config/settings.yaml에서 broker.type을 변경하세요.
4. main.py에서 새로운 브로커 클래스를 import하여 사용하세요.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Order:
    """주문 정보 데이터 클래스"""
    order_id: str           # 주문 번호
    symbol: str             # 종목 코드
    side: str               # "buy" 또는 "sell"
    quantity: int           # 수량
    price: float            # 주문 가격 (0이면 시장가)
    status: str             # "pending", "filled", "cancelled", "failed"
    filled_price: float     # 체결 가격
    filled_quantity: int    # 체결 수량
    timestamp: datetime     # 주문 시간
    message: str = ""       # 부가 메시지


@dataclass
class Position:
    """보유 포지션 데이터 클래스"""
    symbol: str             # 종목 코드
    quantity: int           # 보유 수량
    avg_price: float        # 평균 매수가
    current_price: float    # 현재가
    unrealized_pnl: float   # 미실현 손익
    unrealized_pnl_pct: float  # 미실현 손익률


@dataclass
class AccountInfo:
    """계좌 정보 데이터 클래스"""
    account_number: str     # 계좌번호
    total_value: float      # 총 평가금액
    cash: float             # 현금 잔고
    invested: float         # 투자금액
    profit_loss: float      # 손익금액
    profit_loss_pct: float  # 손익률


class BaseBroker(ABC):
    """
    브로커 추상 기본 클래스

    실제 증권사 API를 사용하려면 이 클래스를 상속받아
    아래의 모든 추상 메서드(@abstractmethod)를 구현하세요.

    구현 예시:
        class KISBroker(BaseBroker):
            def __init__(self, app_key: str, app_secret: str, account: str):
                self.app_key = app_key
                self.app_secret = app_secret
                self.account = account
                self._access_token = None
                self._authenticate()

            def get_balance(self) -> float:
                # 한국투자증권 API 호출하여 잔고 조회
                ...
    """

    def __init__(self):
        self._is_connected = False
        self._orders: Dict[str, Order] = {}

    @property
    def is_connected(self) -> bool:
        """브로커 연결 상태를 반환합니다."""
        return self._is_connected

    @abstractmethod
    def get_balance(self) -> float:
        """
        현금 잔고를 조회합니다.

        구현 방법:
            증권사 API의 잔고 조회 endpoint를 호출하여
            현재 사용 가능한 현금 금액을 반환합니다.

        Returns:
            현금 잔고 (원화)

        예시:
            def get_balance(self) -> float:
                response = self._api_call("GET", "/balance")
                return response["available_cash"]
        """
        pass

    @abstractmethod
    def get_positions(self) -> Dict[str, Position]:
        """
        현재 보유 포지션을 조회합니다.

        구현 방법:
            증권사 API의 보유종목 조회 endpoint를 호출하여
            현재 보유 중인 모든 종목 정보를 반환합니다.

        Returns:
            Dict[종목코드, Position] 형태의 딕셔너리

        예시:
            def get_positions(self) -> Dict[str, Position]:
                response = self._api_call("GET", "/positions")
                positions = {}
                for item in response["holdings"]:
                    positions[item["symbol"]] = Position(
                        symbol=item["symbol"],
                        quantity=item["qty"],
                        avg_price=item["avg_price"],
                        current_price=item["current_price"],
                        unrealized_pnl=item["eval_profit"],
                        unrealized_pnl_pct=item["profit_rate"]
                    )
                return positions
        """
        pass

    @abstractmethod
    def get_price(self, symbol: str) -> float:
        """
        특정 종목의 현재가를 조회합니다.

        구현 방법:
            증권사 API의 현재가 조회 endpoint를 호출합니다.
            실시간 시세 또는 최근 체결가를 반환합니다.

        Args:
            symbol: 종목 코드 (예: "005930" for 삼성전자)

        Returns:
            현재가 (원화)

        예시:
            def get_price(self, symbol: str) -> float:
                response = self._api_call("GET", f"/price/{symbol}")
                return float(response["current_price"])
        """
        pass

    @abstractmethod
    def buy(self, symbol: str, quantity: int, price: Optional[float] = None) -> Order:
        """
        매수 주문을 제출합니다.

        구현 방법:
            증권사 API의 매수 주문 endpoint를 호출합니다.
            price가 None이면 시장가 주문, 있으면 지정가 주문으로 처리하세요.

        Args:
            symbol: 종목 코드
            quantity: 매수 수량
            price: 지정가 (None이면 시장가)

        Returns:
            Order 객체 (주문 결과)

        예시:
            def buy(self, symbol: str, quantity: int, price: Optional[float] = None) -> Order:
                order_type = "01" if price else "00"  # 지정가 or 시장가
                response = self._api_call("POST", "/order/buy", {
                    "symbol": symbol,
                    "qty": quantity,
                    "price": price or 0,
                    "order_type": order_type
                })
                return Order(
                    order_id=response["order_id"],
                    symbol=symbol,
                    side="buy",
                    quantity=quantity,
                    price=price or 0,
                    status="pending",
                    filled_price=0,
                    filled_quantity=0,
                    timestamp=datetime.now()
                )
        """
        pass

    @abstractmethod
    def sell(self, symbol: str, quantity: int, price: Optional[float] = None) -> Order:
        """
        매도 주문을 제출합니다.

        구현 방법:
            증권사 API의 매도 주문 endpoint를 호출합니다.
            price가 None이면 시장가 주문, 있으면 지정가 주문으로 처리하세요.

        Args:
            symbol: 종목 코드
            quantity: 매도 수량
            price: 지정가 (None이면 시장가)

        Returns:
            Order 객체 (주문 결과)

        예시:
            def sell(self, symbol: str, quantity: int, price: Optional[float] = None) -> Order:
                order_type = "01" if price else "00"
                response = self._api_call("POST", "/order/sell", {
                    "symbol": symbol,
                    "qty": quantity,
                    "price": price or 0,
                    "order_type": order_type
                })
                return Order(
                    order_id=response["order_id"],
                    symbol=symbol,
                    side="sell",
                    quantity=quantity,
                    price=price or 0,
                    status="pending",
                    filled_price=0,
                    filled_quantity=0,
                    timestamp=datetime.now()
                )
        """
        pass

    @abstractmethod
    def get_order_status(self, order_id: str) -> Order:
        """
        주문 상태를 조회합니다.

        구현 방법:
            증권사 API의 주문 조회 endpoint를 호출하여
            특정 주문의 현재 상태를 반환합니다.

        Args:
            order_id: 주문 번호

        Returns:
            Order 객체 (최신 상태)

        예시:
            def get_order_status(self, order_id: str) -> Order:
                response = self._api_call("GET", f"/order/{order_id}")
                return Order(
                    order_id=order_id,
                    symbol=response["symbol"],
                    side=response["side"],
                    quantity=response["qty"],
                    price=response["price"],
                    status=response["status"],  # "filled", "pending", "cancelled"
                    filled_price=response["filled_price"],
                    filled_quantity=response["filled_qty"],
                    timestamp=datetime.fromisoformat(response["timestamp"])
                )
        """
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """
        주문을 취소합니다.

        구현 방법:
            증권사 API의 주문 취소 endpoint를 호출합니다.

        Args:
            order_id: 취소할 주문 번호

        Returns:
            True if 취소 성공, False otherwise

        예시:
            def cancel_order(self, order_id: str) -> bool:
                try:
                    response = self._api_call("DELETE", f"/order/{order_id}")
                    return response["success"]
                except Exception:
                    return False
        """
        pass

    @abstractmethod
    def get_account_info(self) -> AccountInfo:
        """
        계좌 정보를 조회합니다.

        구현 방법:
            증권사 API의 계좌 정보 조회 endpoint를 호출하여
            계좌의 전체 정보를 반환합니다.

        Returns:
            AccountInfo 객체

        예시:
            def get_account_info(self) -> AccountInfo:
                response = self._api_call("GET", "/account")
                return AccountInfo(
                    account_number=self.account,
                    total_value=response["total_eval"],
                    cash=response["available_cash"],
                    invested=response["invested_amount"],
                    profit_loss=response["total_profit"],
                    profit_loss_pct=response["profit_rate"]
                )
        """
        pass

    def get_order_history(self, days: int = 7) -> List[Order]:
        """
        주문 내역을 조회합니다. (선택적 구현)

        Args:
            days: 조회 기간 (일)

        Returns:
            Order 목록
        """
        return list(self._orders.values())

"""
선물 포지션 데이터 클래스 (Futures Position Dataclass)

롱/숏 포지션의 손익, 청산가, 레버리지 계산 등을 담당합니다.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class FuturesPosition:
    """
    선물 포지션 정보

    Attributes:
        symbol:             종목 코드 (예: BTC-USD)
        side:               방향 ("long" 또는 "short")
        entry_price:        진입가
        quantity:           포지션 크기 (USD 환산 명목 금액)
        margin:             실제 사용 증거금 (quantity / leverage)
        leverage:           적용 레버리지 (1~10)
        stop_loss:          손절가
        take_profit:        익절가
        liquidation_price:  강제 청산가
        entry_time:         진입 시각
        unrealized_pnl:     미실현 손익 (USD)
    """

    symbol: str
    side: str                       # "long" or "short"
    entry_price: float
    quantity: float                 # 명목 포지션 크기 (USD)
    margin: float                   # 실제 투입 증거금 (USD)
    leverage: int                   # 레버리지 (1~10)
    stop_loss: float
    take_profit: float
    liquidation_price: float
    entry_time: datetime
    unrealized_pnl: float = 0.0
    # 트레일링 스탑 추적용
    best_price: float = 0.0   # 보유 중 최고(롱) 또는 최저(숏) 도달가
    entry_atr: float = 0.0    # 진입 시점 ATR (트레일링 계산 기준)
    # 진입 근거 추적용
    signal_strength: int = 0
    entry_basis: str = ""
    # 내부 추적용
    trade_id: Optional[str] = None

    def __post_init__(self):
        """입력값 유효성 검사"""
        if self.side not in ("long", "short"):
            raise ValueError(f"side는 'long' 또는 'short'이어야 합니다. 입력값: {self.side}")
        if not (1 <= self.leverage <= 10):
            raise ValueError(f"레버리지는 1~10 사이여야 합니다. 입력값: {self.leverage}")
        if self.entry_price <= 0:
            raise ValueError(f"진입가는 양수여야 합니다. 입력값: {self.entry_price}")
        if self.margin <= 0:
            raise ValueError(f"증거금은 양수여야 합니다. 입력값: {self.margin}")

    # ─── 계산 메서드 ──────────────────────────────────────────────

    def calculate_liquidation_price(self) -> float:
        """
        강제 청산가 계산

        유지 증거금률 = 0.5% (maintenance_margin)
        롱: entry_price × (1 - 1/leverage + maintenance_margin)
        숏: entry_price × (1 + 1/leverage - maintenance_margin)
        """
        maintenance_margin = 0.005  # 0.5%
        if self.side == "long":
            return self.entry_price * (1 - 1 / self.leverage + maintenance_margin)
        else:
            return self.entry_price * (1 + 1 / self.leverage - maintenance_margin)

    def calculate_pnl(self, current_price: float) -> float:
        """
        현재가 기준 미실현 손익 계산 (USD)

        롱: (현재가 - 진입가) / 진입가 × 명목금액
        숏: (진입가 - 현재가) / 진입가 × 명목금액
        """
        if current_price <= 0:
            return 0.0
        if self.side == "long":
            return (current_price - self.entry_price) / self.entry_price * self.quantity
        else:
            return (self.entry_price - current_price) / self.entry_price * self.quantity

    def calculate_pnl_pct(self, current_price: float) -> float:
        """
        증거금 대비 손익률 (%)

        손익 / 증거금 × 100
        """
        if self.margin <= 0:
            return 0.0
        return self.calculate_pnl(current_price) / self.margin * 100

    def distance_to_liquidation_pct(self, current_price: float) -> float:
        """
        현재가 기준 청산가까지의 거리 (%)
        양수 = 아직 안전, 음수 = 청산 위험
        """
        if current_price <= 0:
            return 0.0
        if self.side == "long":
            return (current_price - self.liquidation_price) / current_price * 100
        else:
            return (self.liquidation_price - current_price) / current_price * 100

    def is_stop_loss_hit(self, low: float, high: float) -> bool:
        """캔들의 고/저가 기준으로 손절 조건 확인"""
        if self.side == "long":
            return low <= self.stop_loss
        else:
            return high >= self.stop_loss

    def is_take_profit_hit(self, low: float, high: float) -> bool:
        """캔들의 고/저가 기준으로 익절 조건 확인"""
        if self.side == "long":
            return high >= self.take_profit
        else:
            return low <= self.take_profit

    def is_liquidated(self, low: float, high: float) -> bool:
        """캔들의 고/저가 기준으로 청산 조건 확인"""
        if self.side == "long":
            return low <= self.liquidation_price
        else:
            return high >= self.liquidation_price

    def to_dict(self) -> dict:
        """딕셔너리로 변환"""
        return {
            "symbol": self.symbol,
            "side": self.side,
            "entry_price": self.entry_price,
            "quantity": self.quantity,
            "margin": self.margin,
            "leverage": self.leverage,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "liquidation_price": self.liquidation_price,
            "entry_time": self.entry_time.isoformat() if self.entry_time else None,
            "unrealized_pnl": self.unrealized_pnl,
            "trade_id": self.trade_id,
        }

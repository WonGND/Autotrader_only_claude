"""
레버리지 관리자 (Leverage Manager)

변동성(ATR)과 신호 강도에 따라 레버리지, 증거금, 손절/익절가를 계산합니다.
"""

from utils.logger import get_logger

logger = get_logger(__name__)


class LeverageManager:
    """
    레버리지 및 포지션 크기 관리

    - ATR% 기반 최대 레버리지 결정
    - 신호 강도 기반 실효 레버리지 조정
    - 리스크 기반 증거금 계산
    - ATR 기반 손절/익절가 계산
    - 청산가 안전 여부 확인
    """

    MAX_LEVERAGE = 10
    MIN_LEVERAGE = 1

    # ─── 레버리지 계산 ─────────────────────────────────────────────

    def calculate_max_leverage_by_volatility(self, atr_pct: float) -> int:
        """
        ATR% 기반 최대 레버리지 결정

        낮은 변동성 → 높은 레버리지 허용
        높은 변동성 → 낮은 레버리지로 리스크 제한

        Args:
            atr_pct: ATR을 현재가 대비 백분율로 나타낸 값

        Returns:
            최대 허용 레버리지 (int)
        """
        if atr_pct < 2.0:
            return 10
        elif atr_pct < 4.0:
            return 7
        elif atr_pct < 6.0:
            return 5
        elif atr_pct < 8.0:
            return 3
        else:
            return 2

    def calculate_effective_leverage(self, base_leverage: int, signal_strength: int) -> int:
        """
        신호 강도에 따른 실효 레버리지 조정

        신호가 약할수록 레버리지를 줄여 리스크 감소:
            1 (약함) → base × 40%
            2 (보통) → base × 70%
            3 (강함) → base × 100%

        Args:
            base_leverage:    ATR 기반으로 결정된 최대 레버리지
            signal_strength:  신호 강도 (1~3)

        Returns:
            실효 레버리지 (int, 최소 1)
        """
        multipliers = {1: 0.4, 2: 0.7, 3: 1.0}
        factor = multipliers.get(signal_strength, 0.4)
        effective = max(self.MIN_LEVERAGE, round(base_leverage * factor))
        return min(effective, self.MAX_LEVERAGE)

    # ─── 증거금 계산 ───────────────────────────────────────────────

    def calculate_margin(
        self,
        portfolio_value: float,
        risk_pct: float,
        stop_loss_distance_pct: float,
        leverage: int,
    ) -> float:
        """
        리스크 기반 증거금 계산

        원칙: 최대 손실 = portfolio_value × risk_pct
        수식: margin × leverage × stop_loss_distance_pct = max_loss
              margin = max_loss / (leverage × stop_loss_distance_pct)

        최대 증거금 상한: portfolio_value × 15%

        Args:
            portfolio_value:          현재 포트폴리오 총 가치
            risk_pct:                 거래당 최대 손실 허용 비율 (예: 0.02 = 2%)
            stop_loss_distance_pct:   진입가 대비 손절가 거리 비율 (소수)
            leverage:                 적용 레버리지

        Returns:
            계산된 증거금 (float)
        """
        if leverage <= 0 or stop_loss_distance_pct <= 0:
            logger.warning("레버리지 또는 손절 거리가 0 이하입니다. 기본값 사용.")
            return portfolio_value * 0.02  # 폴백: 포트폴리오의 2%

        max_loss = portfolio_value * risk_pct
        margin = max_loss / (leverage * stop_loss_distance_pct)
        max_margin = portfolio_value * 0.15  # 포트폴리오의 최대 15%
        margin = min(margin, max_margin)
        # 최소 증거금: $10 이상
        margin = max(margin, 10.0)
        return margin

    # ─── 손절/익절가 계산 ──────────────────────────────────────────

    def calculate_stop_loss(self, entry_price: float, atr: float, side: str) -> float:
        """
        ATR 기반 손절가 계산 (2 ATR)

        롱: 진입가 - 2 × ATR
        숏: 진입가 + 2 × ATR

        Args:
            entry_price: 진입가
            atr:         ATR 값
            side:        "long" 또는 "short"

        Returns:
            손절가
        """
        if side == "long":
            return entry_price - 2 * atr
        else:
            return entry_price + 2 * atr

    def calculate_take_profit(self, entry_price: float, atr: float, side: str) -> float:
        """
        ATR 기반 익절가 계산 (3 ATR, RR = 1.5)

        롱: 진입가 + 3 × ATR
        숏: 진입가 - 3 × ATR

        Args:
            entry_price: 진입가
            atr:         ATR 값
            side:        "long" 또는 "short"

        Returns:
            익절가
        """
        if side == "long":
            return entry_price + 3 * atr
        else:
            return entry_price - 3 * atr

    # ─── 안전 확인 ────────────────────────────────────────────────

    def is_liquidation_safe(
        self,
        stop_loss: float,
        liquidation_price: float,
        side: str,
    ) -> bool:
        """
        손절가가 청산가보다 충분히 안전한 위치에 있는지 확인

        15% 버퍼: 손절가가 청산가에서 최소 15% 이상 떨어져 있어야 함

        Args:
            stop_loss:         손절가
            liquidation_price: 강제 청산가
            side:              "long" 또는 "short"

        Returns:
            True if 안전 (손절가가 청산가보다 먼저 도달)
        """
        buffer = 0.15
        if side == "long":
            # 롱: 손절가 > 청산가 × (1 + buffer)
            return stop_loss > liquidation_price * (1 + buffer)
        else:
            # 숏: 손절가 < 청산가 × (1 - buffer)
            return stop_loss < liquidation_price * (1 - buffer)

    def calculate_stop_loss_distance_pct(
        self, entry_price: float, stop_loss: float
    ) -> float:
        """
        진입가 대비 손절 거리 비율 계산

        Returns:
            손절 거리 비율 (소수, 예: 0.04 = 4%)
        """
        if entry_price <= 0:
            return 0.04  # 기본값 4%
        return abs(entry_price - stop_loss) / entry_price

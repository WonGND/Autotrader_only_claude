"""
볼린저 밴드 전략 (Bollinger Bands Strategy)

가격이 하단 밴드에 닿으면 매수,
가격이 상단 밴드에 닿으면 매도합니다.
"""

import pandas as pd
from typing import Dict, Any

from strategies.base import BaseStrategy


class BollingerBandsStrategy(BaseStrategy):
    """볼린저 밴드 전략"""

    DEFAULT_PARAMS = {
        "period": 20,
        "std_dev": 2.0,
    }

    def __init__(self, params: Dict[str, Any] = None):
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)
        self.period = int(self.params["period"])
        self.std_dev = float(self.params["std_dev"])

    @property
    def name(self) -> str:
        return f"볼린저 밴드 (기간:{self.period}, 표준편차:{self.std_dev})"

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        볼린저 밴드 기반 신호 생성

        Args:
            data: OHLCV DataFrame

        Returns:
            signal 컬럼이 추가된 DataFrame
        """
        df = data.copy()

        # 볼린저 밴드 계산
        df["bb_middle"] = df["Close"].rolling(window=self.period).mean()
        df["bb_std"] = df["Close"].rolling(window=self.period).std()
        df["bb_upper"] = df["bb_middle"] + self.std_dev * df["bb_std"]
        df["bb_lower"] = df["bb_middle"] - self.std_dev * df["bb_std"]

        df["signal"] = 0

        df["prev_close"] = df["Close"].shift(1)
        df["prev_lower"] = df["bb_lower"].shift(1)
        df["prev_upper"] = df["bb_upper"].shift(1)

        # 하단 밴드 터치 후 반등 -> 매수
        buy_signal = (
            (df["Close"] > df["bb_lower"]) &
            (df["prev_close"] <= df["prev_lower"])
        )
        df.loc[buy_signal, "signal"] = 1

        # 상단 밴드 터치 후 하락 -> 매도
        sell_signal = (
            (df["Close"] < df["bb_upper"]) &
            (df["prev_close"] >= df["prev_upper"])
        )
        df.loc[sell_signal, "signal"] = -1

        df.drop(columns=["prev_close", "prev_lower", "prev_upper"], inplace=True)

        return df

    def get_required_history(self) -> int:
        """볼린저 밴드 계산을 위해 period 일수 필요"""
        return self.period + 1

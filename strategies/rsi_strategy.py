"""
RSI 과매수/과매도 전략 (RSI Overbought/Oversold Strategy)

RSI가 과매도 구간(기본 30 이하)에 진입하면 매수,
RSI가 과매수 구간(기본 70 이상)에 진입하면 매도합니다.
"""

import pandas as pd
import numpy as np
from typing import Dict, Any

from strategies.base import BaseStrategy


class RSIStrategy(BaseStrategy):
    """RSI 과매수/과매도 전략"""

    DEFAULT_PARAMS = {
        "period": 14,
        "oversold": 30,
        "overbought": 70,
    }

    def __init__(self, params: Dict[str, Any] = None):
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)
        self.period = int(self.params["period"])
        self.oversold = float(self.params["oversold"])
        self.overbought = float(self.params["overbought"])

    @property
    def name(self) -> str:
        return f"RSI 전략 (기간:{self.period}, 과매도:{self.oversold}, 과매수:{self.overbought})"

    def _calculate_rsi(self, prices: pd.Series) -> pd.Series:
        """RSI 계산"""
        delta = prices.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)

        avg_gain = gain.ewm(com=self.period - 1, min_periods=self.period).mean()
        avg_loss = loss.ewm(com=self.period - 1, min_periods=self.period).mean()

        rs = avg_gain / avg_loss.replace(0, np.inf)
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        RSI 기반 신호 생성

        Args:
            data: OHLCV DataFrame

        Returns:
            signal 컬럼이 추가된 DataFrame
        """
        df = data.copy()

        df["rsi"] = self._calculate_rsi(df["Close"])
        df["signal"] = 0

        df["prev_rsi"] = df["rsi"].shift(1)

        # 과매도 -> 매수: RSI가 oversold 아래에서 위로 돌파
        buy_signal = (
            (df["rsi"] > self.oversold) &
            (df["prev_rsi"] <= self.oversold)
        )
        df.loc[buy_signal, "signal"] = 1

        # 과매수 -> 매도: RSI가 overbought 위에서 아래로 돌파
        sell_signal = (
            (df["rsi"] < self.overbought) &
            (df["prev_rsi"] >= self.overbought)
        )
        df.loc[sell_signal, "signal"] = -1

        df.drop(columns=["prev_rsi"], inplace=True)

        return df

    def get_required_history(self) -> int:
        """RSI 계산을 위해 period * 2 일수 필요"""
        return self.period * 2

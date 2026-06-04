"""
이동평균 교차 전략 (Moving Average Crossover Strategy)

단기 이동평균이 장기 이동평균을 상향 돌파할 때 매수,
단기 이동평균이 장기 이동평균을 하향 돌파할 때 매도합니다.
"""

import pandas as pd
from typing import Dict, Any

from strategies.base import BaseStrategy


class MACrossoverStrategy(BaseStrategy):
    """이동평균 교차 전략"""

    DEFAULT_PARAMS = {
        "short_window": 5,
        "long_window": 20,
    }

    def __init__(self, params: Dict[str, Any] = None):
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)
        self.short_window = int(self.params["short_window"])
        self.long_window = int(self.params["long_window"])

    @property
    def name(self) -> str:
        return f"이동평균 교차 ({self.short_window}/{self.long_window})"

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        단기/장기 이동평균 교차 신호 생성

        Args:
            data: OHLCV DataFrame

        Returns:
            signal 컬럼이 추가된 DataFrame
        """
        df = data.copy()

        # 이동평균 계산
        df["short_ma"] = df["Close"].rolling(window=self.short_window).mean()
        df["long_ma"] = df["Close"].rolling(window=self.long_window).mean()

        # 신호 생성
        df["signal"] = 0

        # 이전 MA 값
        df["prev_short_ma"] = df["short_ma"].shift(1)
        df["prev_long_ma"] = df["long_ma"].shift(1)

        # 골든 크로스: 단기 MA가 장기 MA를 상향 돌파 -> 매수
        golden_cross = (
            (df["short_ma"] > df["long_ma"]) &
            (df["prev_short_ma"] <= df["prev_long_ma"])
        )
        df.loc[golden_cross, "signal"] = 1

        # 데드 크로스: 단기 MA가 장기 MA를 하향 돌파 -> 매도
        dead_cross = (
            (df["short_ma"] < df["long_ma"]) &
            (df["prev_short_ma"] >= df["prev_long_ma"])
        )
        df.loc[dead_cross, "signal"] = -1

        # 임시 컬럼 제거
        df.drop(columns=["prev_short_ma", "prev_long_ma"], inplace=True)

        return df

    def get_required_history(self) -> int:
        """장기 이동평균 계산을 위해 long_window 일수 필요"""
        return self.long_window + 1

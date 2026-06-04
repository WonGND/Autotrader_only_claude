"""
기술적 지표 계산 모듈
"""

import numpy as np
import pandas as pd
from typing import Tuple


class TechnicalIndicators:
    """기술적 지표 계산 클래스 (모든 메서드는 정적 메서드)"""

    @staticmethod
    def sma(data: pd.Series, window: int) -> pd.Series:
        """
        단순 이동평균 (Simple Moving Average)

        Args:
            data: 가격 시리즈
            window: 이동평균 기간

        Returns:
            SMA 시리즈
        """
        return data.rolling(window=window).mean()

    @staticmethod
    def ema(data: pd.Series, window: int) -> pd.Series:
        """
        지수 이동평균 (Exponential Moving Average)

        Args:
            data: 가격 시리즈
            window: EMA 기간

        Returns:
            EMA 시리즈
        """
        return data.ewm(span=window, adjust=False).mean()

    @staticmethod
    def rsi(data: pd.Series, period: int = 14) -> pd.Series:
        """
        상대 강도 지수 (Relative Strength Index)

        Args:
            data: 가격 시리즈
            period: RSI 기간 (기본값: 14)

        Returns:
            RSI 시리즈 (0~100)
        """
        delta = data.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)

        avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
        avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()

        rs = avg_gain / avg_loss.replace(0, np.inf)
        rsi = 100 - (100 / (1 + rs))
        return rsi

    @staticmethod
    def bollinger_bands(
        data: pd.Series, period: int = 20, std_dev: float = 2.0
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        볼린저 밴드 (Bollinger Bands)

        Args:
            data: 가격 시리즈
            period: 이동평균 기간 (기본값: 20)
            std_dev: 표준편차 배수 (기본값: 2.0)

        Returns:
            (상단 밴드, 중간 밴드, 하단 밴드) 튜플
        """
        middle = data.rolling(window=period).mean()
        std = data.rolling(window=period).std()
        upper = middle + std_dev * std
        lower = middle - std_dev * std
        return upper, middle, lower

    @staticmethod
    def macd(
        data: pd.Series,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        MACD (Moving Average Convergence Divergence)

        Args:
            data: 가격 시리즈
            fast: 단기 EMA 기간 (기본값: 12)
            slow: 장기 EMA 기간 (기본값: 26)
            signal: 시그널 라인 기간 (기본값: 9)

        Returns:
            (MACD 라인, 시그널 라인, 히스토그램) 튜플
        """
        ema_fast = data.ewm(span=fast, adjust=False).mean()
        ema_slow = data.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    @staticmethod
    def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        """
        평균 진폭 (Average True Range)

        Args:
            high: 고가 시리즈
            low: 저가 시리즈
            close: 종가 시리즈
            period: ATR 기간 (기본값: 14)

        Returns:
            ATR 시리즈
        """
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        return tr.ewm(com=period - 1, min_periods=period).mean()

    @staticmethod
    def stochastic(
        high: pd.Series, low: pd.Series, close: pd.Series,
        k_period: int = 14, d_period: int = 3
    ) -> Tuple[pd.Series, pd.Series]:
        """
        스토캐스틱 오실레이터

        Args:
            high: 고가 시리즈
            low: 저가 시리즈
            close: 종가 시리즈
            k_period: %K 기간
            d_period: %D 기간

        Returns:
            (%K, %D) 튜플
        """
        lowest_low = low.rolling(window=k_period).min()
        highest_high = high.rolling(window=k_period).max()
        k = 100 * (close - lowest_low) / (highest_high - lowest_low).replace(0, np.inf)
        d = k.rolling(window=d_period).mean()
        return k, d

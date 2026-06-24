"""
대안 진입 전략 후보 (워크포워드 탐색용)

기존 FuturesStrategy(SMA크로스+RSI+BB)는 평균회귀 성격이라 추세장 코인에서
워크포워드 forward edge가 없었다(2026-06-14 검증). 추세추종형 후보 3종을
동일 인터페이스(signal/signal_strength/atr/entry_basis 컬럼)로 구현한다.

  - DonchianBreakout : N일 채널 돌파 추종 (고전 추세추종)
  - MomentumStrategy : 시계열 모멘텀 (과거 N일 수익 부호)
  - AdxTrendStrategy : SMA크로스 + ADX 추세강도 필터 (횡보장 진입 차단)
"""

import numpy as np
import pandas as pd
from typing import Dict, Any

from strategies.base import BaseStrategy


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(span=period, min_periods=period).mean()


def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ADX(추세 강도) 계산. 0~100, 25 이상이면 추세 존재."""
    high, low, close = df["High"], df["Low"], df["Close"]
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    atr = tr.ewm(span=period, min_periods=period).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(span=period, min_periods=period).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(span=period, min_periods=period).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(span=period, min_periods=period).mean()


class DonchianBreakout(BaseStrategy):
    """돈치안 채널 돌파 추세추종. 롱/숏을 독립 파라미터로 분리 운용한다.

    - 롱: long_channel일 고가 상향 돌파 (enable_long일 때만)
    - 숏: short_channel일 저가 하향 이탈 (enable_short일 때만)
    롱과 숏의 채널·활성여부를 따로 지정해 방향별로 최적화/차단할 수 있다.
    """

    DEFAULT = {
        "long_channel": 20,
        "short_channel": 20,
        "enable_long": True,
        "enable_short": True,
        # 하위호환: channel 하나만 주면 롱/숏 공통값으로 적용
        "channel": None,
    }

    def __init__(self, params: Dict[str, Any] = None):
        super().__init__({**self.DEFAULT, **(params or {})})
        ch = self.params.get("channel")
        self.long_channel = int(ch if ch else self.params["long_channel"])
        self.short_channel = int(ch if ch else self.params["short_channel"])
        self.enable_long = bool(self.params["enable_long"])
        self.enable_short = bool(self.params["enable_short"])

    @property
    def name(self):
        l = f"L{self.long_channel}" if self.enable_long else "L-off"
        s = f"S{self.short_channel}" if self.enable_short else "S-off"
        return f"Donchian({l}/{s})"

    def get_required_history(self):
        return max(self.long_channel, self.short_channel) + 20

    def generate_signals(self, data):
        df = data.copy()
        df["atr"] = _atr(df)
        df["signal"] = 0
        df["entry_basis"] = ""
        if self.enable_long:
            upper = df["High"].rolling(self.long_channel).max().shift(1)
            long_mask = df["Close"] > upper
            df.loc[long_mask, "signal"] = 1
            df.loc[long_mask, "entry_basis"] = f"{self.long_channel}일 고가돌파"
        if self.enable_short:
            lower = df["Low"].rolling(self.short_channel).min().shift(1)
            short_mask = df["Close"] < lower
            # 롱/숏 동시 충족 시 롱 우선(이미 1로 세팅된 곳은 덮어쓰지 않음)
            short_only = short_mask & (df["signal"] == 0)
            df.loc[short_only, "signal"] = -1
            df.loc[short_only, "entry_basis"] = f"{self.short_channel}일 저가이탈"
        df["signal_strength"] = 2
        return df


class MomentumStrategy(BaseStrategy):
    """과거 lookback일 수익률 부호로 추세 추종 (시계열 모멘텀)."""

    DEFAULT = {"lookback": 30, "threshold": 0.0}

    def __init__(self, params: Dict[str, Any] = None):
        super().__init__({**self.DEFAULT, **(params or {})})
        self.lookback = int(self.params["lookback"])
        self.threshold = float(self.params["threshold"])

    @property
    def name(self):
        return f"Momentum(lb{self.lookback})"

    def get_required_history(self):
        return self.lookback + 20

    def generate_signals(self, data):
        df = data.copy()
        df["atr"] = _atr(df)
        mom = df["Close"].pct_change(self.lookback)
        df["signal"] = 0
        df.loc[mom > self.threshold, "signal"] = 1
        df.loc[mom < -self.threshold, "signal"] = -1
        df["signal_strength"] = 2
        df["entry_basis"] = np.where(df["signal"] == 1, f"{self.lookback}일 모멘텀+",
                                np.where(df["signal"] == -1, f"{self.lookback}일 모멘텀-", ""))
        return df


class AdxTrendStrategy(BaseStrategy):
    """SMA 크로스 + ADX 추세강도 필터. ADX<임계값(횡보장)이면 진입 안 함."""

    DEFAULT = {"short_window": 10, "long_window": 30, "adx_threshold": 25}

    def __init__(self, params: Dict[str, Any] = None):
        super().__init__({**self.DEFAULT, **(params or {})})
        self.short = int(self.params["short_window"])
        self.long = int(self.params["long_window"])
        self.adx_threshold = float(self.params["adx_threshold"])

    @property
    def name(self):
        return f"AdxTrend(MA{self.short}/{self.long},ADX{self.adx_threshold:.0f})"

    def get_required_history(self):
        return self.long + 30

    def generate_signals(self, data):
        df = data.copy()
        df["atr"] = _atr(df)
        df["adx"] = _adx(df)
        sma_s = df["Close"].rolling(self.short).mean()
        sma_l = df["Close"].rolling(self.long).mean()
        trend_up = sma_s > sma_l
        trend_dn = sma_s < sma_l
        strong = df["adx"] >= self.adx_threshold
        df["signal"] = 0
        df.loc[trend_up & strong, "signal"] = 1
        df.loc[trend_dn & strong, "signal"] = -1
        df["signal_strength"] = 2
        df["entry_basis"] = np.where(df["signal"] == 1, "ADX추세↑",
                                np.where(df["signal"] == -1, "ADX추세↓", ""))
        return df

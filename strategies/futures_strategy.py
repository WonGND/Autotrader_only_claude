"""
선물 거래 복합 신호 전략 (Futures Combined Signal Strategy)

MA 크로스오버 + RSI + 볼린저 밴드를 결합한 복합 신호 전략입니다.
롱(Long)과 숏(Short) 방향 모두 지원합니다.
"""

import numpy as np
import pandas as pd
from typing import Dict, Any, Tuple

from strategies.base import BaseStrategy
from utils.logger import get_logger

logger = get_logger(__name__)


class FuturesStrategy(BaseStrategy):
    """
    선물 복합 신호 전략

    신호:
        +1  = 롱 (매수)
        -1  = 숏 (매도)
         0  = 포지션 없음

    신호 강도:
        1 = 약함 (1개 지표 일치)
        2 = 보통 (2개 지표 일치)
        3 = 강함 (3개 지표 모두 일치)
    """

    DEFAULT_PARAMS: Dict[str, Any] = {
        "short_window": 5,
        "long_window": 20,
        "rsi_period": 14,
        "bb_period": 20,
        "bb_std": 2.0,
        "rsi_long_threshold": 65,   # RSI < 이 값이면 롱 조건 충족
        "rsi_short_threshold": 35,  # RSI > 이 값이면 숏 조건 충족
    }

    def __init__(self, params: Dict[str, Any] = None):
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)
        self.short_window = int(self.params["short_window"])
        self.long_window = int(self.params["long_window"])
        self.rsi_period = int(self.params["rsi_period"])
        self.bb_period = int(self.params["bb_period"])
        self.bb_std = float(self.params["bb_std"])
        self.rsi_long_threshold = float(self.params["rsi_long_threshold"])
        self.rsi_short_threshold = float(self.params["rsi_short_threshold"])

    @property
    def name(self) -> str:
        return (
            f"선물복합전략 (MA{self.short_window}/{self.long_window}"
            f"+RSI{self.rsi_period}+BB{self.bb_period})"
        )

    def _calculate_rsi(self, close: pd.Series, period: int) -> pd.Series:
        """RSI 계산"""
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
        avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        return rsi.fillna(50)

    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """ATR (Average True Range) 계산"""
        high = df["High"]
        low = df["Low"]
        close = df["Close"]
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        return tr.ewm(span=period, min_periods=period).mean()

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        복합 신호 생성 (롱/숏)

        Returns:
            signal, signal_strength, short_ma, long_ma, rsi, bb_upper, bb_middle, bb_lower, atr 컬럼이 추가된 DataFrame
        """
        df = data.copy()

        # ── 이동평균 ──────────────────────────────────────────────
        df["short_ma"] = df["Close"].rolling(window=self.short_window).mean()
        df["long_ma"] = df["Close"].rolling(window=self.long_window).mean()

        # ── RSI ───────────────────────────────────────────────────
        df["rsi"] = self._calculate_rsi(df["Close"], self.rsi_period)

        # ── 볼린저 밴드 ───────────────────────────────────────────
        df["bb_middle"] = df["Close"].rolling(window=self.bb_period).mean()
        bb_std = df["Close"].rolling(window=self.bb_period).std()
        df["bb_upper"] = df["bb_middle"] + self.bb_std * bb_std
        df["bb_lower"] = df["bb_middle"] - self.bb_std * bb_std

        # ── ATR ───────────────────────────────────────────────────
        if "High" in df.columns and "Low" in df.columns:
            df["atr"] = self._calculate_atr(df)
        else:
            # High/Low 없을 때 Close 기반 근사값
            df["atr"] = df["Close"].rolling(14).std()

        # ── 신호 생성 ─────────────────────────────────────────────
        df["signal"] = 0
        df["signal_strength"] = 0

        for i in range(len(df)):
            row = df.iloc[i]

            # NaN 검사
            if pd.isna(row["short_ma"]) or pd.isna(row["long_ma"]) or \
               pd.isna(row["rsi"]) or pd.isna(row["bb_middle"]):
                continue

            price = row["Close"]
            short_ma = row["short_ma"]
            long_ma = row["long_ma"]
            rsi = row["rsi"]
            bb_upper = row["bb_upper"]
            bb_middle = row["bb_middle"]
            bb_lower = row["bb_lower"]

            # ── 롱 조건 평가 ──────────────────────────────────────
            # 1) MA 골든 크로스 (단기 > 장기)
            ma_long = short_ma > long_ma
            # 2) RSI < 롱 임계값 (과매수 아님)
            rsi_long = rsi < self.rsi_long_threshold
            # 3) 가격이 BB 중간선 위 또는 BB 하단에서 반등
            bb_long = (price > bb_middle) or (price < bb_lower * 1.01)

            # ── 숏 조건 평가 ──────────────────────────────────────
            # 1) MA 데드 크로스 (단기 < 장기)
            ma_short = short_ma < long_ma
            # 2) RSI > 숏 임계값 (과매도 아님)
            rsi_short = rsi > self.rsi_short_threshold
            # 3) 가격이 BB 중간선 아래 또는 BB 상단에서 거부
            bb_short = (price < bb_middle) or (price > bb_upper * 0.99)

            long_score = int(ma_long) + int(rsi_long) + int(bb_long)
            short_score = int(ma_short) + int(rsi_short) + int(bb_short)

            # 최소 2개 지표 일치 시 신호 발생
            if long_score >= 2 and long_score > short_score:
                df.iloc[i, df.columns.get_loc("signal")] = 1
                df.iloc[i, df.columns.get_loc("signal_strength")] = long_score
            elif short_score >= 2 and short_score > long_score:
                df.iloc[i, df.columns.get_loc("signal")] = -1
                df.iloc[i, df.columns.get_loc("signal_strength")] = short_score

        logger.debug(
            f"신호 생성 완료: 롱 {(df['signal'] == 1).sum()}건, "
            f"숏 {(df['signal'] == -1).sum()}건"
        )
        return df

    def get_required_history(self) -> int:
        """전략 계산에 필요한 최소 과거 데이터 일수"""
        return max(self.long_window, self.bb_period, self.rsi_period) + 5

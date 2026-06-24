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


# ── 코인 종목별 돈치안 파라미터 (2026-06-14 워크포워드 전략전환) ──────
# 진입전략을 SMA크로스 → DonchianBreakout으로 전환(워크포워드 WFE +1.73 vs
# SMA -0.26). 롱/숏을 독립 채널로 분리 운용한다.
# 채널값: 종목별·방향별 워크포워드 결과 반영. 단 폴드가 짧아 일부 방향이
# 노이즈로 '비활성' 판정 → 양방향 유지하되 미검출 방향은 안전기본값 20.
# DROP된 종목(ATOM/DOT/TRX/SOL)은 OOS 견고성 미확보로 유니버스 제외.
COIN_SYMBOL_PARAMS: Dict[str, Dict[str, int]] = {
    "BTC-USD":  {"long_channel": 20, "short_channel": 10},
    "ETH-USD":  {"long_channel": 20, "short_channel": 20},
    "BNB-USD":  {"long_channel": 40, "short_channel": 20},
    "SOL-USD":  {"long_channel": 40, "short_channel": 10},
    "XRP-USD":  {"long_channel": 20, "short_channel": 10},
    "DOGE-USD": {"long_channel": 30, "short_channel": 20},
    "ADA-USD":  {"long_channel": 30, "short_channel": 10},
    "AVAX-USD": {"long_channel": 20, "short_channel": 10},
    # 2026-06-19: 5라운드 백테스트(IS/OOS→3구간 워크포워드→신호지연) 결과
    # L30/S20은 730일 수익 음수(-3.6%/-6.1%). L18/S5는 3구간 모두 우위 +
    # 1봉 지연에도 +5.7% 유지(유일하게 견고)로 단축 채택. MDD -19%→-13%.
    "LINK-USD": {"long_channel": 18, "short_channel": 5},
    "DOT-USD":  {"long_channel": 20, "short_channel": 20},
}


def get_coin_params(symbol: str) -> Dict[str, int]:
    """종목별 돈치안 파라미터 반환. 없으면 빈 dict(=전략 기본값 사용)."""
    return dict(COIN_SYMBOL_PARAMS.get(symbol, {}))


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
        "short_window": 10,
        # 2026-06-14 재최적화: 30→40. 8종목 IS/OOS 격자분석(run_coin_param_optimization.py)
        # 결과 글로벌 최적이 (10,40) — 평균min(IS,OOS) -1.43%로 (10,30)의 -1.81%보다 개선,
        # 견고종목 3/8→4/8. ※ 단, 어떤 글로벌값도 8종목 평균은 음수 — 단일 글로벌
        # 파라미터를 이질적 코인 전체에 강제하는 구조의 한계. 근본 개선은 종목별
        # 파라미터화 또는 유니버스 축소(견고한 4종목만 운용) 필요.
        "long_window": 40,
        "rsi_period": 14,
        "bb_period": 20,
        "bb_std": 2.0,
        "rsi_long_threshold": 60,
        "rsi_short_threshold": 40,
        # 추세 필터: 100 MA 기준으로 노이즈 감소
        "trend_window": 100,
        # 거래량 필터: 1.0 = 비활성화 (눌림목은 원래 거래량이 낮음)
        "volume_factor": 1.0,
        # 최소 신호 강도: 2 = 2/3개 지표 일치, 3 = 전부 일치
        "min_signal_strength": 2,
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
        self.trend_window = int(self.params["trend_window"])
        self.volume_factor = float(self.params["volume_factor"])
        self.min_signal_strength = int(self.params["min_signal_strength"])

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
            df["atr"] = df["Close"].rolling(14).std()

        # ── 추세 필터 (50 MA) ─────────────────────────────────────
        df["trend_ma"] = df["Close"].rolling(window=self.trend_window).mean()

        # ── 거래량 평균 (필터용) ──────────────────────────────────
        if "Volume" in df.columns:
            df["vol_avg"] = df["Volume"].rolling(window=20).mean()
        else:
            df["vol_avg"] = np.nan

        # ── 신호 생성 ─────────────────────────────────────────────
        df["signal"] = 0
        df["signal_strength"] = 0
        df["entry_basis"] = ""

        for i in range(len(df)):
            row = df.iloc[i]

            # NaN 검사 (trend_ma 포함)
            if pd.isna(row["short_ma"]) or pd.isna(row["long_ma"]) or \
               pd.isna(row["rsi"]) or pd.isna(row["bb_middle"]) or \
               pd.isna(row["trend_ma"]):
                continue

            price     = row["Close"]
            short_ma  = row["short_ma"]
            long_ma   = row["long_ma"]
            rsi       = row["rsi"]
            bb_upper  = row["bb_upper"]
            bb_middle = row["bb_middle"]
            bb_lower  = row["bb_lower"]
            trend_ma  = row["trend_ma"]

            # ── 거래량 필터 ───────────────────────────────────────
            vol_ok = True
            if self.volume_factor > 1.0 and "Volume" in row.index and \
               not pd.isna(row.get("vol_avg", np.nan)) and row["vol_avg"] > 0:
                vol_ok = row["Volume"] >= row["vol_avg"] * self.volume_factor

            if not vol_ok:
                continue

            # ── 추세 방향 판단 ────────────────────────────────────
            # 50 MA 기준: 가격이 위면 상승 추세, 아래면 하락 추세
            uptrend   = price > trend_ma
            downtrend = price < trend_ma

            # ── 롱 조건 평가 ──────────────────────────────────────
            # 상승추세(50MA 위) 안에서 되돌림(midband 이하) 구간에만 진입
            # — 추세 상단에서 쫓아 사지 않고 눌림목만 매수
            if not uptrend:
                long_score = 0
                ma_long = rsi_long = bb_long = False
            else:
                # 1) 단기 MA > 장기 MA (모멘텀 확인)
                ma_long  = short_ma > long_ma
                # 2) RSI < 60 (과매수 아님)
                rsi_long = rsi < self.rsi_long_threshold
                # 3) 가격이 BB 중간선(20MA) 이하 — 눌림목 진입 조건
                bb_long  = price <= bb_middle
                long_score = int(ma_long) + int(rsi_long) + int(bb_long)

            # ── 숏 조건 평가 ──────────────────────────────────────
            # 하락추세(50MA 아래) 안에서 반등(midband 이상) 구간에만 진입
            # — 추세 하단에서 쫓아 팔지 않고 반등 구간만 매도
            if not downtrend:
                short_score = 0
                ma_short = rsi_short = bb_short = False
            else:
                # 1) 단기 MA < 장기 MA (하락 모멘텀)
                ma_short  = short_ma < long_ma
                # 2) RSI > 40 (과매도 아님)
                rsi_short = rsi > self.rsi_short_threshold
                # 3) 가격이 BB 중간선(20MA) 이상 — 반등 매도 조건
                bb_short  = price >= bb_middle
                short_score = int(ma_short) + int(rsi_short) + int(bb_short)

            # 최소 min_signal_strength개 지표 일치 시 신호 발생
            if long_score >= self.min_signal_strength and long_score > short_score:
                df.iloc[i, df.columns.get_loc("signal")] = 1
                df.iloc[i, df.columns.get_loc("signal_strength")] = long_score
                triggered = ["추세↑"]
                if ma_long:  triggered.append("MA골든크로스")
                if rsi_long: triggered.append(f"RSI={rsi:.0f}(기준{self.rsi_long_threshold:.0f}미만)")
                if bb_long:  triggered.append("BB지지")
                df.iloc[i, df.columns.get_loc("entry_basis")] = " + ".join(triggered)
            elif short_score >= self.min_signal_strength and short_score > long_score:
                df.iloc[i, df.columns.get_loc("signal")] = -1
                df.iloc[i, df.columns.get_loc("signal_strength")] = short_score
                triggered = ["추세↓"]
                if ma_short:  triggered.append("MA데드크로스")
                if rsi_short: triggered.append(f"RSI={rsi:.0f}(기준{self.rsi_short_threshold:.0f}초과)")
                if bb_short:  triggered.append("BB저항")
                df.iloc[i, df.columns.get_loc("entry_basis")] = " + ".join(triggered)

        logger.debug(
            f"신호 생성 완료: 롱 {(df['signal'] == 1).sum()}건, "
            f"숏 {(df['signal'] == -1).sum()}건"
        )
        return df

    def get_required_history(self) -> int:
        """전략 계산에 필요한 최소 과거 데이터 일수"""
        return max(self.long_window, self.bb_period, self.rsi_period, self.trend_window) + 5

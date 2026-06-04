"""
시장 데이터 수집 모듈

한국 주식은 FinanceDataReader, 미국 주식은 yfinance를 사용합니다.
메모리 캐시를 통해 반복 요청을 최소화합니다.
"""

import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Tuple
import pandas as pd

from utils.logger import get_logger
from utils.helpers import is_korean_stock

logger = get_logger(__name__)


class DataFetcher:
    """
    시장 데이터 수집 클래스

    FinanceDataReader(한국 주식)와 yfinance(미국 주식)를 지원합니다.
    """

    CACHE_TTL = 300  # 5분 캐시

    def __init__(self, provider: str = "fdr"):
        self.provider = provider
        self._cache: Dict[str, Tuple[pd.DataFrame, float]] = {}  # key -> (df, timestamp)

    def _cache_key(self, symbol: str, start_date: str, end_date: str) -> str:
        return f"{symbol}_{start_date}_{end_date}"

    def _is_cache_valid(self, key: str) -> bool:
        if key not in self._cache:
            return False
        _, ts = self._cache[key]
        return time.time() - ts < self.CACHE_TTL

    def get_ohlcv(
        self,
        symbol: str,
        start_date: str,
        end_date: str = None,
    ) -> pd.DataFrame:
        """
        OHLCV 데이터를 가져옵니다.

        Args:
            symbol: 종목 코드 (예: "005930" 또는 "AAPL")
            start_date: 시작일 (YYYY-MM-DD)
            end_date: 종료일 (YYYY-MM-DD, 기본값: 오늘)

        Returns:
            OHLCV DataFrame (Open, High, Low, Close, Volume)
        """
        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")

        cache_key = self._cache_key(symbol, start_date, end_date)
        if self._is_cache_valid(cache_key):
            logger.debug(f"{symbol} 캐시 데이터 사용")
            df, _ = self._cache[cache_key]
            return df.copy()

        try:
            if is_korean_stock(symbol):
                df = self._fetch_korean(symbol, start_date, end_date)
            else:
                df = self._fetch_us(symbol, start_date, end_date)

            if df is not None and not df.empty:
                self._cache[cache_key] = (df, time.time())
                logger.info(f"{symbol} 데이터 로드 완료: {len(df)}일치")
                return df.copy()
            else:
                logger.warning(f"{symbol} 데이터를 가져오지 못했습니다.")
                return pd.DataFrame()

        except Exception as e:
            logger.error(f"{symbol} 데이터 수집 실패: {e}")
            return pd.DataFrame()

    def _fetch_korean(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """FinanceDataReader로 한국 주식 데이터 수집"""
        try:
            import FinanceDataReader as fdr
            df = fdr.DataReader(symbol, start_date, end_date)
            if df.empty:
                return pd.DataFrame()
            # 컬럼 정규화
            df = df.rename(columns={
                "Open": "Open", "High": "High", "Low": "Low",
                "Close": "Close", "Volume": "Volume"
            })
            # 필요한 컬럼만 선택
            cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
            df = df[cols]
            df.index = pd.to_datetime(df.index)
            df = df.sort_index()
            return df
        except Exception as e:
            logger.warning(f"FDR 수집 실패 ({symbol}): {e}, yfinance로 시도합니다.")
            return self._fetch_us(f"{symbol}.KS", start_date, end_date)

    def _fetch_us(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """yfinance로 미국 주식 (또는 한국 주식 .KS/.KQ) 데이터 수집"""
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            df = ticker.history(start=start_date, end=end_date, auto_adjust=True)
            if df.empty:
                return pd.DataFrame()
            df = df.rename(columns={
                "Open": "Open", "High": "High", "Low": "Low",
                "Close": "Close", "Volume": "Volume"
            })
            cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
            df = df[cols]
            df.index = pd.to_datetime(df.index).tz_localize(None)
            df = df.sort_index()
            return df
        except Exception as e:
            logger.error(f"yfinance 수집 실패 ({symbol}): {e}")
            return pd.DataFrame()

    def get_current_price(self, symbol: str) -> float:
        """
        현재가를 가져옵니다.

        Args:
            symbol: 종목 코드

        Returns:
            현재가 (float), 실패 시 0.0
        """
        try:
            # 오늘 포함한 최근 5일치 데이터로 현재가 추정
            end_date = datetime.now().strftime("%Y-%m-%d")
            start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            df = self.get_ohlcv(symbol, start_date, end_date)
            if not df.empty:
                return float(df["Close"].iloc[-1])
            return 0.0
        except Exception as e:
            logger.error(f"{symbol} 현재가 조회 실패: {e}")
            return 0.0

    def clear_cache(self):
        """캐시 초기화"""
        self._cache.clear()
        logger.info("데이터 캐시가 초기화되었습니다.")

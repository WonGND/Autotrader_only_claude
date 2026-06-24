"""
매크로 데이터 수집 모듈

- Crypto Fear & Greed Index (alternative.me, 무료, 2018~현재)
- VIX 미국 공포지수 (yfinance)
- BTC 도미넌스 근사치 (yfinance BTC 시총 기반)
"""

import os
import json
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)

CACHE_DIR = Path(__file__).parent.parent / "data" / "macro_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


class MacroDataFetcher:
    """
    매크로 지표 데이터 수집기

    백테스트용 히스토리컬 데이터 + 실거래용 실시간 데이터를 모두 지원합니다.
    결과는 로컬 캐시에 저장해 API 호출 횟수를 최소화합니다.
    """

    FNG_API = "https://api.alternative.me/fng/"
    FNG_CACHE = CACHE_DIR / "fear_greed.parquet"
    FNG_CACHE_DAYS = 1  # 하루마다 갱신

    def _get_fear_greed_raw(self, limit: int = 2000) -> pd.DataFrame:
        """Fear & Greed Index 원본 데이터 수집"""
        url = f"{self.FNG_API}?limit={limit}&format=json&date_format=world"
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()["data"]
            df = pd.DataFrame(data)
            df["timestamp"] = pd.to_datetime(df["timestamp"], format="%d-%m-%Y")
            df = df.rename(columns={"value": "fg_value", "value_classification": "fg_class"})
            df["fg_value"] = df["fg_value"].astype(int)
            df = df[["timestamp", "fg_value", "fg_class"]].sort_values("timestamp").reset_index(drop=True)
            return df
        except Exception as e:
            logger.error(f"Fear & Greed 데이터 수집 실패: {e}")
            return pd.DataFrame()

    def get_fear_greed(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """
        Fear & Greed Index 데이터 반환

        Args:
            start_date: "YYYY-MM-DD"
            end_date:   "YYYY-MM-DD"
            use_cache:  True면 캐시 사용

        Returns:
            timestamp, fg_value(0~100), fg_class 컬럼 DataFrame
        """
        # 캐시 확인
        if use_cache and self.FNG_CACHE.exists():
            age = (datetime.now() - datetime.fromtimestamp(self.FNG_CACHE.stat().st_mtime)).days
            if age < self.FNG_CACHE_DAYS:
                df = pd.read_parquet(self.FNG_CACHE)
                logger.debug(f"Fear & Greed 캐시 사용 ({len(df)}행)")
            else:
                df = self._get_fear_greed_raw()
                if not df.empty:
                    df.to_parquet(self.FNG_CACHE, index=False)
        else:
            df = self._get_fear_greed_raw()
            if not df.empty:
                df.to_parquet(self.FNG_CACHE, index=False)

        if df.empty:
            return df

        df = df.set_index("timestamp")
        if start_date:
            df = df[df.index >= pd.Timestamp(start_date)]
        if end_date:
            df = df[df.index <= pd.Timestamp(end_date)]
        return df.reset_index()

    def get_vix(
        self,
        start_date: str,
        end_date: str,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """
        VIX (CBOE 변동성 지수) 데이터 반환

        Args:
            start_date / end_date: "YYYY-MM-DD"

        Returns:
            Date, vix_close 컬럼 DataFrame
        """
        cache_file = CACHE_DIR / f"vix_{start_date}_{end_date}.parquet"

        if use_cache and cache_file.exists():
            return pd.read_parquet(cache_file)

        try:
            import yfinance as yf
            vix = yf.download("^VIX", start=start_date, end=end_date, progress=False, auto_adjust=True)
            if vix.empty:
                logger.warning("VIX 데이터 없음")
                return pd.DataFrame()
            df = pd.DataFrame({
                "Date": pd.to_datetime(vix.index.get_level_values(0) if hasattr(vix.index, 'levels') else vix.index),
                "vix_close": vix["Close"].values.flatten(),
            })
            df = df.dropna().reset_index(drop=True)
            df.to_parquet(cache_file, index=False)
            return df
        except Exception as e:
            logger.error(f"VIX 데이터 수집 실패: {e}")
            return pd.DataFrame()

    def get_btc_dominance_proxy(
        self,
        start_date: str,
        end_date: str,
        window: int = 14,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """
        BTC 상대 강도를 이용한 도미넌스 근사치

        ETH, SOL, BNB 대비 BTC 가격 변화율 차이로 도미넌스 방향을 추정합니다.
        - btc_rel_strength > 0  → BTC 도미넌스 상승 추세 (알트 약세)
        - btc_rel_strength < 0  → 알트 강세 / BTC 도미넌스 하락

        Returns:
            Date, btc_rel_strength(-100~+100) 컬럼 DataFrame
        """
        cache_file = CACHE_DIR / f"btc_dom_{start_date}_{end_date}.parquet"
        if use_cache and cache_file.exists():
            return pd.read_parquet(cache_file)

        try:
            import yfinance as yf
            tickers = ["BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD"]
            data = {}
            for t in tickers:
                raw = yf.download(t, start=start_date, end=end_date, progress=False, auto_adjust=True)
                if not raw.empty:
                    close_col = raw["Close"]
                    if hasattr(close_col, 'iloc'):
                        data[t] = close_col.squeeze()
                    else:
                        data[t] = close_col

            if "BTC-USD" not in data or len(data) < 2:
                return pd.DataFrame()

            prices = pd.DataFrame(data).dropna()
            returns = prices.pct_change(window).dropna()

            btc_ret = returns["BTC-USD"]
            alt_rets = [returns[c] for c in returns.columns if c != "BTC-USD"]
            alt_avg = pd.concat(alt_rets, axis=1).mean(axis=1)

            rel = (btc_ret - alt_avg) * 100
            rel = rel.clip(-100, 100)

            df = pd.DataFrame({"Date": prices.index[window:], "btc_rel_strength": rel.values})
            df = df.dropna().reset_index(drop=True)
            df.to_parquet(cache_file, index=False)
            return df

        except Exception as e:
            logger.error(f"BTC 도미넌스 근사치 수집 실패: {e}")
            return pd.DataFrame()

    def get_combined(
        self,
        start_date: str,
        end_date: str,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """
        모든 매크로 지표를 날짜 기준으로 병합

        Returns:
            Date(index), fg_value, fg_class, vix_close, btc_rel_strength 컬럼 DataFrame
        """
        logger.info("매크로 데이터 수집 중...")

        fg  = self.get_fear_greed(start_date, end_date, use_cache)
        vix = self.get_vix(start_date, end_date, use_cache)
        dom = self.get_btc_dominance_proxy(start_date, end_date, use_cache=use_cache)

        # 날짜 인덱스 생성
        date_range = pd.date_range(start=start_date, end=end_date, freq="D")
        base = pd.DataFrame(index=date_range)
        base.index.name = "Date"

        if not fg.empty:
            fg_indexed = fg.set_index("timestamp")[["fg_value", "fg_class"]]
            fg_indexed.index.name = "Date"
            base = base.join(fg_indexed, how="left")

        if not vix.empty:
            vix_indexed = vix.set_index("Date")[["vix_close"]]
            base = base.join(vix_indexed, how="left")

        if not dom.empty:
            dom_indexed = dom.set_index("Date")[["btc_rel_strength"]]
            base = base.join(dom_indexed, how="left")

        # 주말/공휴일 등 결측값은 전날 값으로 채움 (ffill)
        base = base.ffill().bfill()

        # 기본값: F&G=50, VIX=20, dom=0 (데이터 없을 때)
        if "fg_value" not in base.columns:
            base["fg_value"] = 50
            base["fg_class"] = "Neutral"
        if "vix_close" not in base.columns:
            base["vix_close"] = 20.0
        if "btc_rel_strength" not in base.columns:
            base["btc_rel_strength"] = 0.0

        logger.info(f"매크로 데이터 준비 완료: {len(base)}일치")
        return base


# 전역 싱글톤 (데이터 중복 다운로드 방지)
_macro_cache: dict = {}


def get_macro_data(start_date: str, end_date: str) -> pd.DataFrame:
    """캐시된 매크로 데이터 반환 (백테스터에서 호출)"""
    key = f"{start_date}_{end_date}"
    if key not in _macro_cache:
        fetcher = MacroDataFetcher()
        _macro_cache[key] = fetcher.get_combined(start_date, end_date)
    return _macro_cache[key]

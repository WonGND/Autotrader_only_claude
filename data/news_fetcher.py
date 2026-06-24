"""
뉴스 감성 분석 모듈 (실거래용)

RSS 피드에서 최신 뉴스를 수집하고 Claude API로 감성을 분석합니다.
백테스트에서는 사용하지 않고, 실거래 엔진(trader/engine.py)에서만 호출합니다.
"""

import os
import json
import time
import hashlib
import feedparser
import anthropic
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from utils.logger import get_logger

logger = get_logger(__name__)

CACHE_DIR = Path(__file__).parent.parent / "data" / "news_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 코인별 RSS 피드 URL
COIN_RSS_FEEDS = {
    "BTC": [
        "https://cointelegraph.com/rss/tag/bitcoin",
        "https://cointelegraph.com/rss",
    ],
    "ETH": [
        "https://cointelegraph.com/rss/tag/ethereum",
        "https://cointelegraph.com/rss",
    ],
    "XRP": [
        "https://cointelegraph.com/rss",   # XRP는 전체 피드에서 키워드로 필터
        "https://cryptonews.com/news/feed/cryptocurrency-news/ripple/",
    ],
    "BNB": [
        "https://cointelegraph.com/rss/tag/bnb",
        "https://cointelegraph.com/rss",
    ],
    "SOL": [
        "https://cointelegraph.com/rss/tag/solana",
        "https://cointelegraph.com/rss",
    ],
    "DEFAULT": [
        "https://cointelegraph.com/rss",
        "https://cryptonews.com/news/feed",
    ],
}

# 일반 시장 RSS
MARKET_RSS_FEEDS = [
    "https://feeds.bloomberg.com/markets/news.rss",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",  # CNBC Markets
    "https://finance.yahoo.com/news/rssindex",
]


class NewsFetcher:
    """
    실거래용 실시간 뉴스 감성 분석기

    1. RSS 피드에서 최근 24시간 뉴스 수집
    2. Claude API로 감성 점수(-100~+100) 분석
    3. 결과를 6시간 캐시에 저장
    """

    CACHE_HOURS = 6  # 캐시 유효 시간
    MAX_HEADLINES = 10  # Claude에 전달할 최대 헤드라인 수

    def __init__(self, api_key: Optional[str] = None):
        api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if api_key:
            self.client = anthropic.Anthropic(api_key=api_key)
        else:
            self.client = None
            logger.warning("ANTHROPIC_API_KEY 없음 — 뉴스 감성 분석 비활성화")

    def _fetch_headlines(self, urls: List[str], hours: int = 24) -> List[str]:
        """RSS 피드에서 최근 N시간 이내 헤드라인 수집"""
        headlines = []
        cutoff = datetime.utcnow() - timedelta(hours=hours)

        for url in urls:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries:
                    # 발행 시간 파싱
                    pub = getattr(entry, "published_parsed", None)
                    if pub:
                        pub_dt = datetime(*pub[:6])
                        if pub_dt < cutoff:
                            continue
                    title = getattr(entry, "title", "").strip()
                    if title and len(title) > 10:
                        headlines.append(title)
            except Exception as e:
                logger.debug(f"RSS 수집 실패 ({url}): {e}")

        return list(dict.fromkeys(headlines))  # 중복 제거

    def _analyze_with_claude(self, headlines: List[str], coin: str) -> dict:
        """Claude API를 통한 감성 분석"""
        if not self.client or not headlines:
            return {"score": 0, "reasoning": "분석 불가", "bullish": [], "bearish": []}

        prompt = f"""다음은 최근 24시간 {coin} 관련 뉴스 헤드라인입니다.
각 헤드라인을 분석하여 {coin} 가격에 미칠 영향을 평가해주세요.

헤드라인:
{chr(10).join(f'- {h}' for h in headlines[:self.MAX_HEADLINES])}

다음 JSON 형식으로만 응답하세요:
{{
  "score": <-100(극단 하락)에서 +100(극단 상승) 사이 정수>,
  "reasoning": "<50자 이내 핵심 근거>",
  "bullish": ["<긍정 헤드라인 1>", "<긍정 헤드라인 2>"],
  "bearish": ["<부정 헤드라인 1>", "<부정 헤드라인 2>"]
}}"""

        try:
            response = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            # JSON 추출
            start = text.find("{")
            end   = text.rfind("}") + 1
            return json.loads(text[start:end])
        except Exception as e:
            logger.error(f"Claude 감성 분석 실패: {e}")
            return {"score": 0, "reasoning": "분석 오류", "bullish": [], "bearish": []}

    def _cache_path(self, coin: str) -> Path:
        return CACHE_DIR / f"sentiment_{coin}.json"

    def _load_cache(self, coin: str) -> Optional[dict]:
        path = self._cache_path(coin)
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            cached_at = datetime.fromisoformat(data["cached_at"])
            if datetime.utcnow() - cached_at > timedelta(hours=self.CACHE_HOURS):
                return None  # 캐시 만료
            return data
        except Exception:
            return None

    def _save_cache(self, coin: str, result: dict):
        result["cached_at"] = datetime.utcnow().isoformat()
        with open(self._cache_path(coin), "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    def get_sentiment(self, symbol: str, use_cache: bool = True) -> dict:
        """
        심볼(예: BTC-USD)의 뉴스 감성 점수 반환

        Returns:
            {
              "score":      int (-100 ~ +100),
              "reasoning":  str,
              "bullish":    list[str],
              "bearish":    list[str],
              "cached_at":  str,
              "headlines_count": int,
            }
        """
        coin = symbol.split("-")[0].upper()

        # 캐시 확인
        if use_cache:
            cached = self._load_cache(coin)
            if cached:
                logger.debug(f"{coin} 뉴스 감성 캐시 사용 (score={cached['score']})")
                return cached

        # 피드 URL 결정
        urls = COIN_RSS_FEEDS.get(coin, COIN_RSS_FEEDS["DEFAULT"])
        headlines = self._fetch_headlines(urls)
        logger.info(f"{coin} 헤드라인 {len(headlines)}건 수집")

        result = self._analyze_with_claude(headlines, coin)
        result["headlines_count"] = len(headlines)

        self._save_cache(coin, result)
        return result

    def get_market_sentiment(self, use_cache: bool = True) -> dict:
        """전체 시장(미국 시장 포함) 뉴스 감성 반환"""
        if use_cache:
            cached = self._load_cache("MARKET")
            if cached:
                return cached

        headlines = self._fetch_headlines(MARKET_RSS_FEEDS)
        result = self._analyze_with_claude(headlines, "전체 금융시장")
        result["headlines_count"] = len(headlines)
        self._save_cache("MARKET", result)
        return result


class NewsAdjustedSignal:
    """
    뉴스 감성 + 매크로 필터를 합산해 최종 레버리지 조정 배율 반환

    사용 예시 (실거래 엔진에서):
        nas = NewsAdjustedSignal()
        modifier = nas.get_leverage_modifier("BTC-USD")
        final_leverage = base_leverage * modifier
    """

    def __init__(self):
        self.news_fetcher = NewsFetcher()

    def get_leverage_modifier(
        self,
        symbol: str,
        macro_modifier: float = 1.0,
    ) -> float:
        """
        뉴스 감성 점수와 매크로 배율을 합산한 최종 레버리지 배율

        Returns:
            0.3 ~ 1.0 범위의 float
        """
        sentiment = self.news_fetcher.get_sentiment(symbol)
        score = sentiment.get("score", 0)

        # 뉴스 감성 배율 계산
        if score >= 60:
            news_modifier = 1.0   # 강한 호재: 정상
        elif score >= 30:
            news_modifier = 0.9   # 약한 호재: 소폭 유지
        elif score >= -20:
            news_modifier = 0.8   # 중립: 약간 보수적
        elif score >= -50:
            news_modifier = 0.6   # 부정: 포지션 축소
        else:
            news_modifier = 0.3   # 강한 악재: 대폭 축소

        final = macro_modifier * news_modifier
        final = max(0.25, min(1.0, final))

        logger.info(
            f"{symbol} 레버리지 배율: 뉴스={news_modifier:.2f} × 매크로={macro_modifier:.2f} = {final:.2f}"
            f" (뉴스점수={score}, 근거: {sentiment.get('reasoning', '')})"
        )
        return final

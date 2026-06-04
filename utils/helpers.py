"""
유틸리티 헬퍼 함수 모음
"""

import os
import yaml
from datetime import datetime, time
from typing import List, Optional
import pytz

from utils.logger import get_logger

logger = get_logger(__name__)

# 한국 시간대
KST = pytz.timezone("Asia/Seoul")

# 설정 파일 경로
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(BASE_DIR, "config")


def format_currency(amount: float, currency: str = "KRW") -> str:
    """
    금액을 통화 형식으로 포매팅합니다.

    Args:
        amount: 금액
        currency: 통화 코드 (기본값: KRW)

    Returns:
        포매팅된 문자열 (예: "₩10,000,000")
    """
    if currency == "KRW":
        return f"₩{amount:,.0f}"
    elif currency == "USD":
        return f"${amount:,.2f}"
    else:
        return f"{amount:,.2f} {currency}"


def format_pct(value: float, decimal_places: int = 2) -> str:
    """
    값을 퍼센트 형식으로 포매팅합니다.

    Args:
        value: 퍼센트 값 (예: 0.05 = 5%)
        decimal_places: 소수점 자리수

    Returns:
        포매팅된 문자열 (예: "+5.00%")
    """
    pct = value * 100
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.{decimal_places}f}%"


def get_market_status() -> bool:
    """
    현재 한국 주식 시장이 열려있는지 확인합니다.

    Returns:
        True if 시장이 열려있음, False otherwise
    """
    now = datetime.now(KST)

    # 주말 확인
    if now.weekday() >= 5:  # 토요일(5), 일요일(6)
        return False

    # 거래 시간 확인 (09:00 ~ 15:30 KST)
    market_open = time(9, 0)
    market_close = time(15, 30)
    current_time = now.time()

    return market_open <= current_time <= market_close


def get_trading_symbols(strategy_name: str) -> List[str]:
    """
    전략에 해당하는 종목 코드 목록을 반환합니다.

    Args:
        strategy_name: 전략 이름

    Returns:
        종목 코드 목록
    """
    try:
        strategies_file = os.path.join(CONFIG_DIR, "strategies.yaml")
        with open(strategies_file, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        strategy_config = config.get("strategies", {}).get(strategy_name, {})
        return strategy_config.get("watchlist", [])
    except Exception as e:
        logger.error(f"전략 종목 로드 실패: {e}")
        return []


def load_settings() -> dict:
    """
    메인 설정 파일을 로드합니다.

    Returns:
        설정 딕셔너리
    """
    settings_file = os.path.join(CONFIG_DIR, "settings.yaml")
    try:
        with open(settings_file, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception as e:
        logger.error(f"설정 파일 로드 실패: {e}")
        return {}


def load_strategies_config() -> dict:
    """
    전략 설정 파일을 로드합니다.

    Returns:
        전략 설정 딕셔너리
    """
    strategies_file = os.path.join(CONFIG_DIR, "strategies.yaml")
    try:
        with open(strategies_file, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception as e:
        logger.error(f"전략 설정 파일 로드 실패: {e}")
        return {}


def get_stock_name(symbol: str) -> str:
    """
    종목 코드에 해당하는 종목명을 반환합니다.

    Args:
        symbol: 종목 코드

    Returns:
        종목명 (알 수 없으면 symbol 반환)
    """
    stock_names = {
        "005930": "삼성전자",
        "000660": "SK하이닉스",
        "035420": "NAVER",
        "051910": "LG화학",
        "006400": "삼성SDI",
        "035720": "카카오",
        "000270": "기아",
        "005380": "현대차",
        "068270": "셀트리온",
        "207940": "삼성바이오로직스",
        "AAPL": "Apple",
        "GOOGL": "Alphabet",
        "MSFT": "Microsoft",
        "AMZN": "Amazon",
        "TSLA": "Tesla",
    }
    return stock_names.get(symbol, symbol)


def calculate_change_pct(current: float, previous: float) -> float:
    """
    변화율을 계산합니다.

    Args:
        current: 현재 값
        previous: 이전 값

    Returns:
        변화율 (소수, 예: 0.05 = 5%)
    """
    if previous == 0:
        return 0.0
    return (current - previous) / previous


def is_korean_stock(symbol: str) -> bool:
    """
    한국 주식 코드인지 확인합니다.

    Args:
        symbol: 종목 코드

    Returns:
        True if 한국 주식
    """
    return symbol.isdigit() and len(symbol) == 6


def get_current_kst_time() -> datetime:
    """현재 한국 시간을 반환합니다."""
    return datetime.now(KST)

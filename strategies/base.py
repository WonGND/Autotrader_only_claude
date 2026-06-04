"""
전략 추상 기본 클래스
"""

from abc import ABC, abstractmethod
from typing import Dict, Any
import pandas as pd


class BaseStrategy(ABC):
    """
    모든 거래 전략의 기본 클래스.

    새로운 전략을 만들려면 이 클래스를 상속받아
    generate_signals()와 get_required_history()를 구현하세요.
    """

    def __init__(self, params: Dict[str, Any] = None):
        self.params = params or {}

    @property
    @abstractmethod
    def name(self) -> str:
        """전략 이름"""
        pass

    @abstractmethod
    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        주어진 OHLCV 데이터를 기반으로 매매 신호를 생성합니다.

        Args:
            data: OHLCV DataFrame (columns: Open, High, Low, Close, Volume)
                  인덱스는 날짜(DatetimeIndex)여야 합니다.

        Returns:
            'signal' 컬럼이 추가된 DataFrame:
                1  = 매수 신호
               -1  = 매도 신호
                0  = 보유 (아무 것도 안 함)
        """
        pass

    @abstractmethod
    def get_required_history(self) -> int:
        """
        전략 계산에 필요한 최소 과거 데이터 일수를 반환합니다.

        Returns:
            필요한 최소 일수 (예: 이동평균 20일이면 20 반환)
        """
        pass

    def validate_data(self, data: pd.DataFrame) -> bool:
        """
        입력 데이터의 유효성을 검사합니다.

        Args:
            data: 검사할 DataFrame

        Returns:
            True if 유효, False otherwise
        """
        required_cols = {"Close"}
        if not required_cols.issubset(data.columns):
            return False
        if len(data) < self.get_required_history():
            return False
        return True

"""전략 모듈"""
from strategies.base import BaseStrategy
from strategies.ma_crossover import MACrossoverStrategy
from strategies.rsi_strategy import RSIStrategy
from strategies.bollinger_bands import BollingerBandsStrategy

STRATEGY_MAP = {
    "ma_crossover": MACrossoverStrategy,
    "rsi": RSIStrategy,
    "bollinger_bands": BollingerBandsStrategy,
}

__all__ = [
    "BaseStrategy",
    "MACrossoverStrategy",
    "RSIStrategy",
    "BollingerBandsStrategy",
    "STRATEGY_MAP",
]

"""
전략 성과 모니터 (Strategy Performance Monitor)

승률이 임계값 이하인 전략을 자동으로 비활성화합니다.
최근 N개 거래를 슬라이딩 윈도우로 평가합니다.
"""

import json
from collections import deque
from typing import Dict, Optional

from database.models import AppConfig, get_session
from utils.logger import get_logger

logger = get_logger(__name__)

_CONFIG_KEY_PREFIX = "perf_monitor:"

# 모듈 레벨 싱글톤
_instance: Optional["StrategyPerformanceMonitor"] = None


def get_monitor() -> "StrategyPerformanceMonitor":
    """전역 성과 모니터 싱글톤 반환"""
    global _instance
    if _instance is None:
        _instance = StrategyPerformanceMonitor()
    return _instance


class StrategyPerformanceMonitor:
    """
    전략별 승률을 추적하고 기준 이하 전략을 자동 비활성화합니다.

    Args:
        min_trades:          평가를 시작할 최소 거래 수 (기본: 10)
        disable_threshold:   자동 비활성화 승률 임계값 (기본: 0.40 = 40%)
        reenable_threshold:  자동 재활성화 승률 임계값 (기본: 0.50 = 50%)
        lookback_trades:     평가에 사용할 최근 거래 수 (기본: 20)
    """

    def __init__(
        self,
        min_trades: int = 10,
        disable_threshold: float = 0.40,
        reenable_threshold: float = 0.50,
        lookback_trades: int = 20,
    ):
        self.min_trades = min_trades
        self.disable_threshold = disable_threshold
        self.reenable_threshold = reenable_threshold
        self.lookback_trades = lookback_trades
        self._trade_history: Dict[str, deque] = {}
        self._disabled: Dict[str, bool] = {}
        self._load_state()

    def _load_state(self):
        """DB에서 이전 상태 복원"""
        try:
            session = get_session()
            configs = (
                session.query(AppConfig)
                .filter(AppConfig.key.like(f"{_CONFIG_KEY_PREFIX}%"))
                .all()
            )
            for cfg in configs:
                name = cfg.key[len(_CONFIG_KEY_PREFIX):]
                if cfg.value:
                    data = json.loads(cfg.value)
                    self._trade_history[name] = deque(
                        data.get("history", []), maxlen=self.lookback_trades
                    )
                    self._disabled[name] = data.get("disabled", False)
            session.close()
        except Exception as e:
            logger.warning(f"성과 모니터 상태 로드 실패: {e}")

    def _save_state(self, strategy_name: str):
        """전략 상태를 DB에 저장"""
        try:
            session = get_session()
            key = f"{_CONFIG_KEY_PREFIX}{strategy_name}"
            value = json.dumps({
                "history": list(self._trade_history.get(strategy_name, [])),
                "disabled": self._disabled.get(strategy_name, False),
            })
            cfg = session.query(AppConfig).filter_by(key=key).first()
            if cfg:
                cfg.value = value
            else:
                session.add(AppConfig(key=key, value=value))
            session.commit()
            session.close()
        except Exception as e:
            logger.warning(f"성과 모니터 상태 저장 실패 ({strategy_name}): {e}")

    def record_trade(self, strategy_name: str, won: bool):
        """
        거래 결과를 기록하고 자동 비활성화 여부를 평가합니다.

        Args:
            strategy_name: 전략 이름
            won:           True = 수익 거래, False = 손실 거래
        """
        if strategy_name not in self._trade_history:
            self._trade_history[strategy_name] = deque(maxlen=self.lookback_trades)
        self._trade_history[strategy_name].append(won)
        self._evaluate(strategy_name)
        self._save_state(strategy_name)

    def _evaluate(self, strategy_name: str):
        """승률 기반 활성화/비활성화 평가"""
        history = self._trade_history.get(strategy_name, deque())
        if len(history) < self.min_trades:
            return

        win_rate = sum(history) / len(history)
        disabled = self._disabled.get(strategy_name, False)

        if not disabled and win_rate < self.disable_threshold:
            self._disabled[strategy_name] = True
            logger.warning(
                f"[성과모니터] 전략 자동 비활성화: {strategy_name} "
                f"승률 {win_rate*100:.1f}% < 임계값 {self.disable_threshold*100:.0f}%"
            )
        elif disabled and win_rate >= self.reenable_threshold:
            self._disabled[strategy_name] = False
            logger.info(
                f"[성과모니터] 전략 자동 재활성화: {strategy_name} "
                f"승률 {win_rate*100:.1f}% >= 재활성화 임계값 {self.reenable_threshold*100:.0f}%"
            )

    def is_enabled(self, strategy_name: str) -> bool:
        """전략이 현재 활성 상태인지 반환"""
        return not self._disabled.get(strategy_name, False)

    def get_win_rate(self, strategy_name: str) -> Optional[float]:
        """
        전략의 현재 승률 반환.
        최소 거래 수 미달 시 None.
        """
        history = self._trade_history.get(strategy_name, deque())
        if len(history) < self.min_trades:
            return None
        return sum(history) / len(history)

    def get_trade_count(self, strategy_name: str) -> int:
        """전략의 최근 거래 수 반환"""
        return len(self._trade_history.get(strategy_name, deque()))

    def set_enabled(self, strategy_name: str, enabled: bool):
        """전략을 수동으로 활성화 또는 비활성화"""
        self._disabled[strategy_name] = not enabled
        self._save_state(strategy_name)
        logger.info(
            f"[성과모니터] 수동 {'활성화' if enabled else '비활성화'}: {strategy_name}"
        )

    def get_all_status(self) -> Dict[str, dict]:
        """모든 전략의 성과 상태 반환"""
        all_names = set(self._trade_history) | set(self._disabled)
        result = {}
        for name in sorted(all_names):
            history = self._trade_history.get(name, deque())
            win_rate = self.get_win_rate(name)
            result[name] = {
                "enabled": self.is_enabled(name),
                "trade_count": len(history),
                "win_rate": win_rate,
                "win_rate_pct": f"{win_rate*100:.1f}%" if win_rate is not None else "데이터 부족",
                "min_trades_required": self.min_trades,
                "disable_threshold_pct": f"{self.disable_threshold*100:.0f}%",
                "reenable_threshold_pct": f"{self.reenable_threshold*100:.0f}%",
            }
        return result

"""
선물 거래 백테스터 (Futures Backtester)

선물 거래의 레버리지, 청산, 펀딩비, 수수료를 시뮬레이션합니다.
롱/숏 양방향 거래를 지원합니다.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

from strategies.futures_strategy import FuturesStrategy
from trader.futures_position import FuturesPosition
from trader.leverage_manager import LeverageManager
from data.fetcher import DataFetcher
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class FuturesBacktestResult:
    """선물 백테스트 결과"""
    symbol: str
    strategy: str
    start_date: str
    end_date: str
    initial_capital: float
    final_capital: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    win_rate: float
    total_trades: int
    long_trades: int
    short_trades: int
    liquidations: int
    avg_leverage: float
    avg_hold_hours: float
    trades_log: List[Dict[str, Any]] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)
    leverage_mode: str = "dynamic"

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "strategy": self.strategy,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "initial_capital": self.initial_capital,
            "final_capital": self.final_capital,
            "total_return_pct": self.total_return_pct,
            "max_drawdown_pct": self.max_drawdown_pct,
            "sharpe_ratio": self.sharpe_ratio,
            "win_rate": self.win_rate,
            "total_trades": self.total_trades,
            "long_trades": self.long_trades,
            "short_trades": self.short_trades,
            "liquidations": self.liquidations,
            "avg_leverage": self.avg_leverage,
            "avg_hold_hours": self.avg_hold_hours,
            "leverage_mode": self.leverage_mode,
        }


class FuturesBacktester:
    """
    선물 거래 백테스터

    Args:
        strategy:          선물 전략 객체
        leverage_manager:  레버리지 관리자
        initial_capital:   초기 자본금 (USD)
        maker_fee:         메이커 수수료율 (기본: 0.02%)
        taker_fee:         테이커 수수료율 (기본: 0.05%)
        funding_rate_8h:   8시간 펀딩 비율 (기본: 0.01%)
        fixed_leverage:    고정 레버리지 (None이면 동적)
        risk_per_trade_pct: 거래당 최대 손실 비율 (기본: 2%)
    """

    def __init__(
        self,
        strategy: FuturesStrategy,
        leverage_manager: LeverageManager,
        initial_capital: float = 10_000_000,
        maker_fee: float = 0.0002,
        taker_fee: float = 0.0005,
        funding_rate_8h: float = 0.0001,
        fixed_leverage: Optional[int] = None,
        risk_per_trade_pct: float = 0.02,
    ):
        self.strategy = strategy
        self.leverage_manager = leverage_manager
        self.initial_capital = initial_capital
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self.funding_rate_8h = funding_rate_8h
        self.fixed_leverage = fixed_leverage  # None = dynamic
        self.risk_per_trade_pct = risk_per_trade_pct
        self.data_fetcher = DataFetcher()

    def run(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> FuturesBacktestResult:
        """
        선물 백테스트 실행

        Args:
            symbol:     종목 코드 (예: BTC-USD)
            start_date: 시작일 (YYYY-MM-DD)
            end_date:   종료일 (YYYY-MM-DD)

        Returns:
            FuturesBacktestResult
        """
        leverage_desc = f"{self.fixed_leverage}x" if self.fixed_leverage else "동적"
        logger.info(
            f"선물 백테스트 시작: {symbol} | {start_date}~{end_date} | "
            f"레버리지={leverage_desc}"
        )

        # ── 1. 데이터 로드 ────────────────────────────────────────
        buffer_days = self.strategy.get_required_history() * 2
        extended_start = (
            datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=buffer_days)
        ).strftime("%Y-%m-%d")

        raw_data = self.data_fetcher.get_ohlcv(symbol, extended_start, end_date)
        if raw_data.empty:
            logger.error(f"{symbol} 데이터를 가져오지 못했습니다.")
            return self._empty_result(symbol, start_date, end_date)

        # ── 2. 지표 계산 (신호 포함) ──────────────────────────────
        data_with_signals = self.strategy.generate_signals(raw_data)

        # start_date 이후 데이터만 백테스트
        bt_data = data_with_signals[data_with_signals.index >= start_date].copy()
        if bt_data.empty:
            logger.error(f"{symbol} 백테스트 기간 데이터 없음")
            return self._empty_result(symbol, start_date, end_date)

        # ── 3. 시뮬레이션 루프 ────────────────────────────────────
        cash = float(self.initial_capital)
        position: Optional[FuturesPosition] = None
        trades_log: List[Dict] = []
        equity_curve: List[float] = []
        leverage_list: List[int] = []
        hold_hours_list: List[float] = []
        long_count = 0
        short_count = 0
        liquidation_count = 0

        # 일봉 기준 펀딩비: 하루에 3회 지급 (8h 간격)
        daily_funding_rate = self.funding_rate_8h * 3

        for idx, row in bt_data.iterrows():
            price_open = float(row.get("Open", row["Close"]))
            price_high = float(row["High"]) if "High" in row else float(row["Close"])
            price_low = float(row["Low"]) if "Low" in row else float(row["Close"])
            price_close = float(row["Close"])
            signal = int(row.get("signal", 0))
            signal_strength = int(row.get("signal_strength", 1))
            atr = float(row.get("atr", price_close * 0.02))

            # ── a. 기존 포지션 청산 조건 확인 ─────────────────────
            if position is not None:
                # 펀딩비 적용 (일일 3회 × 8h 비율)
                # 롱은 펀딩비 납부 (positive funding rate), 숏은 수취
                notional = position.quantity
                funding_cost = notional * daily_funding_rate
                if position.side == "long":
                    cash -= funding_cost
                else:
                    cash += funding_cost

                exit_price = None
                exit_reason = None

                # 청산가 터치 확인 (가장 먼저)
                if position.is_liquidated(price_low, price_high):
                    exit_price = position.liquidation_price
                    exit_reason = "liquidation"
                    liquidation_count += 1
                    # 청산 시 증거금 전액 손실
                    pnl = -position.margin
                    cash += 0  # 증거금 이미 소진
                # 손절가 확인
                elif position.is_stop_loss_hit(price_low, price_high):
                    exit_price = position.stop_loss
                    exit_reason = "stop_loss"
                # 익절가 확인
                elif position.is_take_profit_hit(price_low, price_high):
                    exit_price = position.take_profit
                    exit_reason = "take_profit"

                if exit_reason is not None and exit_reason != "liquidation":
                    # 수수료 차감 후 손익 계산
                    pnl = position.calculate_pnl(exit_price)
                    fee = position.quantity * self.taker_fee
                    pnl -= fee
                    cash += position.margin + pnl  # 증거금 반환 + 손익

                if exit_price is not None:
                    hold_duration = (idx - position.entry_time).total_seconds() / 3600
                    hold_hours_list.append(hold_duration)
                    trades_log.append({
                        "entry_time": position.entry_time.isoformat(),
                        "exit_time": idx.isoformat(),
                        "symbol": symbol,
                        "side": position.side,
                        "entry_price": position.entry_price,
                        "exit_price": exit_price,
                        "leverage": position.leverage,
                        "margin": position.margin,
                        "quantity": position.quantity,
                        "pnl": pnl if exit_reason != "liquidation" else -position.margin,
                        "pnl_pct": (pnl / position.margin * 100) if exit_reason != "liquidation" else -100.0,
                        "exit_reason": exit_reason,
                        "hold_hours": hold_duration,
                    })
                    position = None

            # ── b. 신호 기반 새 포지션 진입 ───────────────────────
            if signal != 0 and position is None and cash > 0:
                side = "long" if signal == 1 else "short"

                # ATR% 계산
                atr_pct = (atr / price_close * 100) if price_close > 0 else 2.0
                atr_pct = max(atr_pct, 0.1)  # 최소 0.1%

                # 레버리지 결정
                if self.fixed_leverage is not None:
                    leverage = self.fixed_leverage
                else:
                    max_lev = self.leverage_manager.calculate_max_leverage_by_volatility(atr_pct)
                    leverage = self.leverage_manager.calculate_effective_leverage(
                        max_lev, signal_strength
                    )

                # 손절/익절가 계산
                stop_loss = self.leverage_manager.calculate_stop_loss(
                    price_close, atr, side
                )
                take_profit = self.leverage_manager.calculate_take_profit(
                    price_close, atr, side
                )

                # 청산가 임시 계산 (증거금 계산 전 안전 체크)
                maintenance_margin = 0.005
                if side == "long":
                    liq_price = price_close * (1 - 1 / leverage + maintenance_margin)
                else:
                    liq_price = price_close * (1 + 1 / leverage - maintenance_margin)

                # 청산 안전 체크
                if not self.leverage_manager.is_liquidation_safe(stop_loss, liq_price, side):
                    # 레버리지를 낮춰서 재시도
                    leverage = max(1, leverage - 1)
                    if side == "long":
                        liq_price = price_close * (1 - 1 / leverage + maintenance_margin)
                    else:
                        liq_price = price_close * (1 + 1 / leverage - maintenance_margin)

                # 손절 거리 비율
                sl_dist_pct = abs(price_close - stop_loss) / price_close
                sl_dist_pct = max(sl_dist_pct, 0.005)  # 최소 0.5%

                # 증거금 계산
                margin = self.leverage_manager.calculate_margin(
                    portfolio_value=cash,
                    risk_pct=self.risk_per_trade_pct,
                    stop_loss_distance_pct=sl_dist_pct,
                    leverage=leverage,
                )
                margin = min(margin, cash * 0.95)  # 가용 현금의 95% 이내

                if margin < 10:
                    # 증거금 부족 시 진입 포기
                    equity_curve.append(cash)
                    continue

                # 명목 포지션 크기 (USD)
                quantity = margin * leverage

                # 포지션 생성
                try:
                    pos = FuturesPosition(
                        symbol=symbol,
                        side=side,
                        entry_price=price_close,
                        quantity=quantity,
                        margin=margin,
                        leverage=leverage,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        liquidation_price=liq_price,
                        entry_time=idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx,
                    )
                except ValueError as e:
                    logger.warning(f"포지션 생성 실패 ({symbol} {idx}): {e}")
                    equity_curve.append(cash)
                    continue

                # 진입 수수료 차감
                entry_fee = quantity * self.taker_fee
                cash -= (margin + entry_fee)

                position = pos
                leverage_list.append(leverage)

                if side == "long":
                    long_count += 1
                else:
                    short_count += 1

            # ── e. 포트폴리오 가치 계산 ───────────────────────────
            if position is not None:
                unrealized = position.calculate_pnl(price_close)
                portfolio_val = cash + position.margin + unrealized
            else:
                portfolio_val = cash
            equity_curve.append(max(portfolio_val, 0.0))

        # 마지막 포지션 강제 청산
        if position is not None:
            last_price = float(bt_data["Close"].iloc[-1])
            pnl = position.calculate_pnl(last_price)
            fee = position.quantity * self.taker_fee
            pnl -= fee
            cash += position.margin + pnl
            hold_duration = (bt_data.index[-1] - position.entry_time).total_seconds() / 3600
            hold_hours_list.append(hold_duration)
            trades_log.append({
                "entry_time": position.entry_time.isoformat(),
                "exit_time": bt_data.index[-1].isoformat(),
                "symbol": symbol,
                "side": position.side,
                "entry_price": position.entry_price,
                "exit_price": last_price,
                "leverage": position.leverage,
                "margin": position.margin,
                "quantity": position.quantity,
                "pnl": pnl,
                "pnl_pct": pnl / position.margin * 100 if position.margin > 0 else 0,
                "exit_reason": "end_of_backtest",
                "hold_hours": hold_duration,
            })
            if equity_curve:
                equity_curve[-1] = max(cash, 0.0)

        final_capital = max(cash, 0.0)

        # ── 4. 성과 지표 계산 ─────────────────────────────────────
        eq_series = pd.Series(equity_curve)
        total_return_pct = (final_capital - self.initial_capital) / self.initial_capital * 100
        max_drawdown_pct = self._calc_max_drawdown(eq_series)
        sharpe = self._calc_sharpe(eq_series)

        closed_trades = [t for t in trades_log if t["exit_reason"] != "end_of_backtest"]
        win_trades = sum(1 for t in closed_trades if t["pnl"] > 0)
        total_closed = len(closed_trades)
        win_rate = win_trades / total_closed if total_closed > 0 else 0.0

        avg_leverage = float(np.mean(leverage_list)) if leverage_list else (
            float(self.fixed_leverage) if self.fixed_leverage else 1.0
        )
        avg_hold_hours = float(np.mean(hold_hours_list)) if hold_hours_list else 0.0

        leverage_mode = f"{self.fixed_leverage}x" if self.fixed_leverage else "동적"

        logger.info(
            f"선물 백테스트 완료: {symbol} {leverage_mode} | "
            f"수익={total_return_pct:.1f}% | MDD={max_drawdown_pct:.1f}% | "
            f"샤프={sharpe:.2f} | 청산={liquidation_count}"
        )

        return FuturesBacktestResult(
            symbol=symbol,
            strategy=self.strategy.name,
            start_date=start_date,
            end_date=end_date,
            initial_capital=self.initial_capital,
            final_capital=final_capital,
            total_return_pct=total_return_pct,
            max_drawdown_pct=max_drawdown_pct,
            sharpe_ratio=sharpe,
            win_rate=win_rate,
            total_trades=long_count + short_count,
            long_trades=long_count,
            short_trades=short_count,
            liquidations=liquidation_count,
            avg_leverage=avg_leverage,
            avg_hold_hours=avg_hold_hours,
            trades_log=trades_log,
            equity_curve=equity_curve,
            leverage_mode=leverage_mode,
        )

    # ─── 성과 지표 ────────────────────────────────────────────────

    def _calc_max_drawdown(self, equity: pd.Series) -> float:
        """최대 낙폭(MDD) 계산 (%)"""
        if equity.empty or equity.max() == 0:
            return 0.0
        peak = equity.expanding().max()
        drawdown = (equity - peak) / peak
        return float(drawdown.min() * 100)

    def _calc_sharpe(self, equity: pd.Series, risk_free_rate: float = 0.03) -> float:
        """샤프 비율 계산 (연율화, 암호화폐 365일 기준)"""
        if len(equity) < 2:
            return 0.0
        daily_returns = equity.pct_change().dropna()
        if daily_returns.std() == 0:
            return 0.0
        annual_factor = np.sqrt(365)
        daily_rf = risk_free_rate / 365
        sharpe = (daily_returns.mean() - daily_rf) / daily_returns.std() * annual_factor
        return float(np.clip(sharpe, -10, 10))

    def _empty_result(
        self, symbol: str, start_date: str, end_date: str
    ) -> FuturesBacktestResult:
        """오류 시 빈 결과 반환"""
        leverage_mode = f"{self.fixed_leverage}x" if self.fixed_leverage else "동적"
        return FuturesBacktestResult(
            symbol=symbol,
            strategy=self.strategy.name,
            start_date=start_date,
            end_date=end_date,
            initial_capital=self.initial_capital,
            final_capital=self.initial_capital,
            total_return_pct=0.0,
            max_drawdown_pct=0.0,
            sharpe_ratio=0.0,
            win_rate=0.0,
            total_trades=0,
            long_trades=0,
            short_trades=0,
            liquidations=0,
            avg_leverage=float(self.fixed_leverage or 1),
            avg_hold_hours=0.0,
            trades_log=[],
            equity_curve=[],
            leverage_mode=leverage_mode,
        )

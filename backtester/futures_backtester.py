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
from data.macro_fetcher import get_macro_data
from strategies.macro_filter import MacroFilter
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
    validation: Dict[str, Any] = field(default_factory=dict)   # 검증 지표 전체

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
        use_trailing_stop: bool = False,
        trail_atr_multiplier: float = 2.0,
        trail_activation_atr: float = 3.0,
        use_macro_filter: bool = False,
        signal_delay_bars: int = 0,
    ):
        self.strategy = strategy
        self.leverage_manager = leverage_manager
        self.initial_capital = initial_capital
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self.funding_rate_8h = funding_rate_8h
        self.fixed_leverage = fixed_leverage
        self.risk_per_trade_pct = risk_per_trade_pct
        # 트레일링 스탑 설정
        self.use_trailing_stop = use_trailing_stop
        self.trail_atr_multiplier = trail_atr_multiplier
        self.trail_activation_atr = trail_activation_atr
        # 매크로 필터 설정
        self.use_macro_filter = use_macro_filter
        # 신호 지연(봉): Time Delay Test용. N>0이면 신호를 N봉 뒤에 실행
        self.signal_delay_bars = int(signal_delay_bars)
        self.data_fetcher = DataFetcher()

    def run(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        raw_data: Optional[pd.DataFrame] = None,
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

        # raw_data가 주어지면 다운로드를 건너뛴다 (파라미터 그리드 재실행 시 속도/레이트리밋 방지)
        if raw_data is None:
            raw_data = self.data_fetcher.get_ohlcv(symbol, extended_start, end_date)
        if raw_data is None or raw_data.empty:
            logger.error(f"{symbol} 데이터를 가져오지 못했습니다.")
            return self._empty_result(symbol, start_date, end_date)

        # ── 2. 지표 계산 (신호 포함) ──────────────────────────────
        data_with_signals = self.strategy.generate_signals(raw_data)

        # ── Time Delay Test (24): 신호를 N봉 뒤로 미뤄 실행 ──────────
        # 같은 봉 즉시 체결이라는 비현실적 가정에 전략이 의존하는지 검증.
        # 견고한 전략은 1~2봉 지연에도 성과가 급락하지 않는다.
        if self.signal_delay_bars > 0:
            for col in ("signal", "signal_strength"):
                if col in data_with_signals.columns:
                    data_with_signals[col] = data_with_signals[col].shift(self.signal_delay_bars)
            data_with_signals = data_with_signals.dropna(subset=["signal"])

        # start_date 이후 데이터만 백테스트
        bt_data = data_with_signals[data_with_signals.index >= start_date].copy()
        if bt_data.empty:
            logger.error(f"{symbol} 백테스트 기간 데이터 없음")
            return self._empty_result(symbol, start_date, end_date)

        # ── 3. 매크로 데이터 로드 ────────────────────────────────
        macro_df = pd.DataFrame()
        macro_filter = MacroFilter()
        if self.use_macro_filter:
            try:
                macro_df = get_macro_data(start_date, end_date)
                logger.info(f"매크로 필터 활성화 — F&G/VIX/도미넌스 적용")
            except Exception as e:
                logger.warning(f"매크로 데이터 로드 실패 ({e}) — 매크로 필터 비활성화")

        # ── 4. 시뮬레이션 루프 ────────────────────────────────────
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
            entry_basis = str(row.get("entry_basis", ""))
            atr = float(row.get("atr", price_close * 0.02))

            # ── a. 기존 포지션 청산 조건 확인 ─────────────────────
            if position is not None:
                # 펀딩비 적용 (일일 3회 × 8h 비율)
                notional = position.quantity
                funding_cost = notional * daily_funding_rate
                if position.side == "long":
                    cash -= funding_cost
                else:
                    cash += funding_cost

                # ── 트레일링 스탑 업데이트 ────────────────────────
                if self.use_trailing_stop:
                    entry_atr = position.entry_atr or atr
                    if position.side == "long":
                        # 새 고점 갱신
                        position.best_price = max(position.best_price, price_high)
                        # 진입가에서 activation_atr 이상 이익 발생 시 트레일링 활성화
                        activation = position.entry_price + self.trail_activation_atr * entry_atr
                        if position.best_price >= activation:
                            trail_stop = self.leverage_manager.calculate_trailing_stop(
                                position.best_price, atr, "long", self.trail_atr_multiplier
                            )
                            # 손절은 한 방향(올라가는 방향)으로만 이동
                            if trail_stop > position.stop_loss:
                                position.stop_loss = trail_stop
                    else:
                        # 새 저점 갱신
                        if position.best_price == 0:
                            position.best_price = price_low
                        else:
                            position.best_price = min(position.best_price, price_low)
                        # 진입가에서 activation_atr 이상 이익 발생 시 트레일링 활성화
                        activation = position.entry_price - self.trail_activation_atr * entry_atr
                        if position.best_price <= activation:
                            trail_stop = self.leverage_manager.calculate_trailing_stop(
                                position.best_price, atr, "short", self.trail_atr_multiplier
                            )
                            # 손절은 한 방향(내려가는 방향)으로만 이동
                            if trail_stop < position.stop_loss:
                                position.stop_loss = trail_stop

                exit_price = None
                exit_reason = None

                # 청산가 터치 확인 (가장 먼저)
                if position.is_liquidated(price_low, price_high):
                    exit_price = position.liquidation_price
                    exit_reason = "liquidation"
                    liquidation_count += 1
                    pnl = -position.margin
                    cash += 0
                # 손절/트레일링 스탑 확인
                elif position.is_stop_loss_hit(price_low, price_high):
                    exit_price = position.stop_loss
                    exit_reason = "stop_loss"
                # 고정 익절가 확인 (트레일링 미작동 구간의 안전망)
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
                        "signal_strength": getattr(position, "signal_strength", signal_strength),
                        "entry_basis": getattr(position, "entry_basis", ""),
                    })
                    position = None

            # ── b. 신호 기반 새 포지션 진입 ───────────────────────
            if signal != 0 and position is None and cash > 0:
                side = "long" if signal == 1 else "short"

                # ── 매크로 필터 적용 ──────────────────────────────
                macro_modifier = 1.0
                if self.use_macro_filter and not macro_df.empty:
                    try:
                        date_key = idx.normalize() if hasattr(idx, "normalize") else pd.Timestamp(idx).normalize()
                        if date_key in macro_df.index:
                            mrow = macro_df.loc[date_key]
                        else:
                            # 가장 가까운 이전 날짜 사용
                            past = macro_df.index[macro_df.index <= date_key]
                            mrow = macro_df.loc[past[-1]] if len(past) > 0 else None

                        if mrow is not None:
                            msig = macro_filter.evaluate_from_row(mrow, str(date_key.date()), symbol)
                            # 방향 차단
                            if side == "long"  and not msig.allow_long:
                                equity_curve.append(cash)
                                continue
                            if side == "short" and not msig.allow_short:
                                equity_curve.append(cash)
                                continue
                            macro_modifier = msig.position_modifier
                    except Exception as e:
                        logger.debug(f"매크로 필터 오류 ({e}), 스킵")

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

                # 증거금 계산 (매크로 배율 적용, 최대 포트폴리오의 20%까지)
                margin = self.leverage_manager.calculate_margin(
                    portfolio_value=cash,
                    risk_pct=self.risk_per_trade_pct * macro_modifier,
                    stop_loss_distance_pct=sl_dist_pct,
                    leverage=leverage,
                )
                margin = min(margin, cash * 0.95, cash * 0.20)  # 최대 20% 상한

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

                pos.signal_strength = signal_strength
                pos.entry_basis = entry_basis
                # 트레일링 스탑 초기화
                pos.entry_atr = atr
                pos.best_price = price_close if side == "long" else price_close
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
                "signal_strength": getattr(position, "signal_strength", 0),
                "entry_basis": getattr(position, "entry_basis", ""),
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

        # ── 검증 지표 전체 계산 (켈리/소르티노/Calmar/몬테카를로/CVaR 등) ──
        # 코인은 24시간 시장이므로 연율화 계수 365 사용
        validation_report = {}
        try:
            import validation as _val
            # 포트폴리오 대비 거래수익률(pnl / 초기자본)을 사용해야 자산곡선 MDD와
            # 몬테카를로/시퀀스 결과가 정합한다. pnl_pct는 증거금 대비라 부적합.
            trade_returns = [t["pnl"] / self.initial_capital for t in closed_trades
                             if t.get("pnl") is not None]
            if len(trade_returns) >= 2 and len(equity_curve) >= 2:
                validation_report = _val.full_report(
                    equity_curve, trade_returns, periods=365, num_trials=1)
        except Exception as e:
            logger.warning(f"검증 지표 계산 실패: {e}")

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
            validation=validation_report,
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

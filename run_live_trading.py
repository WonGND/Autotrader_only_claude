# -*- coding: utf-8 -*-
"""
Bybit 실계좌 자동매매 실행 스크립트

실행 방법:
    python -X utf8 run_live_trading.py
"""

import os
import sys
import time
import signal
import atexit
import ctypes
import threading
from pathlib import Path
from datetime import datetime, timedelta

try:
    from pybit.exceptions import InvalidRequestError as _BybitInvalidRequestError
except ImportError:
    _BybitInvalidRequestError = None

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from broker.bybit_futures_broker import BybitFuturesBroker
from strategies.futures_strategy import FuturesStrategy, COIN_SYMBOL_PARAMS, get_coin_params
from strategies.alt_strategies import DonchianBreakout
from trader.leverage_manager import LeverageManager
from data.fetcher import DataFetcher
from data.macro_fetcher import MacroDataFetcher
from strategies.macro_filter import MacroFilter
from data.news_fetcher import NewsFetcher
from utils.telegram_notifier import TelegramNotifier
from trader.position_monitor import PositionMonitor
from utils.logger import get_logger

logger = get_logger(__name__)

# ── 트레이딩 설정 ────────────────────────────────────────────────────
CONFIG = {
    # 2026-06-14: IS/OOS 재최적화로 OOS 견고성 미확보 4종목(ATOM/DOT/TRX/SOL) 제외.
    # 종목별 SMA는 COIN_SYMBOL_PARAMS 사용. 운용 종목 = 그 dict의 키.
    "symbols": list(COIN_SYMBOL_PARAMS.keys()),  # [BTC, ETH, BNB, DOGE]
    "risk_per_trade_pct": 0.05,   # 거래당 잔고의 5%
    "max_positions": 3,            # 동시 최대 포지션
    "check_interval_sec": 1800,    # 30분마다 체크
    "min_balance_usdt": 5,         # 최소 운용 잔고

    # ── 불타기(피라미딩): 이기는 포지션에 추세 따라 추가 ──────────
    # 레버리지 선물이라 보수적으로: 추가는 현재 수량의 일부만, 증거금 상한 고정.
    "pyramid_max_adds":       2,      # 종목당 최대 추가 횟수
    "pyramid_step_atr":       0.5,    # 직전 진입가에서 0.5×ATR 더 유리하게 갔을 때 추가
    "pyramid_min_profit":     0.01,   # 미실현 +1%(가격) 이상 이익일 때만 추가
    "pyramid_add_fraction":   0.5,    # 추가분 = 현재 수량의 50%
    "pyramid_max_margin_pct": 0.25,   # 불타기 포함 종목당 최대 증거금 비중
}


class LiveFuturesTrader:
    def __init__(self):
        testnet = os.environ.get("BYBIT_TESTNET", "true").lower() != "false"
        self.mode = "테스트넷" if testnet else "실계좌"

        self.broker        = BybitFuturesBroker(testnet=testnet)
        # 종목별 돈치안 전략 (롱/숏 채널 분리). 진입전략 SMA→Donchian 전환(2026-06-14)
        self.strategies    = {sym: DonchianBreakout(get_coin_params(sym))
                              for sym in CONFIG["symbols"]}
        # 데이터 길이 체크 등 공용 호출용 기본 전략
        self.strategy      = DonchianBreakout()
        self.lm            = LeverageManager()
        # 포지션 청산 시 메인 루프를 즉시 깨워 신규 매수 기회를 탐색하기 위한 이벤트
        self._cycle_now    = threading.Event()
        self.fetcher       = DataFetcher()
        self.macro_fetcher = MacroDataFetcher()
        self.macro_filter  = MacroFilter()
        self.news_fetcher  = NewsFetcher()
        self.telegram      = TelegramNotifier()
        self.monitor       = PositionMonitor(self.broker, self.telegram,
                                              on_position_closed=self._cycle_now.set)

        self._running = False
        # 동일 차단 사유의 텔레그램 알림이 매 사이클(30분) 반복되는 것을 막기 위한 캐시.
        # key="SYMBOL:방향" → 마지막으로 알린 차단 사유. 사유가 바뀔 때만 1회 알린다.
        self._last_block_reason: dict = {}
        # 불타기 상태: symbol → {"adds": 추가횟수, "last_add_price": 마지막 추가가}
        self._pyramid_state: dict = {}

    def start(self):
        print(f"\n{'='*60}")
        print(f"  Bybit 선물 자동매매 [{self.mode}]")
        print(f"  종목: {', '.join(CONFIG['symbols'])}")
        print(f"  체크 주기: {CONFIG['check_interval_sec']}초")
        print(f"{'='*60}\n")

        # 연결 확인
        status = self.broker.ping()
        balance = status["balance_usdt"]
        equity  = status["equity_usdt"]
        print(f"  잔고:   {balance:.2f} USDT")
        print(f"  총자산: {equity:.2f} USDT\n")

        if balance < CONFIG["min_balance_usdt"]:
            msg = f"잔고 부족: {balance:.2f} USDT (최소 {CONFIG['min_balance_usdt']} USDT)"
            print(f"[경고] {msg}")
            self.telegram.notify_low_balance(balance, CONFIG["min_balance_usdt"])
            return

        # 텔레그램 시작 알림
        self.telegram.notify_start(
            self.mode, balance, CONFIG["symbols"], CONFIG["check_interval_sec"]
        )

        # 포지션 실시간 모니터 시작 (1분 주기)
        self.monitor.start()

        self._running = True
        signal.signal(signal.SIGINT, self._stop_handler)

        while self._running:
            try:
                self._run_cycle()
            except Exception as e:
                logger.error(f"사이클 오류: {e}", exc_info=True)
                self.telegram.notify_error("메인 사이클", str(e))
            finally:
                if self._running:
                    next_dt = (datetime.now() + timedelta(seconds=CONFIG["check_interval_sec"]))
                    print(f"\n  다음 체크: {next_dt.strftime('%H:%M')} ({CONFIG['check_interval_sec']}초 후) "
                          f"— 포지션 청산 시 즉시 재실행")
                    # 1초 단위 분할 대기: ①SIGBREAK(/stop) 핸들러 즉시 반영
                    # ②포지션 청산(_cycle_now) 감지 시 대기 중단 → 즉시 다음 사이클
                    self._cycle_now.clear()
                    for _ in range(int(CONFIG["check_interval_sec"])):
                        if not self._running:
                            break
                        if self._cycle_now.is_set():
                            print("  ⚡ 포지션 청산 감지 → 즉시 다음 사이클 (신규 매수 기회 탐색)")
                            break
                        time.sleep(1)

    def _stop_handler(self, sig, frame):
        print("\n\n[중단] Ctrl+C - 현재 사이클 완료 후 종료...")
        self._running = False
        self.monitor.stop()

    def _run_cycle(self):
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        print(f"\n[{now}] 사이클 시작 {'─'*40}")

        # ── 1. 매크로 확인 ───────────────────────────────────────────
        macro = self._get_macro_state()
        print(f"  F&G={macro['fg_value']:.0f}({macro['fg_class']})  "
              f"VIX={macro['vix']:.1f}  배율={macro['modifier']:.2f}x  "
              f"[{macro['reason']}]")

        # ── 2. 포지션 갱신 ───────────────────────────────────────────
        open_positions = {p.symbol: p for p in self.broker.get_positions()}
        n_open = len(open_positions)
        # 청산된 종목의 불타기 상태 정리 (다음 진입 시 0부터)
        for sym in list(self._pyramid_state):
            if sym.replace("-USD", "USDT") not in open_positions:
                self._pyramid_state.pop(sym, None)
        print(f"  포지션: {n_open}개")
        for sym, p in open_positions.items():
            pnl_sign = "+" if p.unrealized_pnl >= 0 else ""
            print(f"    {sym} {p.side} x{p.leverage} | "
                  f"진입=${p.avg_price:.4f} | 현재=${p.mark_price:.4f} | "
                  f"PnL={pnl_sign}{p.unrealized_pnl:.2f}USDT")

        # ── 3. 신호 확인 및 주문 ─────────────────────────────────────
        for symbol in CONFIG["symbols"]:
            try:
                self._process_symbol(symbol, open_positions, macro)
            except Exception as e:
                logger.error(f"{symbol} 처리 오류: {e}")
                self.telegram.notify_error(f"{symbol} 처리", str(e))

        # ── 4. 사이클 요약 ───────────────────────────────────────────
        balance = self.broker.get_balance()
        equity  = self.broker.get_total_equity()
        positions_list = list(self.broker.get_positions())
        print(f"\n  잔고: {balance:.2f} USDT | 총자산: {equity:.2f} USDT")

        self.telegram.notify_cycle(
            balance=balance,
            equity=equity,
            n_positions=len(positions_list),
            fg_value=macro["fg_value"],
            fg_class=macro["fg_class"],
            vix=macro["vix"],
            modifier=macro["modifier"],
            positions=positions_list,
        )

    def _get_macro_state(self) -> dict:
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            fg_df  = self.macro_fetcher.get_fear_greed(today, today)
            vix_df = self.macro_fetcher.get_vix(today, today)
            fg_val  = float(fg_df["fg_value"].iloc[-1]) if not fg_df.empty else 50
            fg_cls  = str(fg_df["fg_class"].iloc[-1])   if not fg_df.empty else "N/A"
            vix_val = float(vix_df["vix_close"].iloc[-1]) if not vix_df.empty else 20
            msig = self.macro_filter.evaluate(fg_val, vix_val, 0.0, today)
            return {
                "fg_value": fg_val, "fg_class": fg_cls,
                "vix": vix_val, "modifier": msig.position_modifier,
                "allow_long": msig.allow_long, "allow_short": msig.allow_short,
                "reason": msig.reason,
            }
        except Exception as e:
            logger.warning(f"매크로 조회 실패: {e}")
            return {
                "fg_value": 50, "fg_class": "N/A", "vix": 20,
                "modifier": 1.0, "allow_long": True, "allow_short": True, "reason": "기본값",
            }

    def _notify_block_once(self, symbol: str, direction: str, reason: str):
        """차단 사유가 직전과 다를 때만 텔레그램 알림 (동일 사유 반복 스팸 방지)."""
        key = f"{symbol}:{direction}"
        if self._last_block_reason.get(key) == reason:
            return                       # 같은 사유 → 알림 생략 (콘솔에는 이미 출력됨)
        self._last_block_reason[key] = reason
        self.telegram.notify_signal_blocked(symbol, direction, reason)

    def _clear_block(self, symbol: str, direction: str):
        """차단이 해소되면(진입/신호소멸) 캐시를 비워 다음 차단 시 다시 알리도록 한다."""
        self._last_block_reason.pop(f"{symbol}:{direction}", None)

    def _try_pyramid(self, pos, atr: float, macro: dict):
        """불타기(피라미딩): 이기는 포지션이 추세를 이어가면 추가 진입한다.

        레버리지 선물이라 보수적으로: 추가분은 현재 수량의 일부(pyramid_add_fraction)만,
        종목당 증거금 비중 상한(pyramid_max_margin_pct) 내에서만 더한다.
          · 손절선: 추가하면 거래소 평단이 올라가고, 모니터(_ratchet_stop)가 '새 평단'
                    기준 본전(BE) 락 + ATR 트레일링으로 자동 상향 → 원물량 이익 보호
          · 익절선: 추가 직후 새 평단 기준(평단 + 4×ATR)으로 재설정
          · 최소 익절(보장) 라인: 본전 락이 새 평단 기준이라, 손절돼도 전체가 ≥본전
        """
        if pos is None or atr <= 0:
            return
        cfg    = CONFIG
        symbol = pos.symbol.replace("USDT", "-USD")
        state  = self._pyramid_state.setdefault(symbol, {"adds": 0, "last_add_price": pos.avg_price})
        if state["adds"] >= cfg["pyramid_max_adds"]:
            return

        long  = pos.side == "Buy"
        price = self.broker.get_price(symbol)
        step  = cfg["pyramid_step_atr"] * atr
        # 추세 지속: 직전 추가가에서 0.5×ATR 이상 더 유리하게 진행했을 때만
        if long and price < state["last_add_price"] + step:
            return
        if not long and price > state["last_add_price"] - step:
            return
        # 이익 구간에서만 추가 (지는 포지션엔 절대 안 더함 = 물타기 금지)
        unreal = ((price - pos.avg_price) / pos.avg_price if long
                  else (pos.avg_price - price) / pos.avg_price)
        if unreal < cfg["pyramid_min_profit"]:
            return
        if long and not macro["allow_long"]:
            return
        if not long and not macro["allow_short"]:
            return

        # 증거금 비중 상한 내에서만 추가
        balance      = self.broker.get_balance()
        lev          = max(pos.leverage, 1)
        cur_margin   = (pos.size * price) / lev
        max_margin   = balance * cfg["pyramid_max_margin_pct"]
        if cur_margin >= max_margin:
            return
        add_margin   = min(cur_margin * cfg["pyramid_add_fraction"], max_margin - cur_margin)
        add_notional = add_margin * lev
        if add_notional < cfg["min_balance_usdt"]:
            return

        side_str = "long" if long else "short"
        try:
            if long:
                self.broker.open_long(symbol, add_notional, lev)
            else:
                self.broker.open_short(symbol, add_notional, lev)
        except Exception as e:
            logger.warning(f"{symbol} 불타기 추가 실패: {e}")
            return

        state["adds"] += 1
        state["last_add_price"] = price
        print(f"  {symbol}: 🔺 불타기 {state['adds']}차 추가 | +{add_notional:.1f} USDT @ ${price:.4f}")

        # 새 평단 기준 익절선 재설정 (손절선은 포지션 모니터가 자동 상향)
        time.sleep(1)
        newpos = next((p for p in self.broker.get_positions(symbol) if p.side == pos.side), None)
        if newpos:
            new_tp = self.lm.calculate_take_profit(newpos.avg_price, atr, side_str)
            self.broker.update_take_profit(symbol, side_str, new_tp)
            self.telegram.send(
                f"🔺 <b>불타기 추가 ({state['adds']}차)</b>  {symbol} {pos.side}\n"
                f"추가 명목 {add_notional:.1f} USDT @ ${price:,.4f}\n"
                f"새 평단 ${newpos.avg_price:,.4f}  (수량 {newpos.size})\n"
                f"익절 → ${new_tp:,.4f}  ·  손절은 새 평단 기준 자동 상향"
            )

    def _process_symbol(self, symbol: str, open_positions: dict, macro: dict):
        # 90일 데이터 로드
        end   = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d")
        data  = self.fetcher.get_ohlcv(symbol, start, end)

        # 종목별 최적 파라미터 전략 사용 (없으면 기본)
        strat = self.strategies.get(symbol, self.strategy)

        if data.empty or len(data) < strat.get_required_history():
            return

        signals  = strat.generate_signals(data)
        latest   = signals.iloc[-1]
        signal   = int(latest.get("signal", 0))
        atr      = float(latest.get("atr", 0))
        strength = int(latest.get("signal_strength", 1))
        basis    = str(latest.get("entry_basis", "") or "")

        if signal == 0:
            # 신호 소멸 → 이 종목의 차단 알림 캐시 초기화 (다음 차단 시 재알림)
            self._clear_block(symbol, "롱")
            self._clear_block(symbol, "숏")
            return

        bybit_sym = symbol.replace("-USD", "USDT")
        has_long  = any(p.side == "Buy"  and p.symbol == bybit_sym for p in open_positions.values())
        has_short = any(p.side == "Sell" and p.symbol == bybit_sym for p in open_positions.values())
        direction = "롱" if signal == 1 else "숏"

        # 중복 포지션: 신규 진입 대신 불타기(피라미딩) 시도
        if signal == 1 and has_long:
            self._try_pyramid(open_positions.get(bybit_sym), atr, macro)
            return
        if signal == -1 and has_short:
            self._try_pyramid(open_positions.get(bybit_sym), atr, macro)
            return

        # 최대 포지션 체크
        if len(open_positions) >= CONFIG["max_positions"]:
            print(f"  {symbol}: {direction} 신호 → 최대 포지션 도달")
            return

        # 매크로 차단
        if signal == 1 and not macro["allow_long"]:
            print(f"  {symbol}: {direction} 신호 → 매크로 차단 ({macro['reason']})")
            self._notify_block_once(symbol, direction, f"매크로 차단 ({macro['reason']})")
            return
        if signal == -1 and not macro["allow_short"]:
            print(f"  {symbol}: {direction} 신호 → 매크로 차단 ({macro['reason']})")
            self._notify_block_once(symbol, direction, f"매크로 차단 ({macro['reason']})")
            return

        # 뉴스 감성 체크
        try:
            sentiment  = self.news_fetcher.get_sentiment(symbol)
            news_score = sentiment.get("score", 0)
            if news_score <= -50:
                reason = f"뉴스 악재 (score={news_score})"
                print(f"  {symbol}: {direction} 신호 → {reason}")
                self._notify_block_once(symbol, direction, reason)
                return
        except Exception:
            news_score = 0

        # 잔고 확인
        balance = self.broker.get_balance()
        if balance < CONFIG["min_balance_usdt"]:
            self.telegram.notify_low_balance(balance, CONFIG["min_balance_usdt"])
            return

        # 포지션 크기 계산
        price       = self.broker.get_price(symbol)
        atr_pct     = (atr / price * 100) if price > 0 else 2.0
        max_lev     = self.lm.calculate_max_leverage_by_volatility(atr_pct)
        leverage    = self.lm.calculate_effective_leverage(max_lev, strength)
        side_str    = "long" if signal == 1 else "short"
        stop_loss   = self.lm.calculate_stop_loss(price, atr, side_str)
        take_profit = self.lm.calculate_take_profit(price, atr, side_str)
        sl_dist_pct = abs(price - stop_loss) / price

        margin = self.lm.calculate_margin(
            portfolio_value=balance,
            risk_pct=CONFIG["risk_per_trade_pct"] * macro["modifier"],
            stop_loss_distance_pct=sl_dist_pct,
            leverage=leverage,
        )
        margin       = min(margin, balance * 0.90)
        usd_notional = margin * leverage

        print(
            f"  {symbol}: {direction} 진입 | ${price:.4f} | {leverage}x | "
            f"마진={margin:.2f}USDT | 명목={usd_notional:.2f}USDT"
        )

        # 주문 실행
        try:
            if signal == 1:
                order = self.broker.open_long(symbol, usd_notional, leverage, stop_loss, take_profit)
            else:
                order = self.broker.open_short(symbol, usd_notional, leverage, stop_loss, take_profit)

            print(f"    주문 완료: {order.order_id}")
            self._clear_block(symbol, direction)   # 진입 성공 → 차단 캐시 초기화
            self.telegram.notify_entry(
                symbol=symbol,
                direction=direction,
                price=price,
                qty=order.qty,
                leverage=leverage,
                margin=margin,
                notional=usd_notional,
                sl=stop_loss,
                tp=take_profit,
                order_id=order.order_id,
                basis=basis,
                strength=strength,
                news_score=news_score,
                macro=macro,
            )

        except Exception as e:
            err_str = str(e)
            print(f"    주문 실패: {err_str}")
            # ErrCode 110007 = 잔고 부족 — 흔한 케이스, traceback 없이 경고만 기록
            is_balance_err = (
                _BybitInvalidRequestError and isinstance(e, _BybitInvalidRequestError)
                and "110007" in err_str
            )
            if is_balance_err:
                logger.warning(f"{symbol} 주문 실패 (잔고 부족): {err_str}")
            else:
                self.telegram.notify_entry_failed(symbol, direction, err_str)
                logger.error(f"{symbol} 주문 실패: {err_str}", exc_info=True)


# ── 실행 ─────────────────────────────────────────────────────────────

def check_env():
    missing = []
    for var in ("BYBIT_API_KEY", "BYBIT_API_SECRET"):
        if not os.environ.get(var):
            missing.append(var)
    if missing:
        print(f"[오류] 환경변수 미설정: {', '.join(missing)}")
        sys.exit(1)

    testnet = os.environ.get("BYBIT_TESTNET", "true").lower() != "false"
    if not testnet:
        auto_confirm = os.environ.get("TRADING_AUTO_CONFIRM", "").lower() == "true"
        if not auto_confirm:
            print("\n" + "!"*60)
            print("  경고: 실계좌 모드로 실행됩니다! 실제 자금이 거래됩니다.")
            print("!"*60)
            confirm = input("\n  계속하려면 'YES' 입력: ")
            if confirm.strip() != "YES":
                print("  취소.")
                sys.exit(0)


# ── 단일 인스턴스 잠금 (lockfile) ─────────────────────────────────
# 같은 Bybit 계좌에 봇이 2개 이상 동시 실행돼 이중 주문하는 사고 방지.
# (2026-06-14 텔레그램 봇 재시작으로 coin_futures 중복 실행 사고 발생 → 추가)
LOCK_FILE = Path(__file__).with_name("coin_bot.lock")
_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _pid_alive(pid: int) -> bool:
    """해당 PID의 프로세스가 아직 실행 중인지 확인 (Windows API)."""
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        exit_code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        kernel32.CloseHandle(handle)
        return bool(ok) and exit_code.value == _STILL_ACTIVE
    except Exception:
        return False   # 확인 불가 시 잠금을 막지 않음


def _release_lock():
    """정상 종료 시 lockfile 제거 (내 PID일 때만)."""
    try:
        if LOCK_FILE.exists() and LOCK_FILE.read_text().strip() == str(os.getpid()):
            LOCK_FILE.unlink()
    except OSError:
        pass


def acquire_single_instance_lock():
    """단일 인스턴스 잠금 획득. 이미 실행 중이면 봇을 종료한다."""
    if LOCK_FILE.exists():
        try:
            existing_pid = int(LOCK_FILE.read_text().strip())
        except (ValueError, OSError):
            existing_pid = None
        if existing_pid and existing_pid != os.getpid() and _pid_alive(existing_pid):
            msg = (f"이미 실행 중인 코인 봇이 있습니다 (PID {existing_pid}). "
                   f"이중 주문 방지를 위해 이 인스턴스를 종료합니다.")
            print(f"[잠금] {msg}")
            sys.exit(1)
        # 잠금 파일이 있지만 프로세스가 죽음 → 잔여물, 덮어씀
    LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
    atexit.register(_release_lock)
    print(f"[잠금] 단일 인스턴스 잠금 획득 (PID {os.getpid()})")


def _graceful_shutdown_handler(signum, frame):
    """
    텔레그램 봇 /stop이 보내는 종료 신호(CTRL_BREAK → SIGBREAK)를
    KeyboardInterrupt로 변환한다. 핸들러가 없으면 SIGBREAK 기본 동작으로
    즉시 종료되어 아래 finally의 종료 알림(notify_stop)이 발송되지 않는다.
    또한 30분 사이클 대기(time.sleep) 중에도 즉시 깨어나 15초 안에 정리를 마친다.
    """
    raise KeyboardInterrupt


if __name__ == "__main__":
    print("\n" + "="*60)
    print("  Bybit 선물 자동매매 시스템")
    print("="*60)

    if hasattr(signal, "SIGBREAK"):                        # Windows 전용
        signal.signal(signal.SIGBREAK, _graceful_shutdown_handler)
    signal.signal(signal.SIGTERM, _graceful_shutdown_handler)

    acquire_single_instance_lock()   # 중복 실행 방지 (같은 계좌 이중 주문 차단)

    check_env()

    trader = LiveFuturesTrader()

    # 텔레그램 연결 테스트
    if trader.telegram._enabled:
        ok = trader.telegram.ping()
        print(f"  텔레그램: {'연결 성공' if ok else '연결 실패'}")

    try:
        trader.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        # 정상 종료, Ctrl+C(SIGINT), /stop 명령(CTRL_BREAK) 모든 경우에 알림 전송
        try:
            final_balance = trader.broker.get_balance()
            trader.telegram.notify_stop(final_balance)
        except Exception:
            pass

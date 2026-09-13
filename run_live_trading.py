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
    "risk_per_trade_pct": 0.03,   # 거래당 잔고의 3% (2026-06-27 5%→3% 하향: 계좌 리스크 축소)
    "max_positions": 3,            # 동시 최대 포지션
    "check_interval_sec": 1800,    # 30분마다 체크
    "min_balance_usdt": 5,         # 최소 운용 잔고

    # ── 일일 손실 서킷브레이커 (계좌 단위 킬스위치) ───────────────
    # 당일 시작 자산 대비 총자산(equity)이 이 비율 이상 하락하면 '신규 진입/불타기'를
    # 그날 동안 중단한다. 기존 포지션의 SL/TP는 거래소에 그대로 두므로 청산은 정상 작동.
    # (실계좌인데 계좌 단위 손실 차단장치가 없던 문제 보완 — 미국봇 daily_loss_limit과 동등)
    "daily_drawdown_limit": 0.10,  # 일일 자산 -10% 도달 시 당일 신규 진입 중단

    # ── 불타기(피라미딩): 이기는 포지션에 추세 따라 추가 ──────────
    # 레버리지 선물이라 보수적으로: 추가는 현재 수량의 일부만, 증거금 상한 고정.
    "pyramid_max_adds":       2,      # 종목당 최대 추가 횟수
    "pyramid_step_atr":       0.5,    # 직전 진입가에서 0.5×ATR 더 유리하게 갔을 때 추가
    "pyramid_min_profit":     0.01,   # 미실현 +1%(가격) 이상 이익일 때만 추가
    "pyramid_add_fraction":   0.5,    # 추가분 = 현재 수량의 50%
    "pyramid_max_margin_pct": 0.25,   # 불타기 포함 종목당 최대 증거금 비중
}

# 포지션 모니터(1분 주기)가 이 시간(초) 이상 한 바퀴도 못 돌면 '응답 없음'으로 보고 경보한다.
# 정상 주기 60초의 약 5배 → 일시 지연엔 반응하지 않고 실제 행(hang)만 잡는다.
MONITOR_STALE_SEC = 300


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
        # 일일 손실 서킷브레이커 상태
        self._day               = None   # 기준일(date) — 날짜 바뀌면 리셋
        self._day_start_equity  = None   # 당일 시작 시점 총자산(USDT)
        self._halted            = False  # True면 당일 신규 진입/불타기 중단
        # 포지션 모니터 헬스체크: 응답 없음 경보 스팸 방지용 마지막 경보 시각
        self._last_monitor_warn = 0.0

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

    def _update_circuit_breaker(self):
        """일일 자산 낙폭이 한도를 넘으면 당일 신규 진입을 중단한다(서킷브레이커).

        기준일이 바뀌면 시작 자산을 다시 기록하고 중단을 해제한다.
        기존 포지션의 SL/TP는 거래소에 그대로 두므로 손절/익절은 정상 작동한다.
        자산 조회 실패 시에는 상태를 바꾸지 않고 넘어간다(일시 오류로 매매를 막지 않음).
        """
        from datetime import date
        try:
            equity = self.broker.get_total_equity()
        except Exception as e:
            logger.warning(f"서킷브레이커 자산 조회 실패(무시): {e}")
            return

        today = date.today()
        if today != self._day:
            # 날짜 변경 → 당일 기준 자산 리셋, 중단 해제
            self._day              = today
            self._day_start_equity = equity
            self._halted           = False
            return
        if not self._day_start_equity or self._day_start_equity <= 0:
            self._day_start_equity = equity
            return

        dd = (self._day_start_equity - equity) / self._day_start_equity
        if dd >= CONFIG["daily_drawdown_limit"] and not self._halted:
            self._halted = True
            print(f"  ⛔ 일일 손실 한도 도달 (-{dd*100:.1f}%) → 오늘 신규 진입 중단")
            self.telegram.send(
                f"⛔ <b>일일 손실 한도 도달</b>\n"
                f"오늘 자산 {self._day_start_equity:.2f} → {equity:.2f} USDT "
                f"(-{dd*100:.1f}%)\n"
                f"당일 신규 진입/불타기를 중단합니다. (기존 포지션 SL/TP는 유지)"
            )

    def _check_monitor_health(self):
        """포지션 모니터 스레드를 점검한다.

        · 죽어 있으면(thread 종료) 재시작 + 텔레그램 알림.
        · 살아있지만 오래(MONITOR_STALE_SEC) 한 바퀴도 못 돌았으면(행 의심) 경보만 한다.
          (행 의심 스레드를 강제로 재시작하면 중복 스레드가 생겨 더 위험하므로 경보만.)
        SL/TP는 거래소에 등록돼 있어 모니터가 멈춰도 손절은 작동하지만,
        부분 익절·급변동 감지가 조용히 멈추는 것을 막기 위한 안전장치다.
        """
        try:
            if not self.monitor.is_alive():
                self.monitor.restart()
                self.telegram.send("⚠️ 포지션 모니터 스레드가 중단돼 자동 재시작했습니다.")
                return
            stale = self.monitor.seconds_since_beat()
            if stale > MONITOR_STALE_SEC and (time.time() - self._last_monitor_warn) > 1800:
                self._last_monitor_warn = time.time()
                self.telegram.send(
                    f"⚠️ 포지션 모니터 응답 없음({stale/60:.0f}분) — 수동 확인 권장.\n"
                    f"(거래소 SL/TP는 유지되나 부분익절·급변동 감지가 멈췄을 수 있음)"
                )
        except Exception as e:
            logger.error(f"모니터 헬스체크 오류: {e}")

    def _run_cycle(self):
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        print(f"\n[{now}] 사이클 시작 {'─'*40}")

        # ── 0. 일일 손실 서킷브레이커 + 모니터 생존 점검 ─────────────
        self._update_circuit_breaker()
        self._check_monitor_health()

        # ── 1. 매크로 확인 ───────────────────────────────────────────
        macro = self._get_macro_state()
        print(f"  F&G={macro['fg_value']:.0f}({macro['fg_class']})  "
              f"VIX={macro['vix']:.1f}  배율={macro['modifier']:.2f}x  "
              f"[{macro['reason']}]")

        # ── 2. 포지션 갱신 ───────────────────────────────────────────
        # 헤지 모드에선 같은 심볼에 롱·숏이 동시에 존재할 수 있으므로 (symbol, side)로 키를 잡는다.
        # (예전엔 p.symbol만 키로 써서 같은 심볼의 롱/숏 중 하나가 덮어써지며
        #  포지션 오판·이중 진입·불타기 방향 오적용 위험이 있었다.)
        open_positions = {(p.symbol, p.side): p for p in self.broker.get_positions()}
        n_open = len(open_positions)
        # 청산된 종목의 불타기 상태 정리 (다음 진입 시 0부터)
        open_symbols = {sym for (sym, _side) in open_positions}
        for sym in list(self._pyramid_state):
            if sym.replace("-USD", "USDT") not in open_symbols:
                self._pyramid_state.pop(sym, None)
        print(f"  포지션: {n_open}개")
        for (_sym, _side), p in open_positions.items():
            pnl_sign = "+" if p.unrealized_pnl >= 0 else ""
            print(f"    {p.symbol} {p.side} x{p.leverage} | "
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
            # fail-closed: 리스크 게이트인 매크로를 못 읽으면 신규 진입을 '보류'한다.
            # (전엔 allow_long/short=True로 fail-open이라, 매크로 API가 죽으면 위험장에서도
            #  진입이 열렸다. 기존 포지션은 거래소 SL/TP가 지키므로 신규만 막으면 된다.
            #  다음 사이클에 매크로가 복구되면 자동으로 다시 진입이 허용된다.)
            logger.warning(f"매크로 조회 실패 → 신규 진입 보류(fail-closed): {e}")
            return {
                "fg_value": 50, "fg_class": "N/A", "vix": 20,
                "modifier": 0.0, "allow_long": False, "allow_short": False,
                "reason": "매크로 조회 실패(보수적 차단)",
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
          · 손절선: 추가 직후 '새 평단 − 1ATR'(롱)/'+ 1ATR'(숏)로 직접 끌어올린다.
                    위로만(보호 방향) 이동하고, 즉시 손절될 위치면 보류한다.
                    (이익 래칫 모니터가 꺼져 있어도 늘어난 물량이 방어된다.)
          · 익절선: 추가 직후 새 평단 기준(평단 + 8×ATR)으로 재설정
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

        # 새 평단 기준으로 익절선·손절선 재설정.
        # 불타기로 수량이 늘면 손실 한 방도 커지므로, 손절선을 새 평단 기준으로 끌어올려
        # 늘어난 물량을 방어한다. (이익 래칫 모니터가 꺼져 있어도 SL이 따라간다.)
        time.sleep(1)
        newpos = next((p for p in self.broker.get_positions(symbol) if p.side == pos.side), None)
        if newpos:
            new_tp = self.lm.calculate_take_profit(newpos.avg_price, atr, side_str)
            self.broker.update_take_profit(symbol, side_str, new_tp)

            # 손절선: 새 평단 − 1ATR(롱) / + 1ATR(숏). 위로만(보호 방향으로만) 이동시키고,
            # 즉시 손절될 위치(현재가 너머)면 보류한다.
            new_sl   = self.lm.calculate_stop_loss(newpos.avg_price, atr, side_str)
            cur_sl   = newpos.stop_loss if (newpos.stop_loss and newpos.stop_loss > 0) else None
            mark     = newpos.mark_price or price
            safe     = (new_sl < mark) if long else (new_sl > mark)
            improves = cur_sl is None or (new_sl > cur_sl if long else new_sl < cur_sl)
            sl_line  = "손절 유지(개선폭 없음)"
            if safe and improves:
                if self.broker.update_stop_loss(symbol, side_str, new_sl):
                    sl_line = f"손절 → ${new_sl:,.4f} (새 평단 기준 상향)"
                else:
                    sl_line = "손절 이동 실패 — 수동 확인 필요"
            elif not safe:
                sl_line = "손절 이동 보류(현재가 근접)"

            self.telegram.send(
                f"🔺 <b>불타기 추가 ({state['adds']}차)</b>  {symbol} {pos.side}\n"
                f"추가 명목 {add_notional:.1f} USDT @ ${price:,.4f}\n"
                f"새 평단 ${newpos.avg_price:,.4f}  (수량 {newpos.size})\n"
                f"익절 → ${new_tp:,.4f}  ·  {sl_line}"
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

        # 일일 손실 서킷브레이커 발동 시: 신규 진입·불타기 모두 차단.
        # (기존 포지션의 청산은 거래소 SL/TP·모니터가 계속 처리하므로 여기서만 막으면 된다.)
        if self._halted:
            print(f"  {symbol}: 일일 손실 한도 초과 → 신규 진입/추가 차단")
            return

        bybit_sym = symbol.replace("-USD", "USDT")
        has_long  = (bybit_sym, "Buy")  in open_positions
        has_short = (bybit_sym, "Sell") in open_positions
        direction = "롱" if signal == 1 else "숏"

        # 중복 포지션: 신규 진입 대신 불타기(피라미딩) 시도 (방향별 포지션을 정확히 전달)
        if signal == 1 and has_long:
            self._try_pyramid(open_positions.get((bybit_sym, "Buy")), atr, macro)
            return
        if signal == -1 and has_short:
            self._try_pyramid(open_positions.get((bybit_sym, "Sell")), atr, macro)
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

        # ── 청산가 안전 점검 ─────────────────────────────────────────
        # SL이 강제청산가에 너무 가까우면(버퍼 15% 미만) 레버리지를 한 단계씩 낮춰
        # 청산가를 진입가에서 멀린다(SL은 ATR 기준이라 레버리지와 무관하게 고정).
        # 최저 레버리지(1x)에서도 안전거리가 안 나오면 그 거래는 건너뛴다.
        while leverage > self.lm.MIN_LEVERAGE:
            liq = self.lm.estimate_liquidation_price(price, leverage, side_str)
            if self.lm.liquidation_safe(price, stop_loss, liq):
                break
            leverage -= 1
        liq = self.lm.estimate_liquidation_price(price, leverage, side_str)
        if not self.lm.liquidation_safe(price, stop_loss, liq):
            reason = "청산가 안전거리 미달 (SL이 강제청산가에 근접)"
            print(f"  {symbol}: {direction} 신호 → {reason}")
            self._notify_block_once(symbol, direction, reason)
            return

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

# -*- coding: utf-8 -*-
"""
실시간 포지션 모니터 (1분마다 실행)

멀티 타임프레임(1m / 3m / 5m) 캔들을 Bybit 실시간 API로 체크해
급등·급락 감지, 부분 익절, BE SL 이동을 자동으로 수행합니다.

[로직]
  매 60초:
    1. 오픈 포지션 조회
    2. 각 포지션에 대해 1m·3m·5m 캔들 분석
    3. 역방향 급변동 감지 (롱=하락, 숏=상승):
         1 TF 감지 → 텔레그램 경보
         2 TF 감지 → 30% 부분 익절(손실제한) + 경보
         3 TF 감지 → 50% 부분 익절 + SL → BE + 경보
    4. 유리 방향 급변동 감지 (롱=상승, 숏=하락):
         1 TF 감지 → 텔레그램 경보
         2 TF 감지 → 25% 부분 익절(이익확보) + 경보
         3 TF 감지 → 40% 부분 익절 + SL → BE + 경보
    5. BE 자동 이동:
         미실현 수익 >= +BE_TRIGGER_PCT → SL → 진입가
"""

import time
import threading
from datetime import datetime
from typing import Dict, Optional

from broker.bybit_futures_broker import BybitFuturesBroker, FuturesPositionInfo
from utils.telegram_notifier import TelegramNotifier
from utils.logger import get_logger

logger = get_logger(__name__)

# ── 파라미터 ────────────────────────────────────────────────────────

# 역방향 급변동 임계값 (캔들 내 Open→Low/High 변화율)
FLASH_THRESHOLDS = {
    "1m": 1.0,   # 1분봉 1.0% 이상 역방향 = 급변동 감지
    "3m": 1.3,   # 3분봉 1.3% 이상
    "5m": 1.5,   # 5분봉 1.5% 이상
}
ALERT_THRESHOLD = 0.5   # 0.5% 이상 역방향 = 경보만 (청산 없음)

# 부분 청산 비율: 감지된 TF 수에 따라
ADVERSE_CLOSE = {2: 0.30, 3: 0.50}    # 역방향: 2TF=30%, 3TF=50%
FAVORABLE_CLOSE = {2: 0.25, 3: 0.40}  # 유리방향: 2TF=25%, 3TF=40%

BE_TRIGGER_PCT = 1.0    # 미실현 수익 +1.0%(가격 기준) 이상이면 보호 손절 시작
BE_RETRY_COOLDOWN = 600 # 보호 손절 이동 실패 시 재시도 보류 시간(초) — 무한 재시도/로그 폭주 방지
MONITOR_INTERVAL = 60   # 초

# ── 수수료 보정 BE + ATR 트레일링 손절 ──────────────────────────────
# 거래소(본전=진입가)로만 옮기면 왕복 수수료 때문에 손절 시 실제로는 마이너스다.
# 그래서 ①진입가 대신 '수수료 + 증거금 +1% 순이익' 지점으로 옮기고(수수료 보정 BE),
# ②수익이 더 오르면 ATR 기반으로 손절을 따라 올려(트레일링) 수익을 추가로 잠근다.
FEE_ROUNDTRIP   = 0.0011   # 왕복 테이커 수수료 (0.055% × 2, 가격 대비 비율)
BE_LOCK_ROI     = 0.01     # 수수료 보정 BE 시 잠글 증거금 대비 순이익 (+1%)

TRAIL_ACTIVATE_PCT     = 1.0   # 트레일링 시작 미실현 수익(가격 기준 %)
TRAIL_ATR_INTERVAL_MIN = 60    # ATR 계산용 캔들 주기 (1시간)
TRAIL_ATR_PERIOD       = 14    # ATR 평균 기간
TRAIL_ATR_MULT         = 2.5   # 고점에서 트레일 거리 = 2.5 × ATR (작을수록 촘촘=조기청산↑)
TRAIL_MIN_STEP_PCT     = 0.1   # SL 갱신 최소 개선폭(가격 %) — 잦은 갱신/API 스팸 방지
ATR_CACHE_SEC          = 300   # 같은 포지션 ATR 재조회 최소 간격(초)
PROT_MSG_COOLDOWN      = 600   # 보호 손절 텔레그램 알림 쿨다운(초)


class PositionMonitor:
    """1분 주기 포지션 실시간 모니터"""

    def __init__(self, broker: BybitFuturesBroker, telegram: TelegramNotifier,
                 on_position_closed=None):
        self.broker   = broker
        self.telegram = telegram
        # 포지션 청산 감지 시 호출할 콜백 (메인 루프 즉시 사이클 트리거용)
        self.on_position_closed = on_position_closed
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # 포지션별 상태 기억 (symbol_side → dict)
        self._state: Dict[str, dict] = {}

    # ── 외부 제어 ───────────────────────────────────────────────────

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("포지션 모니터 시작 (1분 주기, 1m·3m·5m 멀티 TF)")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)

    # ── 메인 루프 ───────────────────────────────────────────────────

    def _loop(self):
        while self._running:
            try:
                positions = self.broker.get_positions()
                if positions:
                    for pos in positions:
                        self._check_position(pos)
                        # 청산 알림용 마지막 스냅샷 저장
                        self._get_state(pos)["snapshot"] = {
                            "entry": pos.avg_price,
                            "mark": pos.mark_price,
                            "size": pos.size,
                            "leverage": pos.leverage,
                            "tp": pos.take_profit,
                            "sl": pos.stop_loss,
                        }
                # 사라진 포지션 = 청산됨 (TP/SL 거래소 체결 포함) → 알림 후 상태 정리
                active_keys = {self._key(p) for p in positions}
                closed_any = False
                for k in list(self._state.keys()):
                    if k not in active_keys:
                        state = self._state.pop(k)
                        closed_any = True
                        try:
                            self._notify_position_closed(k, state)
                        except Exception as e:
                            logger.error(f"청산 알림 실패 ({k}): {e}", exc_info=True)
                # 포지션이 청산되면 메인 루프를 깨워 즉시 신규 매수 기회 탐색
                if closed_any and self.on_position_closed:
                    try:
                        self.on_position_closed()
                    except Exception as e:
                        logger.error(f"청산 콜백 오류: {e}")
            except Exception as e:
                logger.error(f"모니터 오류: {e}", exc_info=True)
            time.sleep(MONITOR_INTERVAL)

    # ── 청산 감지 알림 ──────────────────────────────────────────────

    def _notify_position_closed(self, key: str, state: dict):
        """추적 중이던 포지션이 사라지면 실현손익을 조회해 익절/손절 알림을 보낸다."""
        symbol, side = key.rsplit("_", 1)          # 예: "BTCUSDT", "Buy"
        exit_side = "Sell" if side == "Buy" else "Buy"  # 청산 주문은 반대 방향
        snap = state.get("snapshot") or {}

        # Bybit 실현손익 내역에서 직전 청산 레코드 검색 (최근 10분 이내)
        record = None
        try:
            for r in self.broker.get_closed_pnl(symbol, limit=10):
                if r.get("symbol") != symbol or r.get("side") != exit_side:
                    continue
                updated_ms = float(r.get("updatedTime", 0))
                if time.time() * 1000 - updated_ms <= 10 * 60 * 1000:
                    record = r
                    break
        except Exception as e:
            logger.warning(f"실현손익 조회 실패 ({symbol}): {e}")

        direction = "롱" if side == "Buy" else "숏"

        if record is None:
            # 내역을 못 찾아도 청산 사실 자체는 알린다
            msg = (
                f"🔔 <b>포지션 청산 감지</b>  {symbol} {direction}\n"
                f"거래소에서 포지션이 종료되었습니다 (TP/SL 또는 수동 청산).\n"
                f"마지막 진입가: ${snap.get('entry', 0):,.4f}"
            )
            self.telegram.send(msg)
            logger.info(f"청산 감지 (내역 미확인): {key}")
            return

        entry = float(record.get("avgEntryPrice", 0))
        exit_p = float(record.get("avgExitPrice", 0))
        qty = float(record.get("qty", 0))
        pnl = float(record.get("closedPnl", 0))
        lev = int(float(record.get("leverage", snap.get("leverage", 1)) or 1))

        # 가격 변동률 및 증거금 대비 수익률(ROI)
        price_pct = 0.0
        if entry > 0:
            price_pct = (exit_p - entry) / entry * 100
            if side == "Sell":
                price_pct = -price_pct
        margin = entry * qty / lev if lev > 0 else 0
        roi_pct = pnl / margin * 100 if margin > 0 else 0

        # TP/SL 가격과 비교해 청산 사유 추정 (0.2% 허용 오차)
        cause = "청산"
        tp, sl = snap.get("tp", 0), snap.get("sl", 0)
        if tp and exit_p and abs(exit_p - tp) / tp < 0.002:
            cause = "TP 체결"
        elif sl and exit_p and abs(exit_p - sl) / sl < 0.002:
            cause = "SL 체결"

        if pnl >= 0:
            header = f"💰 <b>익절 ({cause})</b>"
        else:
            header = f"🛑 <b>손절 ({cause})</b>"

        msg = (
            f"{header}  {symbol} {direction} x{lev}\n"
            f"진입: ${entry:,.4f} → 청산: ${exit_p:,.4f} ({price_pct:+.2f}%)\n"
            f"수량: {qty}\n"
            f"실현손익: <b>{pnl:+.2f} USDT</b> (ROI {roi_pct:+.1f}%)"
        )
        self.telegram.send(msg)
        logger.info(f"청산 알림 전송: {key} pnl={pnl:+.2f} USDT ({cause})")

    # ── 포지션 분석 ─────────────────────────────────────────────────

    def _key(self, pos: FuturesPositionInfo) -> str:
        return f"{pos.symbol}_{pos.side}"

    def _get_state(self, pos: FuturesPositionInfo) -> dict:
        k = self._key(pos)
        if k not in self._state:
            self._state[k] = {"be_moved": False, "last_alert_time": 0}
        return self._state[k]

    # ── 수수료 보정 BE + ATR 트레일링 손절 ──────────────────────────

    def _atr_cached(self, symbol_yf: str, state: dict, now_ts: float) -> Optional[float]:
        """ATR을 ATR_CACHE_SEC 간격으로만 재조회(캐시)해 API 부하/지연을 줄인다."""
        if state.get("atr") and now_ts - state.get("atr_ts", 0) < ATR_CACHE_SEC:
            return state["atr"]
        atr = self.broker.get_atr(symbol_yf, TRAIL_ATR_INTERVAL_MIN, TRAIL_ATR_PERIOD)
        if atr and atr > 0:
            state["atr"]    = atr
            state["atr_ts"] = now_ts
        return atr

    def _ratchet_stop(self, pos: FuturesPositionInfo, symbol_yf: str, side: str,
                      entry: float, mark: float, unreal_pct: float,
                      state: dict, now_ts: float):
        """손절선을 위로만(이익을 더 지키는 방향) 끌어올린다.

        ① 미실현 +BE_TRIGGER_PCT% 이상 → '진입가 + 수수료 + 증거금 +1%' 지점 (수수료 보정 BE)
        ② 미실현 +TRAIL_ACTIVATE_PCT% 이상 → 고점 ∓ TRAIL_ATR_MULT×ATR (ATR 트레일링)
        두 후보 중 더 유리한 값을 택하고, 기존 SL보다 최소폭 이상 개선될 때만 거래소에 반영한다.
        """
        long     = side == "long"
        leverage = max(int(pos.leverage or 1), 1)

        # 고점(롱)/저점(숏) 추적 — 트레일링 기준점
        peak = state.get("peak")
        peak = mark if peak is None else (max(peak, mark) if long else min(peak, mark))
        state["peak"] = peak

        candidates = []

        # ① 수수료 보정 BE: 손절돼도 증거금 +1% 순이익이 남는 가격
        if unreal_pct >= BE_TRIGGER_PCT:
            off = BE_LOCK_ROI / leverage + FEE_ROUNDTRIP
            candidates.append(entry * (1 + off) if long else entry * (1 - off))

        # ② ATR 트레일링: 고점에서 2.5 ATR 떨어진 가격 (코인 변동성에 자동 적응)
        if unreal_pct >= TRAIL_ACTIVATE_PCT:
            atr = self._atr_cached(symbol_yf, state, now_ts)
            if atr:
                candidates.append(peak - TRAIL_ATR_MULT * atr if long
                                  else peak + TRAIL_ATR_MULT * atr)

        if not candidates:
            return

        desired = max(candidates) if long else min(candidates)

        # 현재가 너머(즉시 손절될 위치)면 보류
        if (long and desired >= mark) or (not long and desired <= mark):
            return

        # 기존 거래소 SL보다 최소 개선폭 이상 좋아질 때만 갱신 (잦은 갱신/API 스팸 방지)
        cur_sl   = pos.stop_loss if (pos.stop_loss and pos.stop_loss > 0) else None
        min_step = mark * (TRAIL_MIN_STEP_PCT / 100)
        improved = cur_sl is None or (desired > cur_sl + min_step if long
                                      else desired < cur_sl - min_step)
        if not improved:
            return

        # 직전 갱신이 실패했고 쿨다운 중이면 보류 (무한 재시도 방어)
        if state.get("_update_failed") and now_ts - state.get("last_be_attempt", 0) < BE_RETRY_COOLDOWN:
            return
        state["last_be_attempt"] = now_ts

        ok = self.broker.update_stop_loss(symbol_yf, side, desired)
        if not ok:
            state["_update_failed"] = True
            logger.warning(f"보호 손절 이동 실패 ({pos.symbol} {side}) — {BE_RETRY_COOLDOWN//60}분 후 재시도")
            return
        state["_update_failed"] = False

        first = not state.get("be_moved")
        state["be_moved"] = True

        # 확정 순이익(손절 체결 시) — 가격% 및 증거금 ROI%
        lock_price_pct = ((desired - entry) / entry * 100) if long else ((entry - desired) / entry * 100)
        net_price_pct  = lock_price_pct - FEE_ROUNDTRIP * 100
        net_roi_pct    = net_price_pct * leverage
        kind = "수수료 보정 BE" if first else "트레일링 ↑"
        logger.info(
            f"보호 손절 {kind}: {pos.symbol} {side} SL=${desired:.4f} "
            f"(확정 순이익 +{net_price_pct:.2f}% 가격 / +{net_roi_pct:.1f}% 증거금)"
        )

        # 텔레그램: 최초 이동은 즉시, 이후 트레일링은 쿨다운 두고 알림(스팸 방지)
        if first or now_ts - state.get("last_prot_msg", 0) > PROT_MSG_COOLDOWN:
            state["last_prot_msg"] = now_ts
            emoji = "🛡" if first else "⏫"
            self.telegram.send(
                f"{emoji} <b>보호 손절 {kind}</b>  {pos.symbol} {pos.side}\n"
                f"SL → ${desired:.4f}  (진입 ${entry:.4f})\n"
                f"확정 순이익: +{net_price_pct:.2f}% 가격 / +{net_roi_pct:.1f}% 증거금\n"
                f"현재 미실현: +{unreal_pct:.2f}%  (${mark:.4f})"
            )

    def _check_position(self, pos: FuturesPositionInfo):
        symbol_yf = pos.symbol.replace("USDT", "-USD")
        side      = "long" if pos.side == "Buy" else "short"
        entry     = pos.avg_price
        mark      = pos.mark_price
        state     = self._get_state(pos)

        if entry <= 0 or mark <= 0:
            return

        # 현재 미실현 수익률 (레버리지 미포함)
        unreal_pct = (
            (mark - entry) / entry * 100
            if side == "long"
            else (entry - mark) / entry * 100
        )

        now_str = datetime.now().strftime("%H:%M")
        now_ts  = time.time()

        # ── 1. 수수료 보정 BE + ATR 트레일링 손절 ───────────────────
        self._ratchet_stop(pos, symbol_yf, side, entry, mark, unreal_pct, state, now_ts)

        # ── 2. 멀티 TF 캔들 분석 ────────────────────────────────────
        adverse_count   = 0
        favorable_count = 0
        tf_details      = []

        for interval_min, threshold in sorted(FLASH_THRESHOLDS.items(), key=lambda x: int(x[0][:-1])):
            candle = self.broker.get_kline(symbol_yf, int(interval_min[:-1]))
            if not candle:
                continue

            o, h, l = candle["open"], candle["high"], candle["low"]

            if side == "long":
                adverse_pct   = (o - l) / o * 100 if o > 0 else 0  # 하락
                favorable_pct = (h - o) / o * 100 if o > 0 else 0  # 상승
            else:
                adverse_pct   = (h - o) / o * 100 if o > 0 else 0  # 상승
                favorable_pct = (o - l) / o * 100 if o > 0 else 0  # 하락

            if adverse_pct >= threshold:
                adverse_count += 1
                tf_details.append(f"{interval_min} 역방향 -{adverse_pct:.1f}%")
            elif adverse_pct >= ALERT_THRESHOLD:
                tf_details.append(f"{interval_min} 경보 -{adverse_pct:.1f}%")

            if favorable_pct >= threshold:
                favorable_count += 1

        # ── 3. 역방향 급변동 처리 ────────────────────────────────────
        cooldown = 180  # 같은 포지션에 대해 3분 내 중복 알림 방지

        if adverse_count >= 1 and now_ts - state.get("last_alert_time", 0) > cooldown:
            state["last_alert_time"] = now_ts

            if adverse_count == 1:
                # 경보만
                msg = (
                    f"⚠️ <b>급변동 경보</b>  {pos.symbol} {pos.side}\n"
                    f"[{', '.join(tf_details)}]\n"
                    f"PnL: {unreal_pct:+.2f}%  현재가: ${mark:.4f}"
                )
                self.telegram.send(msg)

            elif adverse_count in ADVERSE_CLOSE:
                ratio = ADVERSE_CLOSE[adverse_count]
                order = None
                try:
                    order = self.broker.close_partial(symbol_yf, side, ratio)
                except Exception as e:
                    logger.error(f"부분 청산 실패 ({pos.symbol}): {e}")

                action = f"{int(ratio*100)}% 부분 청산"
                emoji  = "🔴" if unreal_pct < 0 else "🟡"
                msg = (
                    f"{emoji} <b>급변동 {action}</b>  {pos.symbol} {pos.side}\n"
                    f"감지: {adverse_count}TF [{', '.join(tf_details)}]\n"
                    f"PnL: {unreal_pct:+.2f}%  현재가: ${mark:.4f}\n"
                )
                if order:
                    msg += f"청산: {order.qty}개 ({action})"
                if adverse_count == 3 and not state["be_moved"]:
                    ok = self.broker.update_stop_loss(symbol_yf, side, entry)
                    if ok:
                        state["be_moved"] = True
                        msg += f"\nSL → 진입가 ${entry:.4f} 이동"
                self.telegram.send(msg)
                logger.info(f"역방향 부분 청산: {pos.symbol} {side} {ratio*100:.0f}% ({adverse_count}TF)")

        # ── 4. 유리 방향 급변동 처리 (익절) ──────────────────────────
        if favorable_count >= 2 and now_ts - state.get("last_fav_time", 0) > cooldown:
            state["last_fav_time"] = now_ts
            ratio = FAVORABLE_CLOSE.get(favorable_count, 0)
            if ratio > 0:
                order = None
                try:
                    order = self.broker.close_partial(symbol_yf, side, ratio)
                except Exception as e:
                    logger.error(f"익절 부분 청산 실패 ({pos.symbol}): {e}")

                action = f"{int(ratio*100)}% 부분 익절"
                msg = (
                    f"💰 <b>{action}</b>  {pos.symbol} {pos.side}\n"
                    f"유리 급등락 {favorable_count}TF 동시 감지\n"
                    f"PnL: {unreal_pct:+.2f}%  현재가: ${mark:.4f}\n"
                )
                if order:
                    msg += f"익절: {order.qty}개 ({action})"
                if favorable_count == 3 and not state["be_moved"]:
                    ok = self.broker.update_stop_loss(symbol_yf, side, entry)
                    if ok:
                        state["be_moved"] = True
                        msg += f"\nSL → 진입가 ${entry:.4f} 이동"
                self.telegram.send(msg)
                logger.info(f"유리 방향 익절: {pos.symbol} {side} {ratio*100:.0f}% ({favorable_count}TF)")

        # ── 5. 정기 포지션 상태 로그 (5분마다) ───────────────────────
        if int(time.time()) % 300 < MONITOR_INTERVAL:
            logger.info(
                f"포지션 체크: {pos.symbol} {pos.side} x{pos.leverage} | "
                f"PnL={unreal_pct:+.2f}% | BE={'O' if state['be_moved'] else 'X'}"
            )

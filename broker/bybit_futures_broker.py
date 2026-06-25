"""
Bybit 선물 브로커 (USDT 퍼페츄얼)

pybit v5 API를 사용해 Bybit 실계좌/테스트넷에 연결합니다.

사용 전 준비:
    1. Bybit 계정에서 API 키 발급 (Read + Trade 권한, Withdraw 제외)
    2. 환경변수 설정:
           BYBIT_API_KEY=your_key
           BYBIT_API_SECRET=your_secret
           BYBIT_TESTNET=true   (테스트넷) / false (실계좌)

포지션 모드:
    Hedge Mode 권장 — 롱/숏 동시 보유 가능.
    Bybit 웹사이트 설정 → [선호도] → [포지션 모드] → [헤지 모드] 로 변경.
"""

import os
import math
import time
import functools
import threading
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

import pybit._helpers as _pybit_helpers
from pybit.unified_trading import HTTP
from utils.logger import get_logger

logger = get_logger(__name__)


def _resilient(func):
    """Bybit API 호출 래퍼.

    1) 호출 전 서버 시간 동기화가 오래됐으면 자동 재동기화(_maybe_resync)한다.
    2) 타임스탬프 계열 오류(ErrCode 10002 / 재시도 초과)가 나면 강제 재동기화 후 1회 재시도한다.
       PC 시계 드리프트로 req_timestamp가 서버보다 앞서면 pybit의 recv_window 재시도로는
       복구되지 않으므로(규칙: req <= server + 1000ms), 오프셋을 다시 맞춘 뒤 재시도해야 한다.
    """
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        self._maybe_resync()
        try:
            return func(self, *args, **kwargs)
        except Exception as e:
            if self._is_timestamp_error(e):
                logger.warning(
                    f"타임스탬프 오류 감지 → 서버 시간 재동기화 후 재시도: {func.__name__} ({e})"
                )
                self._sync_server_time(force=True)
                return func(self, *args, **kwargs)
            raise
    return wrapper


# ── 심볼 변환 테이블 (yfinance → Bybit) ─────────────────────────────
SYMBOL_MAP = {
    "BTC-USD":   "BTCUSDT",
    "ETH-USD":   "ETHUSDT",
    "BNB-USD":   "BNBUSDT",
    "SOL-USD":   "SOLUSDT",
    "XRP-USD":   "XRPUSDT",
    "ADA-USD":   "ADAUSDT",
    "DOGE-USD":  "DOGEUSDT",
    "AVAX-USD":  "AVAXUSDT",
    "DOT-USD":   "DOTUSDT",
    "MATIC-USD": "MATICUSDT",
    "LINK-USD":  "LINKUSDT",
    "LTC-USD":   "LTCUSDT",
    "UNI-USD":   "UNIUSDT",
    "ATOM-USD":  "ATOMUSDT",
    "TRX-USD":   "TRXUSDT",
}

# 코인별 최소 수량 단위 (lot size)
LOT_SIZE = {
    "BTCUSDT": 0.001,
    "ETHUSDT": 0.01,
    "BNBUSDT": 0.01,
    "SOLUSDT": 0.1,
    "XRPUSDT": 1.0,
    "ADAUSDT": 1.0,
    "DOGEUSDT": 1.0,
    "AVAXUSDT": 0.1,
    "DOTUSDT": 0.1,
    "MATICUSDT": 1.0,
    "LINKUSDT": 0.1,
    "LTCUSDT": 0.01,
    "UNIUSDT": 0.1,
    "ATOMUSDT": 0.1,
    "TRXUSDT": 1.0,
}


@dataclass
class FuturesOrder:
    """선물 주문 결과"""
    order_id: str
    symbol: str
    side: str           # "Buy" or "Sell"
    qty: float
    price: float
    order_type: str     # "Market" or "Limit"
    status: str         # "New", "Filled", "Cancelled"
    filled_price: float = 0.0
    created_at: str = ""
    pnl: float = 0.0


@dataclass
class FuturesPositionInfo:
    """현재 보유 포지션 정보"""
    symbol: str
    side: str           # "Buy"(롱) or "Sell"(숏)
    size: float         # 수량 (코인)
    avg_price: float    # 평균 진입가
    mark_price: float   # 현재 시장가
    unrealized_pnl: float
    leverage: int
    stop_loss: float
    take_profit: float


class BybitFuturesBroker:
    """
    Bybit USDT 퍼페츄얼 선물 브로커

    전략이 출력하는 포지션(USD 명목 기준)을 Bybit API 주문으로 변환합니다.
    손절/익절 주문을 진입과 동시에 등록해 리스크를 자동 관리합니다.

    Args:
        api_key:    Bybit API 키 (환경변수 BYBIT_API_KEY 사용 가능)
        api_secret: Bybit API 시크릿 (환경변수 BYBIT_API_SECRET 사용 가능)
        testnet:    True = 테스트넷, False = 실계좌
    """

    CATEGORY = "linear"  # USDT 퍼페츄얼 선물

    # 시계 드리프트 대비: 이 주기(초)가 지나면 다음 API 호출 시 서버 시간을 다시 맞춘다.
    RESYNC_INTERVAL = 180
    # 우리 타임스탬프를 서버보다 이만큼(ms) 뒤로 둬 'req <= server + 1000ms' 위반을 구조적으로 막는다.
    # recv_window 기본값(5000ms) 안에 충분히 들어오므로 과거로 치우쳐도 안전하다.
    SAFETY_MARGIN_MS = 1000

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        testnet: Optional[bool] = None,
    ):
        self.api_key    = api_key    or os.environ.get("BYBIT_API_KEY", "")
        self.api_secret = api_secret or os.environ.get("BYBIT_API_SECRET", "")

        if testnet is None:
            testnet = os.environ.get("BYBIT_TESTNET", "true").lower() != "false"
        self.testnet = testnet

        if not self.api_key or not self.api_secret:
            raise ValueError(
                "BYBIT_API_KEY 와 BYBIT_API_SECRET 환경변수를 설정하세요.\n"
                "  예) $env:BYBIT_API_KEY='your_key'\n"
                "      $env:BYBIT_API_SECRET='your_secret'"
            )

        self._session = HTTP(
            testnet=self.testnet,
            api_key=self.api_key,
            api_secret=self.api_secret,
        )
        self._tick_decimals_cache: dict = {}

        # 서버 시간 보정 상태. generate_timestamp는 self._time_offset을 동적으로 읽으므로
        # 패치는 한 번만 하고, 이후 재동기화는 self._time_offset 값만 갱신한다.
        # 메인 루프와 포지션 모니터(별도 스레드)가 동시에 재동기화하지 않도록 락으로 보호한다.
        self._time_offset = 0
        self._last_sync = 0.0
        self._sync_lock = threading.Lock()
        _pybit_helpers.generate_timestamp = lambda: int(time.time() * 1000) + self._time_offset
        self._sync_server_time(force=True)

        mode = "테스트넷" if self.testnet else "실계좌"
        logger.info(f"Bybit 브로커 연결 ({mode})")

    # ── 내부 유틸리티 ────────────────────────────────────────────────

    def _sync_server_time(self, force=False):
        """PC 시계와 Bybit 서버 시간 차이를 측정해 타임스탬프 보정 오프셋(self._time_offset)을 갱신한다.

        ErrCode 10002: req_timestamp > server_timestamp + 1000ms 이면 Bybit가 요청을 거부한다.
        recv_window 조정으로는 해결 불가 (Bybit 규칙: 요청 타임스탬프 <= 서버시간 + 1000ms).
        그래서 보정 오프셋에 SAFETY_MARGIN_MS만큼 음수 바이어스를 줘 우리 타임스탬프를 항상
        서버보다 약간 뒤로 둔다(앞섬 위반 차단). 단 한 번 측정한 오프셋은 시계 드리프트로 곧
        낡으므로, RESYNC_INTERVAL마다(또는 force=True 시) 다시 측정한다.

        메인 루프와 포지션 모니터 스레드가 동시에 호출할 수 있어 락으로 보호한다.
        """
        with self._sync_lock:
            # 락 진입 사이에 다른 스레드가 이미 갱신했으면(최근 동기화) 중복 네트워크 호출을 피한다.
            if not force and (time.time() - self._last_sync) < self.RESYNC_INTERVAL:
                return
            try:
                t0 = int(time.time() * 1000)
                resp = self._session.get_server_time()
                t1 = int(time.time() * 1000)
                # timeNano로 ms 정밀도 확보, RTT 절반을 빼서 네트워크 지연 보정
                server_ms = int(resp["result"]["timeNano"]) // 1_000_000
                local_mid = t0 + (t1 - t0) // 2
                raw_offset = server_ms - local_mid
                # 서버보다 SAFETY_MARGIN_MS만큼 뒤로 치우치게 해 'req <= server + 1000ms'를 항상 만족
                self._time_offset = raw_offset - self.SAFETY_MARGIN_MS
                self._last_sync = time.time()
                if abs(raw_offset) > 200:
                    logger.warning(
                        f"PC 시계 오차 {raw_offset:+d}ms — 보정 오프셋 {self._time_offset:+d}ms 적용"
                    )
            except Exception as e:
                logger.warning(f"서버 시간 동기화 실패 (무시): {e}")

    def _maybe_resync(self):
        """마지막 동기화 후 RESYNC_INTERVAL이 지났으면 서버 시간을 다시 맞춘다(드리프트 보정)."""
        if (time.time() - self._last_sync) >= self.RESYNC_INTERVAL:
            self._sync_server_time()

    @staticmethod
    def _is_timestamp_error(e: Exception) -> bool:
        """예외가 타임스탬프/시계 동기화 계열 오류인지 판별한다.

        최종 예외는 pybit의 'Bad request. retries exceeded maximum.'(ErrCode 400)로 떠
        10002 원문이 사라지므로, 재시도 초과 메시지도 함께 본다(재동기화+재시도는 무해)."""
        s = str(e).lower()
        return (
            "10002" in s
            or "timestamp" in s
            or "recv_window" in s
            or "retries exceeded maximum" in s
        )

    def _to_bybit_symbol(self, symbol: str) -> str:
        """BTC-USD → BTCUSDT"""
        return SYMBOL_MAP.get(symbol, symbol.replace("-USD", "USDT").replace("/", ""))

    def _floor_qty(self, symbol_bybit: str, qty: float) -> float:
        """최소 수량 단위로 내림"""
        step = LOT_SIZE.get(symbol_bybit, 0.001)
        return math.floor(qty / step) * step

    def _price_decimals(self, symbol_bybit: str) -> int:
        """심볼의 가격 틱 사이즈로부터 소수점 자릿수를 조회 (캐시)"""
        if symbol_bybit not in self._tick_decimals_cache:
            try:
                resp = self._session.get_instruments_info(category=self.CATEGORY, symbol=symbol_bybit)
                tick_size = resp["result"]["list"][0]["priceFilter"]["tickSize"]
                decimals = len(tick_size.split(".")[1].rstrip("0")) if "." in tick_size else 0
                self._tick_decimals_cache[symbol_bybit] = max(decimals, 2)
            except Exception as e:
                logger.warning(f"틱 사이즈 조회 실패 ({symbol_bybit}): {e}, 기본값 4 사용")
                self._tick_decimals_cache[symbol_bybit] = 4
        return self._tick_decimals_cache[symbol_bybit]

    def _price_str(self, price: float, symbol_bybit: str) -> str:
        decimals = self._price_decimals(symbol_bybit)
        return f"{price:.{decimals}f}"

    def _raise_if_error(self, resp: dict, action: str):
        if resp.get("retCode", -1) != 0:
            msg = resp.get("retMsg", "알 수 없는 오류")
            raise RuntimeError(f"Bybit API 오류 [{action}]: {msg}")

    @staticmethod
    def _safe_float(val, default: float = 0.0) -> float:
        """Bybit API 필드를 안전하게 float 변환한다.
        포지션을 수동 종료하면 API가 빈 문자열('')/None을 반환해 float()가
        크래시하므로(could not convert string to float: ''), 그 경우 default 반환."""
        try:
            if val is None or val == "":
                return default
            return float(val)
        except (ValueError, TypeError):
            return default

    # ── 계좌/시세 조회 ───────────────────────────────────────────────

    @_resilient
    def get_balance(self) -> float:
        """USDT 지갑 잔고 (거래 가능 금액)"""
        resp = self._session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        self._raise_if_error(resp, "잔고 조회")
        coins = resp["result"]["list"][0]["coin"]
        for c in coins:
            if c["coin"] == "USDT":
                # availableToWithdraw가 빈 문자열인 경우 walletBalance 사용
                val = c.get("availableToWithdraw") or c.get("walletBalance") or "0"
                return float(val) if val else 0.0
        return 0.0

    @_resilient
    def get_total_equity(self) -> float:
        """USDT 총 자산 (미실현 손익 포함)"""
        resp = self._session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        self._raise_if_error(resp, "총 자산 조회")
        val = resp["result"]["list"][0].get("totalEquity") or "0"
        return float(val) if val else 0.0

    @_resilient
    def get_price(self, symbol: str) -> float:
        """현재 시장가 (mark price)"""
        bybit_sym = self._to_bybit_symbol(symbol)
        resp = self._session.get_tickers(category=self.CATEGORY, symbol=bybit_sym)
        self._raise_if_error(resp, f"시세 조회 {symbol}")
        return self._safe_float(resp["result"]["list"][0]["markPrice"])

    @_resilient
    def get_positions(self, symbol: Optional[str] = None) -> List[FuturesPositionInfo]:
        """현재 보유 포지션 목록"""
        kwargs = {"category": self.CATEGORY, "settleCoin": "USDT"}
        if symbol:
            kwargs["symbol"] = self._to_bybit_symbol(symbol)

        resp = self._session.get_positions(**kwargs)
        self._raise_if_error(resp, "포지션 조회")

        result = []
        for p in resp["result"]["list"]:
            # 수동 종료된 포지션은 빈 문자열 필드를 반환 → _safe_float로 안전 처리
            size = self._safe_float(p.get("size"))
            if size == 0:
                continue
            result.append(FuturesPositionInfo(
                symbol=p["symbol"],
                side=p["side"],                         # "Buy" or "Sell"
                size=size,
                avg_price=self._safe_float(p.get("avgPrice")),
                mark_price=self._safe_float(p.get("markPrice")),
                unrealized_pnl=self._safe_float(p.get("unrealisedPnl")),
                leverage=int(self._safe_float(p.get("leverage"), 1)),
                stop_loss=self._safe_float(p.get("stopLoss")),
                take_profit=self._safe_float(p.get("takeProfit")),
            ))
        return result

    # ── 레버리지 설정 ─────────────────────────────────────────────────

    @_resilient
    def set_leverage(self, symbol: str, leverage: int) -> bool:
        """레버리지 설정 (롱/숏 동일 적용)"""
        bybit_sym = self._to_bybit_symbol(symbol)
        lev_str = str(leverage)
        try:
            resp = self._session.set_leverage(
                category=self.CATEGORY,
                symbol=bybit_sym,
                buyLeverage=lev_str,
                sellLeverage=lev_str,
            )
            if resp.get("retCode") == 110043:
                # 이미 같은 레버리지 설정 중 (정상)
                return True
            self._raise_if_error(resp, f"레버리지 설정 {symbol}")
            logger.info(f"{symbol} 레버리지 {leverage}x 설정 완료")
            return True
        except Exception as e:
            logger.warning(f"레버리지 설정 실패 ({symbol}): {e}")
            return False

    # ── 주문 실행 ────────────────────────────────────────────────────

    def open_long(
        self,
        symbol: str,
        usd_notional: float,
        leverage: int,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
    ) -> FuturesOrder:
        """
        롱 포지션 오픈 (시장가)

        Args:
            symbol:           종목 코드 (예: BTC-USD)
            usd_notional:     명목 포지션 크기 (USDT)
            leverage:         레버리지 (1~100)
            stop_loss_price:  손절가 (None이면 미설정)
            take_profit_price: 익절가 (None이면 미설정)

        Returns:
            FuturesOrder
        """
        return self._open_position("Buy", symbol, usd_notional, leverage,
                                   stop_loss_price, take_profit_price)

    def open_short(
        self,
        symbol: str,
        usd_notional: float,
        leverage: int,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
    ) -> FuturesOrder:
        """
        숏 포지션 오픈 (시장가)
        """
        return self._open_position("Sell", symbol, usd_notional, leverage,
                                   stop_loss_price, take_profit_price)

    @_resilient
    def _open_position(
        self,
        side: str,
        symbol: str,
        usd_notional: float,
        leverage: int,
        stop_loss_price: Optional[float],
        take_profit_price: Optional[float],
    ) -> FuturesOrder:
        bybit_sym = self._to_bybit_symbol(symbol)

        # 레버리지 설정
        self.set_leverage(symbol, leverage)

        # 현재가로 수량 계산
        price = self.get_price(symbol)
        raw_qty = usd_notional / price
        qty = self._floor_qty(bybit_sym, raw_qty)

        if qty <= 0:
            raise ValueError(
                f"{symbol} 수량 계산 오류: notional={usd_notional}, price={price}, qty={qty}"
            )

        order_params: dict = {
            "category": self.CATEGORY,
            "symbol": bybit_sym,
            "side": side,
            "orderType": "Market",
            "qty": str(qty),
            "timeInForce": "IOC",  # Immediate or Cancel (시장가)
            "positionIdx": 1 if side == "Buy" else 2,  # 헤지 모드: 1=롱, 2=숏
        }

        if stop_loss_price and stop_loss_price > 0:
            order_params["stopLoss"] = self._price_str(stop_loss_price, bybit_sym)
            order_params["slTriggerBy"] = "MarkPrice"

        if take_profit_price and take_profit_price > 0:
            order_params["takeProfit"] = self._price_str(take_profit_price, bybit_sym)
            order_params["tpTriggerBy"] = "MarkPrice"

        logger.info(
            f"{'롱' if side=='Buy' else '숏'} 진입: {symbol} {qty} @ 시장가 "
            f"(레버리지={leverage}x, SL={stop_loss_price}, TP={take_profit_price})"
        )

        resp = self._session.place_order(**order_params)
        self._raise_if_error(resp, f"주문 실행 {side} {symbol}")

        order_info = resp["result"]
        return FuturesOrder(
            order_id=order_info.get("orderId", ""),
            symbol=bybit_sym,
            side=side,
            qty=qty,
            price=price,
            order_type="Market",
            status="New",
            created_at=datetime.now().isoformat(),
        )

    @_resilient
    def close_position(
        self,
        symbol: str,
        side: str,  # "long" or "short"
    ) -> Optional[FuturesOrder]:
        """
        포지션 전량 청산 (시장가)

        Args:
            symbol: 종목 코드 (예: BTC-USD)
            side:   "long" 또는 "short"
        """
        bybit_sym = self._to_bybit_symbol(symbol)
        close_side = "Sell" if side == "long" else "Buy"
        pos_idx    = 1 if side == "long" else 2

        # 현재 포지션 수량 확인
        positions = self.get_positions(symbol)
        target = next((p for p in positions
                       if p.side == ("Buy" if side == "long" else "Sell")), None)

        if not target or target.size == 0:
            logger.warning(f"{symbol} {side} 포지션 없음, 청산 스킵")
            return None

        logger.info(f"청산: {symbol} {side} {target.size}개 @ 시장가")

        resp = self._session.place_order(
            category=self.CATEGORY,
            symbol=bybit_sym,
            side=close_side,
            orderType="Market",
            qty=str(target.size),
            timeInForce="IOC",
            positionIdx=pos_idx,
            reduceOnly=True,
        )
        self._raise_if_error(resp, f"청산 {side} {symbol}")

        order_info = resp["result"]
        return FuturesOrder(
            order_id=order_info.get("orderId", ""),
            symbol=bybit_sym,
            side=close_side,
            qty=target.size,
            price=target.mark_price,
            order_type="Market",
            status="New",
            created_at=datetime.now().isoformat(),
            pnl=target.unrealized_pnl,
        )

    @_resilient
    def update_stop_loss(
        self,
        symbol: str,
        side: str,
        stop_loss_price: float,
    ) -> bool:
        """기존 포지션의 손절가 변경"""
        bybit_sym = self._to_bybit_symbol(symbol)
        pos_idx   = 1 if side == "long" else 2
        try:
            resp = self._session.set_trading_stop(
                category=self.CATEGORY,
                symbol=bybit_sym,
                stopLoss=self._price_str(stop_loss_price, bybit_sym),
                slTriggerBy="MarkPrice",
                positionIdx=pos_idx,
            )
            self._raise_if_error(resp, f"손절 변경 {symbol}")
            return True
        except Exception as e:
            # ErrCode 34040 = "not modified": 손절선이 이미 요청값과 동일하다는 뜻.
            # 변경할 게 없을 뿐 목표 상태(SL=진입가 등)는 이미 달성됐으므로 성공으로 처리한다.
            # 이걸 실패로 보면 호출부(PositionMonitor BE 이동)가 매 60초 영원히 재시도하며
            # ERROR 로그를 폭주시킨다. (2026-06-25 LINK-USD 무한 재시도 버그 수정)
            if "34040" in str(e) or "not modified" in str(e):
                logger.info(f"손절 변경 생략 ({symbol}): 이미 목표값과 동일 (34040)")
                return True
            logger.error(f"손절 변경 실패 ({symbol}): {e}")
            return False

    # ── 연결 테스트 ──────────────────────────────────────────────────

    @_resilient
    def ping(self) -> dict:
        """연결 및 계좌 상태 확인"""
        balance = self.get_balance()
        equity  = self.get_total_equity()
        mode    = "테스트넷" if self.testnet else "실계좌"
        logger.info(f"Bybit 연결 OK [{mode}] | 잔고={balance:.2f} USDT | 총자산={equity:.2f} USDT")
        return {
            "connected": True,
            "mode": mode,
            "balance_usdt": balance,
            "equity_usdt": equity,
        }

    @_resilient
    def get_kline(self, symbol: str, interval_min: int, limit: int = 3) -> Optional[dict]:
        """최근 완성된 캔들 반환 (interval_min: 1, 3, 5 등)"""
        bybit_sym = self._to_bybit_symbol(symbol)
        try:
            resp = self._session.get_kline(
                category=self.CATEGORY,
                symbol=bybit_sym,
                interval=str(interval_min),
                limit=limit + 1,  # 미완성 봉 제외를 위해 +1
            )
            self._raise_if_error(resp, f"캔들 조회 {symbol} {interval_min}m")
            klines = resp["result"]["list"]
            if len(klines) < 2:
                return None
            # index 0 = 현재 미완성봉, index 1 = 직전 완성봉
            k = klines[1]
            return {
                "open":   float(k[1]),
                "high":   float(k[2]),
                "low":    float(k[3]),
                "close":  float(k[4]),
                "volume": float(k[5]),
            }
        except Exception as e:
            logger.warning(f"캔들 조회 실패 ({symbol} {interval_min}m): {e}")
            return None

    @_resilient
    def get_atr(self, symbol: str, interval_min: int = 60, period: int = 14) -> Optional[float]:
        """최근 period개 완성 캔들로 ATR(평균 실제 변동폭, 가격 단위)을 계산한다.

        True Range = max(고-저, |고-전봉종가|, |저-전봉종가|) 의 period봉 평균.
        트레일링 스탑 등 코인별 변동성 적응이 필요한 곳에서 사용한다.
        """
        bybit_sym = self._to_bybit_symbol(symbol)
        try:
            resp = self._session.get_kline(
                category=self.CATEGORY,
                symbol=bybit_sym,
                interval=str(interval_min),
                limit=period + 2,          # 미완성봉(+1) + 전봉종가용(+1)
            )
            self._raise_if_error(resp, f"ATR 캔들 {symbol} {interval_min}m")
            kl = resp["result"]["list"]    # 최신순(index 0 = 현재 미완성봉)
            if len(kl) < period + 2:
                return None
            # index 1..period = 완성봉, index i+1 = 그 직전봉(전봉종가용)
            trs = []
            for i in range(1, period + 1):
                high = float(kl[i][2])
                low  = float(kl[i][3])
                prev_close = float(kl[i + 1][4])
                tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
                trs.append(tr)
            return sum(trs) / len(trs) if trs else None
        except Exception as e:
            logger.warning(f"ATR 계산 실패 ({symbol} {interval_min}m): {e}")
            return None

    @_resilient
    def close_partial(
        self,
        symbol: str,
        side: str,       # "long" or "short"
        ratio: float,    # 0.0 ~ 1.0 (0.3 = 30% 청산)
    ) -> Optional[FuturesOrder]:
        """포지션 일부 청산 (시장가, reduceOnly)"""
        bybit_sym  = self._to_bybit_symbol(symbol)
        close_side = "Sell" if side == "long" else "Buy"
        pos_idx    = 1 if side == "long" else 2

        positions = self.get_positions(symbol)
        target = next(
            (p for p in positions if p.side == ("Buy" if side == "long" else "Sell")),
            None,
        )
        if not target or target.size == 0:
            logger.warning(f"{symbol} {side} 포지션 없음")
            return None

        lot  = LOT_SIZE.get(bybit_sym, 0.001)
        qty  = math.floor(target.size * ratio / lot) * lot
        if qty <= 0:
            logger.warning(f"{symbol} 부분 청산 수량 부족 (size={target.size}, ratio={ratio})")
            return None

        logger.info(f"부분 청산: {symbol} {side} {qty}/{target.size} ({ratio*100:.0f}%) @ 시장가")
        resp = self._session.place_order(
            category=self.CATEGORY,
            symbol=bybit_sym,
            side=close_side,
            orderType="Market",
            qty=str(qty),
            timeInForce="IOC",
            positionIdx=pos_idx,
            reduceOnly=True,
        )
        self._raise_if_error(resp, f"부분 청산 {side} {symbol}")
        info = resp["result"]
        return FuturesOrder(
            order_id=info.get("orderId", ""),
            symbol=bybit_sym,
            side=close_side,
            qty=qty,
            price=target.mark_price,
            order_type="Market",
            status="New",
            created_at=datetime.now().isoformat(),
            pnl=target.unrealized_pnl * ratio,
        )

    @_resilient
    def get_order_history(self, symbol: Optional[str] = None, limit: int = 50) -> List[dict]:
        """최근 주문 내역"""
        kwargs = {"category": self.CATEGORY, "limit": limit}
        if symbol:
            kwargs["symbol"] = self._to_bybit_symbol(symbol)
        resp = self._session.get_order_history(**kwargs)
        self._raise_if_error(resp, "주문 내역 조회")
        return resp["result"]["list"]

    @_resilient
    def get_closed_pnl(self, symbol: Optional[str] = None, limit: int = 20) -> List[dict]:
        """최근 청산(실현손익) 내역 — TP/SL 등 거래소 측 체결도 포함된다.

        반환 레코드 주요 필드: symbol, side(청산 주문 방향), qty,
        avgEntryPrice, avgExitPrice, closedPnl, leverage, updatedTime(ms)
        """
        kwargs = {"category": self.CATEGORY, "limit": limit}
        if symbol:
            kwargs["symbol"] = self._to_bybit_symbol(symbol)
        resp = self._session.get_closed_pnl(**kwargs)
        self._raise_if_error(resp, "실현손익 조회")
        return resp["result"]["list"]

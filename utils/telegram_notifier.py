# -*- coding: utf-8 -*-
"""
텔레그램 알림 모듈

거래 이벤트(진입/청산/사이클/오류)를 텔레그램으로 실시간 전송합니다.
환경변수 TELEGRAM_TOKEN, TELEGRAM_CHAT_ID 필요.
"""

import os
import time
import threading
import requests
from datetime import datetime
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)


def _escape_html(text: str) -> str:
    """HTML 특수문자를 이스케이프합니다 (parse_mode=HTML 메시지에서 < > & 가 태그로 오인되는 것을 방지)."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class TelegramNotifier:
    """텔레그램 Bot API 알림 전송기"""

    BASE_URL = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(
        self,
        token: Optional[str] = None,
        chat_id: Optional[str] = None,
    ):
        self.token   = token   or os.environ.get("TELEGRAM_TOKEN", "")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
        self._enabled = bool(self.token and self.chat_id)

        if self._enabled:
            logger.info("텔레그램 알림 활성화")
        else:
            logger.warning("텔레그램 환경변수 미설정 — 알림 비활성화")

    # ── 내부 전송 ────────────────────────────────────────────────────

    def _send(self, text: str) -> bool:
        if not self._enabled:
            return False
        url = self.BASE_URL.format(token=self.token)
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if not resp.ok:
                logger.warning(f"텔레그램 전송 실패: {resp.status_code} {resp.text[:200]}")
                return False
            return True
        except Exception as e:
            logger.warning(f"텔레그램 오류: {e}")
            return False

    def _send_async(self, text: str):
        """블로킹 방지용 백그라운드 전송"""
        threading.Thread(target=self._send, args=(text,), daemon=True).start()

    # ── 공개 API ────────────────────────────────────────────────────

    def send(self, text: str):
        self._send_async(text)

    def notify_start(self, mode: str, balance: float, symbols: list, interval_sec: int):
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        syms = "\n".join(f"  • {s}" for s in symbols)
        msg = (
            f"<b>자동매매 시작</b> [{mode}]\n"
            f"시작 시각: {now}\n"
            f"잔고: <b>{balance:.2f} USDT</b>\n"
            f"체크 주기: {interval_sec}초\n"
            f"종목:\n{syms}"
        )
        self._send(msg)  # 시작 알림은 동기로 전송

    def notify_stop(self, balance: float):
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        msg = (
            f"<b>자동매매 종료</b>\n"
            f"종료 시각: {now}\n"
            f"최종 잔고: <b>{balance:.2f} USDT</b>"
        )
        self._send(msg)

    def notify_cycle(
        self,
        balance: float,
        equity: float,
        n_positions: int,
        fg_value: float,
        fg_class: str,
        vix: float,
        modifier: float,
        positions: list,
    ):
        now = datetime.now().strftime("%H:%M")
        pos_lines = ""
        if positions:
            for p in positions:
                pnl_sign = "+" if p.unrealized_pnl >= 0 else ""
                emoji = "📈" if p.unrealized_pnl >= 0 else "📉"
                pos_lines += (
                    f"\n  {emoji} {p.symbol} {p.side} x{p.leverage}"
                    f" | {pnl_sign}{p.unrealized_pnl:.2f} USDT"
                )

        macro_line = f"F&G={fg_value:.0f}({fg_class})  VIX={vix:.1f}  배율={modifier:.2f}x"

        msg = (
            f"<b>[{now}] 사이클 완료</b>\n"
            f"잔고: <b>{balance:.2f} USDT</b>  총자산: {equity:.2f} USDT\n"
            f"포지션: {n_positions}개{pos_lines}\n"
            f"매크로: {macro_line}"
        )
        self._send_async(msg)

    def notify_signal_blocked(self, symbol: str, direction: str, reason: str):
        msg = (
            f"🚫 <b>신호 차단</b>\n"
            f"종목: {symbol}  방향: {direction}\n"
            f"사유: {_escape_html(reason)}"
        )
        self._send_async(msg)

    def notify_entry(
        self,
        symbol: str,
        direction: str,
        price: float,
        qty: float,
        leverage: int,
        margin: float,
        notional: float,
        sl: float,
        tp: float,
        order_id: str,
        basis: str = "",
        strength: int = 0,
        news_score: float = 0,
        macro: Optional[dict] = None,
    ):
        emoji = "🟢" if direction == "롱" else "🔴"
        sl_pct = abs(price - sl) / price * 100
        tp_pct = abs(price - tp) / price * 100
        msg = (
            f"{emoji} <b>{direction} 진입</b>  {symbol}\n"
            f"가격: ${price:,.4f}  수량: {qty}\n"
            f"레버리지: {leverage}x  마진: {margin:.2f} USDT\n"
            f"명목: {notional:.2f} USDT\n"
            f"SL: ${sl:,.4f} (-{sl_pct:.2f}%)\n"
            f"TP: ${tp:,.4f} (+{tp_pct:.2f}%)\n"
            f"주문ID: {order_id}\n"
        )
        if basis:
            msg += f"\n📊 <b>진입 근거</b> (신호강도 {strength}/3)\n{_escape_html(basis)}"
        if macro:
            msg += (
                f"\n🌍 매크로: F&G={macro.get('fg_value', 0):.0f}"
                f"({_escape_html(macro.get('fg_class', 'N/A'))})  "
                f"VIX={macro.get('vix', 0):.1f}  배율={macro.get('modifier', 1.0):.2f}x"
            )
        if news_score:
            msg += f"\n📰 뉴스 감성: {news_score:+.0f}"
        self._send_async(msg)

    def notify_entry_failed(self, symbol: str, direction: str, error: str):
        msg = (
            f"⚠️ <b>주문 실패</b>  {symbol} {direction}\n"
            f"오류: {_escape_html(error)}"
        )
        self._send_async(msg)

    def notify_error(self, context: str, error: str):
        msg = (
            f"❌ <b>오류 발생</b>\n"
            f"위치: {_escape_html(context)}\n"
            f"내용: {_escape_html(error)}"
        )
        self._send_async(msg)

    def notify_low_balance(self, balance: float, minimum: float):
        msg = (
            f"💰 <b>잔고 부족 경고</b>\n"
            f"현재: {balance:.2f} USDT\n"
            f"최소: {minimum:.2f} USDT\n"
            f"거래 중단됨"
        )
        self._send_async(msg)

    def ping(self) -> bool:
        """연결 테스트"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        return self._send(f"텔레그램 연결 테스트 성공 ({now})")

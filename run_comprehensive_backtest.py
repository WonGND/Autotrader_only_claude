"""
시총 상위 15종 선물 종합 백테스트 (2024-01-01 ~ 2026-06-06)

결과를 엑셀 파일로 출력:
  Sheet1: 종합 요약     (종목별 × 레버리지별 성과)
  Sheet2: 거래 상세     (모든 개별 거래 - 진입근거, 레버리지, 손익 등)
  Sheet3: 종목별 통계   (종목별 승률, 평균 수익 등 집계)
  Sheet4: 레버리지별 통계 (레버리지 모드별 비교)
"""

import sys
import os
import json
from datetime import datetime
from typing import List, Optional, Dict, Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategies.futures_strategy import FuturesStrategy
from trader.leverage_manager import LeverageManager
from backtester.futures_backtester import FuturesBacktester, FuturesBacktestResult
from utils.logger import get_logger

logger = get_logger(__name__)

# ─── 설정 ──────────────────────────────────────────────────────────

# 시총 상위 15종 (yfinance 호환 티커)
TOP15_SYMBOLS = [
    "BTC-USD",   # 비트코인
    "ETH-USD",   # 이더리움
    "BNB-USD",   # BNB
    "SOL-USD",   # 솔라나
    "XRP-USD",   # 리플
    "ADA-USD",   # 카르다노
    "DOGE-USD",  # 도지코인
    "AVAX-USD",  # 아발란체
    "DOT-USD",   # 폴카닷
    "MATIC-USD", # 폴리곤
    "LINK-USD",  # 체인링크
    "LTC-USD",   # 라이트코인
    "UNI-USD",   # 유니스왑
    "ATOM-USD",  # 코스모스
    "TRX-USD",   # 트론
]

SYMBOL_NAMES = {
    "BTC-USD": "비트코인",
    "ETH-USD": "이더리움",
    "BNB-USD": "BNB",
    "SOL-USD": "솔라나",
    "XRP-USD": "리플",
    "ADA-USD": "카르다노",
    "DOGE-USD": "도지코인",
    "AVAX-USD": "아발란체",
    "DOT-USD": "폴카닷",
    "MATIC-USD": "폴리곤",
    "LINK-USD": "체인링크",
    "LTC-USD": "라이트코인",
    "UNI-USD": "유니스왑",
    "ATOM-USD": "코스모스",
    "TRX-USD": "트론",
}

LEVERAGE_MODES = [
    ("1x",  1),
    ("3x",  3),
    ("5x",  5),
    ("10x", 10),
    ("동적", None),
]

START_DATE = "2024-01-01"
END_DATE   = "2026-06-06"
INITIAL_CAPITAL = 10_000_000

EXIT_REASON_KO = {
    "stop_loss":      "손절",
    "take_profit":    "익절",
    "liquidation":    "청산(강제)",
    "end_of_backtest": "기간종료",
}

SIGNAL_STRENGTH_KO = {
    1: "약함 (1/3 지표)",
    2: "보통 (2/3 지표)",
    3: "강함 (3/3 지표)",
}


# ─── 백테스트 실행 ──────────────────────────────────────────────────

def run_one(symbol: str, label: str, fixed_leverage: Optional[int]) -> Optional[FuturesBacktestResult]:
    try:
        strategy = FuturesStrategy()
        lm = LeverageManager()
        bt = FuturesBacktester(
            strategy=strategy,
            leverage_manager=lm,
            initial_capital=INITIAL_CAPITAL,
            maker_fee=0.0002,
            taker_fee=0.0005,
            funding_rate_8h=0.0001,
            fixed_leverage=fixed_leverage,
            risk_per_trade_pct=0.02,
        )
        result = bt.run(symbol, START_DATE, END_DATE)
        result.leverage_mode = label
        return result
    except Exception as e:
        logger.error(f"백테스트 실패 {symbol} {label}: {e}", exc_info=True)
        return None


def run_all() -> List[FuturesBacktestResult]:
    results = []
    total = len(TOP15_SYMBOLS) * len(LEVERAGE_MODES)
    done = 0
    for symbol in TOP15_SYMBOLS:
        for label, leverage in LEVERAGE_MODES:
            done += 1
            name = SYMBOL_NAMES.get(symbol, symbol)
            print(f"  [{done:3d}/{total}] {symbol}({name}) {label:5s} ...", end="", flush=True)
            r = run_one(symbol, label, leverage)
            if r:
                results.append(r)
                win_pct = r.win_rate * 100
                print(f" 수익={r.total_return_pct:+.1f}%  승률={win_pct:.0f}%  청산={r.liquidations}")
            else:
                print(" 실패")
    return results


# ─── 엑셀 출력 ─────────────────────────────────────────────────────

def build_excel(results: List[FuturesBacktestResult], filepath: str):
    from openpyxl import Workbook
    from openpyxl.styles import (
        PatternFill, Font, Alignment, Border, Side, numbers
    )
    from openpyxl.utils import get_column_letter

    wb = Workbook()

    # ── 스타일 정의 ────────────────────────────────────────────────
    HEADER_FILL   = PatternFill("solid", fgColor="1F3864")
    HEADER_FONT   = Font(color="FFFFFF", bold=True, size=10)
    TITLE_FILL    = PatternFill("solid", fgColor="2E75B6")
    TITLE_FONT    = Font(color="FFFFFF", bold=True, size=12)
    POS_FILL      = PatternFill("solid", fgColor="E2EFDA")
    NEG_FILL      = PatternFill("solid", fgColor="FDECEA")
    ALT_FILL      = PatternFill("solid", fgColor="F5F5F5")
    CENTER        = Alignment(horizontal="center", vertical="center")
    LEFT          = Alignment(horizontal="left",   vertical="center", wrap_text=True)
    RIGHT         = Alignment(horizontal="right",  vertical="center")
    thin          = Side(style="thin", color="CCCCCC")
    BORDER        = Border(left=thin, right=thin, top=thin, bottom=thin)

    NUM_FMT_PCT   = '0.00"%"'
    NUM_FMT_USD   = '#,##0.00'
    NUM_FMT_INT   = '#,##0'

    def hdr(ws, row, col, value, width=None):
        c = ws.cell(row=row, column=col, value=value)
        c.fill   = HEADER_FILL
        c.font   = HEADER_FONT
        c.alignment = CENTER
        c.border = BORDER
        if width:
            ws.column_dimensions[get_column_letter(col)].width = width
        return c

    def cell(ws, row, col, value, fmt=None, fill=None, align=None):
        c = ws.cell(row=row, column=col, value=value)
        c.border = BORDER
        c.alignment = align or CENTER
        if fmt:
            c.number_format = fmt
        if fill:
            c.fill = fill
        return c

    def pnl_fill(val):
        if val is None: return None
        return POS_FILL if val >= 0 else NEG_FILL

    # ══════════════════════════════════════════════════════════════
    # Sheet 1: 종합 요약
    # ══════════════════════════════════════════════════════════════
    ws1 = wb.active
    ws1.title = "종합 요약"
    ws1.freeze_panes = "A3"

    # 타이틀
    ws1.merge_cells("A1:N1")
    t = ws1["A1"]
    t.value = f"시총 상위 15종 선물 백테스트 종합 요약  |  기간: {START_DATE} ~ {END_DATE}  |  초기자본: ${INITIAL_CAPITAL:,.0f}"
    t.fill      = TITLE_FILL
    t.font      = TITLE_FONT
    t.alignment = CENTER

    headers1 = [
        ("종목코드",    10), ("종목명",    10), ("레버리지",  8),
        ("수익률(%)",   10), ("MDD(%)",    9),  ("샤프비율",  9),
        ("승률(%)",     8),  ("총거래수",  8),  ("롱거래",    7),
        ("숏거래",      7),  ("청산횟수",  8),  ("평균레버",  8),
        ("초기자본($)", 13), ("최종자본($)",13),
    ]
    for ci, (h, w) in enumerate(headers1, 1):
        hdr(ws1, 2, ci, h, w)

    row = 3
    prev_sym = None
    for ri, r in enumerate(results):
        bg = ALT_FILL if (ri // len(LEVERAGE_MODES)) % 2 == 0 else None
        ret_fill = pnl_fill(r.total_return_pct)
        cell(ws1, row, 1,  r.symbol,                       fill=bg)
        cell(ws1, row, 2,  SYMBOL_NAMES.get(r.symbol,""),  fill=bg)
        cell(ws1, row, 3,  r.leverage_mode,                fill=bg)
        cell(ws1, row, 4,  r.total_return_pct,  NUM_FMT_PCT, ret_fill)
        cell(ws1, row, 5,  r.max_drawdown_pct,  NUM_FMT_PCT, NEG_FILL if r.max_drawdown_pct < -10 else bg)
        cell(ws1, row, 6,  round(r.sharpe_ratio, 3),        fill=bg)
        cell(ws1, row, 7,  round(r.win_rate*100, 1), NUM_FMT_PCT,
             POS_FILL if r.win_rate >= 0.5 else (NEG_FILL if r.win_rate < 0.4 else bg))
        cell(ws1, row, 8,  r.total_trades,      NUM_FMT_INT, fill=bg)
        cell(ws1, row, 9,  r.long_trades,       NUM_FMT_INT, fill=bg)
        cell(ws1, row, 10, r.short_trades,      NUM_FMT_INT, fill=bg)
        cell(ws1, row, 11, r.liquidations,      NUM_FMT_INT,
             NEG_FILL if r.liquidations > 0 else bg)
        cell(ws1, row, 12, round(r.avg_leverage, 1),        fill=bg)
        cell(ws1, row, 13, r.initial_capital,   NUM_FMT_USD, fill=bg)
        cell(ws1, row, 14, round(r.final_capital, 2), NUM_FMT_USD, ret_fill)
        row += 1

    ws1.row_dimensions[1].height = 22
    ws1.row_dimensions[2].height = 20

    # ══════════════════════════════════════════════════════════════
    # Sheet 2: 거래 상세
    # ══════════════════════════════════════════════════════════════
    ws2 = wb.create_sheet("거래 상세")
    ws2.freeze_panes = "A3"

    ws2.merge_cells("A1:P1")
    t2 = ws2["A1"]
    t2.value = f"전체 거래 상세 로그  |  기간: {START_DATE} ~ {END_DATE}"
    t2.fill = TITLE_FILL; t2.font = TITLE_FONT; t2.alignment = CENTER

    headers2 = [
        ("종목코드",   10), ("종목명",   10), ("레버리지모드", 9),
        ("방향",        6), ("진입시간", 18), ("청산시간",    18),
        ("진입가($)",  12), ("청산가($)",12), ("레버리지(배)", 10),
        ("증거금($)",  12), ("포지션($)", 12),("손익($)",    12),
        ("손익률(%)",  10), ("청산사유", 10), ("보유(시간)",  9),
        ("진입근거",   40),
    ]
    for ci, (h, w) in enumerate(headers2, 1):
        hdr(ws2, 2, ci, h, w)

    row = 3
    alt_idx = 0
    prev_sym = None
    for r in results:
        for t in r.trades_log:
            alt_idx += 1
            bg = ALT_FILL if alt_idx % 2 == 0 else None
            pnl = t.get("pnl", 0) or 0
            pf  = pnl_fill(pnl)
            side_ko = "롱↑" if t.get("side") == "long" else "숏↓"

            cell(ws2, row, 1,  r.symbol,                      fill=bg)
            cell(ws2, row, 2,  SYMBOL_NAMES.get(r.symbol,""), fill=bg)
            cell(ws2, row, 3,  r.leverage_mode,               fill=bg)
            cell(ws2, row, 4,  side_ko,                       fill=bg)
            cell(ws2, row, 5,  t.get("entry_time",""),        fill=bg, align=CENTER)
            cell(ws2, row, 6,  t.get("exit_time",""),         fill=bg, align=CENTER)
            cell(ws2, row, 7,  t.get("entry_price",0),  NUM_FMT_USD, fill=bg)
            cell(ws2, row, 8,  t.get("exit_price",0),   NUM_FMT_USD, fill=bg)
            cell(ws2, row, 9,  t.get("leverage",0),           fill=bg)
            cell(ws2, row, 10, t.get("margin",0),       NUM_FMT_USD, fill=bg)
            cell(ws2, row, 11, t.get("quantity",0),     NUM_FMT_USD, fill=bg)
            cell(ws2, row, 12, round(pnl, 2),           NUM_FMT_USD, pf)
            cell(ws2, row, 13, round(t.get("pnl_pct",0),2), NUM_FMT_PCT, pf)
            reason_ko = EXIT_REASON_KO.get(t.get("exit_reason",""), t.get("exit_reason",""))
            cell(ws2, row, 14, reason_ko,                     fill=pf if t.get("exit_reason")=="liquidation" else bg)
            cell(ws2, row, 15, round(t.get("hold_hours",0),1), fill=bg)
            basis = t.get("entry_basis","") or f"신호강도 {SIGNAL_STRENGTH_KO.get(t.get('signal_strength',0),'')}"
            cell(ws2, row, 16, basis, fill=bg, align=LEFT)
            row += 1

    ws2.row_dimensions[1].height = 22
    ws2.row_dimensions[2].height = 20

    # ══════════════════════════════════════════════════════════════
    # Sheet 3: 종목별 통계
    # ══════════════════════════════════════════════════════════════
    ws3 = wb.create_sheet("종목별 통계")
    ws3.freeze_panes = "A3"

    ws3.merge_cells("A1:K1")
    t3 = ws3["A1"]
    t3.value = "종목별 종합 통계 (전 레버리지 모드 통합)"
    t3.fill = TITLE_FILL; t3.font = TITLE_FONT; t3.alignment = CENTER

    headers3 = [
        ("종목코드", 10), ("종목명", 10), ("평균수익률(%)", 13),
        ("최고수익률(%)", 13), ("최저수익률(%)", 13),
        ("평균승률(%)", 11), ("총거래수", 9), ("승리거래", 9),
        ("패배거래", 9), ("총청산수", 9), ("평균MDD(%)", 11),
    ]
    for ci, (h, w) in enumerate(headers3, 1):
        hdr(ws3, 2, ci, h, w)

    # 종목별 집계
    sym_data: Dict[str, List[FuturesBacktestResult]] = {}
    for r in results:
        sym_data.setdefault(r.symbol, []).append(r)

    row = 3
    for si, symbol in enumerate(TOP15_SYMBOLS):
        rs = sym_data.get(symbol, [])
        if not rs:
            continue
        bg = ALT_FILL if si % 2 == 0 else None
        all_trades = [t for r in rs for t in r.trades_log]
        wins = sum(1 for t in all_trades if (t.get("pnl") or 0) > 0)
        total_t = len(all_trades)
        avg_ret = sum(r.total_return_pct for r in rs) / len(rs)
        avg_wr  = sum(r.win_rate for r in rs) / len(rs)
        avg_mdd = sum(r.max_drawdown_pct for r in rs) / len(rs)

        cell(ws3, row, 1,  symbol,                         fill=bg)
        cell(ws3, row, 2,  SYMBOL_NAMES.get(symbol,""),    fill=bg)
        cell(ws3, row, 3,  round(avg_ret,2),    NUM_FMT_PCT, pnl_fill(avg_ret))
        cell(ws3, row, 4,  round(max(r.total_return_pct for r in rs),2), NUM_FMT_PCT, POS_FILL)
        cell(ws3, row, 5,  round(min(r.total_return_pct for r in rs),2), NUM_FMT_PCT, NEG_FILL)
        cell(ws3, row, 6,  round(avg_wr*100,1), NUM_FMT_PCT,
             POS_FILL if avg_wr>=0.5 else (NEG_FILL if avg_wr<0.4 else bg))
        cell(ws3, row, 7,  total_t,             NUM_FMT_INT, fill=bg)
        cell(ws3, row, 8,  wins,                NUM_FMT_INT, POS_FILL)
        cell(ws3, row, 9,  total_t - wins,      NUM_FMT_INT, NEG_FILL if total_t-wins > wins else bg)
        cell(ws3, row, 10, sum(r.liquidations for r in rs), NUM_FMT_INT,
             NEG_FILL if sum(r.liquidations for r in rs)>0 else bg)
        cell(ws3, row, 11, round(avg_mdd,2),    NUM_FMT_PCT, NEG_FILL)
        row += 1

    ws3.row_dimensions[1].height = 22
    ws3.row_dimensions[2].height = 20

    # ══════════════════════════════════════════════════════════════
    # Sheet 4: 레버리지별 통계
    # ══════════════════════════════════════════════════════════════
    ws4 = wb.create_sheet("레버리지별 통계")
    ws4.freeze_panes = "A3"

    ws4.merge_cells("A1:J1")
    t4 = ws4["A1"]
    t4.value = "레버리지 모드별 종합 통계 (전 종목 통합)"
    t4.fill = TITLE_FILL; t4.font = TITLE_FONT; t4.alignment = CENTER

    headers4 = [
        ("레버리지모드", 12), ("평균수익률(%)", 13), ("평균MDD(%)", 11),
        ("평균샤프",     10), ("평균승률(%)",   11), ("총거래수",    9),
        ("승리거래",      9), ("패배거래",       9), ("총청산수",    9),
        ("추천여부",     10),
    ]
    for ci, (h, w) in enumerate(headers4, 1):
        hdr(ws4, 2, ci, h, w)

    lev_data: Dict[str, List[FuturesBacktestResult]] = {}
    for r in results:
        lev_data.setdefault(r.leverage_mode, []).append(r)

    row = 3
    best_sharpe = -999
    best_lev = ""
    for label, _ in LEVERAGE_MODES:
        rs = lev_data.get(label, [])
        if not rs: continue
        avg_ret    = sum(r.total_return_pct for r in rs) / len(rs)
        avg_mdd    = sum(r.max_drawdown_pct for r in rs) / len(rs)
        avg_sharpe = sum(r.sharpe_ratio for r in rs) / len(rs)
        avg_wr     = sum(r.win_rate for r in rs) / len(rs)
        all_t      = [t for r in rs for t in r.trades_log]
        wins       = sum(1 for t in all_t if (t.get("pnl") or 0) > 0)
        total_liq  = sum(r.liquidations for r in rs)
        if avg_sharpe > best_sharpe:
            best_sharpe = avg_sharpe
            best_lev = label

    for li, (label, _) in enumerate(LEVERAGE_MODES):
        rs = lev_data.get(label, [])
        if not rs: continue
        bg = ALT_FILL if li % 2 == 0 else None
        avg_ret    = sum(r.total_return_pct for r in rs) / len(rs)
        avg_mdd    = sum(r.max_drawdown_pct for r in rs) / len(rs)
        avg_sharpe = sum(r.sharpe_ratio for r in rs) / len(rs)
        avg_wr     = sum(r.win_rate for r in rs) / len(rs)
        all_t      = [t for r in rs for t in r.trades_log]
        wins       = sum(1 for t in all_t if (t.get("pnl") or 0) > 0)
        total_t    = len(all_t)
        total_liq  = sum(r.liquidations for r in rs)
        is_best    = label == best_lev

        cell(ws4, row, 1,  label,               fill=POS_FILL if is_best else bg)
        cell(ws4, row, 2,  round(avg_ret,2),    NUM_FMT_PCT, pnl_fill(avg_ret))
        cell(ws4, row, 3,  round(avg_mdd,2),    NUM_FMT_PCT, NEG_FILL)
        cell(ws4, row, 4,  round(avg_sharpe,3), fill=bg)
        cell(ws4, row, 5,  round(avg_wr*100,1), NUM_FMT_PCT,
             POS_FILL if avg_wr>=0.5 else (NEG_FILL if avg_wr<0.4 else bg))
        cell(ws4, row, 6,  total_t,             NUM_FMT_INT, fill=bg)
        cell(ws4, row, 7,  wins,                NUM_FMT_INT, POS_FILL)
        cell(ws4, row, 8,  total_t - wins,      NUM_FMT_INT, NEG_FILL)
        cell(ws4, row, 9,  total_liq,           NUM_FMT_INT, NEG_FILL if total_liq>0 else bg)
        cell(ws4, row, 10, "★ 최적" if is_best else "",
             fill=POS_FILL if is_best else bg)
        row += 1

    for ws in [ws1, ws2, ws3, ws4]:
        ws.sheet_view.showGridLines = True

    wb.save(filepath)
    print(f"\n  엑셀 저장 완료: {filepath}")


# ─── 메인 ──────────────────────────────────────────────────────────

def main():
    import io, sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    print()
    print("=" * 66)
    print("  [시총 상위 15종] 선물 종합 백테스트  2024-01-01 ~ 2026-06-06")
    print("=" * 66)
    print(f"  종목: {', '.join(TOP15_SYMBOLS)}")
    print(f"  레버리지 모드: {', '.join(l for l,_ in LEVERAGE_MODES)}")
    print(f"  총 백테스트: {len(TOP15_SYMBOLS)} × {len(LEVERAGE_MODES)} = {len(TOP15_SYMBOLS)*len(LEVERAGE_MODES)}회")
    print()

    results = run_all()

    if not results:
        print("백테스트 결과가 없습니다.")
        return

    # 요약 출력
    print()
    print("═" * 85)
    print(f"{'종목':<12} {'레버':<7} {'수익률':>8} {'MDD':>8} {'샤프':>6} {'승률':>6} {'거래':>5} {'청산':>4}")
    print("─" * 85)
    prev = None
    for r in results:
        if prev and r.symbol != prev:
            print("─" * 85)
        prev = r.symbol
        mark = "★" if r.leverage_mode == "동적" else " "
        print(f"{r.symbol:<12} {r.leverage_mode:<7} "
              f"{r.total_return_pct:>+7.2f}% {r.max_drawdown_pct:>7.2f}% "
              f"{r.sharpe_ratio:>6.2f} {r.win_rate*100:>5.1f}% "
              f"{r.total_trades:>5} {r.liquidations:>4}{mark}")
    print("═" * 85)

    # 엑셀 저장
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    excel_path = os.path.join(script_dir, f"backtest_top15_{ts}.xlsx")
    build_excel(results, excel_path)
    return excel_path


if __name__ == "__main__":
    main()

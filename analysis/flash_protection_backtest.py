# -*- coding: utf-8 -*-
"""
급등/급락 대응 전략 파라미터 최적화 백테스트

1분봉 기준으로 급변동 감지 파라미터를 최적화합니다.
  - 1분봉 (7일치): 정확한 1분 단위 분석
  - 5분봉 (60일치): 보완 분석 (더 긴 기간)

테스트 변수:
  be_trigger_pct  : 포지션 수익이 이 %에 도달하면 SL → 손익분기(BE)로 이동
  flash_pct       : 1분봉 내 이 % 이상 역방향 변동 = 급등/급락
  warn_pct        : 이 % 이상이면 경보만 (청산 X) — 2단계 구조
  auto_close      : flash_pct 초과 시 즉시 청산 여부
"""

import os, sys, json, math, warnings, itertools
from datetime import datetime, timedelta
from typing import List, Tuple, Dict, Optional
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from data.fetcher import DataFetcher          # SSL 패치 자동 적용
from strategies.futures_strategy import FuturesStrategy
import yfinance as yf

# ── 설정 ─────────────────────────────────────────────────────────────

SYMBOLS = [
    "BTC-USD", "ETH-USD", "BNB-USD", "ATOM-USD",
    "DOGE-USD", "DOT-USD", "TRX-USD", "SOL-USD",
]

DAILY_DAYS   = 180     # 일봉 신호 생성용 기간
SL_ATR_MULT  = 1.5
TP_ATR_MULT  = 3.0
LEVERAGE     = 5

# 1분봉 기준 급변동 임계값 (코인 특성상 0.2~1.5%)
BE_TRIGGERS  = [0.5, 1.0, 1.5, 2.0, 3.0]        # % 수익 시 BE 이동
FLASH_PCTS   = [0.3, 0.5, 0.8, 1.0, 1.5, 2.0]   # % — 이 이상이면 즉시 청산
WARN_PCTS    = [0.2, 0.3, 0.5]                    # % — 이 이상이면 경보만

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "flash_protection_results")
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ── 데이터 다운로드 ──────────────────────────────────────────────────

def download_daily(symbol: str, days: int) -> pd.DataFrame:
    end = datetime.now()
    start = end - timedelta(days=days)
    ticker = yf.Ticker(symbol)
    df = ticker.history(start=start.strftime("%Y-%m-%d"),
                        end=end.strftime("%Y-%m-%d"), auto_adjust=True)
    if df.empty:
        return df
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df.dropna()


def download_intraday(symbol: str, interval: str, days: int) -> pd.DataFrame:
    """1m (7일), 5m (60일) 데이터 다운로드"""
    end = datetime.now()
    start = end - timedelta(days=min(days, 6 if interval == "1m" else 59))
    ticker = yf.Ticker(symbol)
    df = ticker.history(start=start.strftime("%Y-%m-%d"),
                        end=end.strftime("%Y-%m-%d"),
                        interval=interval, auto_adjust=True)
    if df.empty:
        return df
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df.dropna()


# ── 신호 생성 ────────────────────────────────────────────────────────

def get_daily_signals(symbol: str) -> Optional[pd.DataFrame]:
    strategy = FuturesStrategy()
    data = download_daily(symbol, DAILY_DAYS)
    if data.empty or len(data) < strategy.get_required_history():
        return None
    return strategy.generate_signals(data)


# ── 데이터 클래스 ────────────────────────────────────────────────────

@dataclass
class TradeResult:
    symbol: str
    direction: str
    entry_date: str
    entry_price: float
    exit_price: float = 0.0
    exit_reason: str = ""  # TP / SL / BE / FLASH_CLOSE / WARN / END
    pnl_pct: float = 0.0
    be_moved: bool = False
    flash_hit: bool = False
    warn_hit: bool = False


# ── 단일 트레이드 시뮬레이션 ─────────────────────────────────────────

def simulate_trade(
    direction: str,
    entry_date: str,
    entry_price: float,
    sl: float,
    tp: float,
    intraday_df: pd.DataFrame,
    be_trigger_pct: float,
    flash_pct: float,
    warn_pct: float,
    auto_close: bool,
) -> TradeResult:

    result = TradeResult(
        symbol="", direction=direction,
        entry_date=entry_date, entry_price=entry_price,
    )

    try:
        entry_dt = pd.Timestamp(entry_date)
    except Exception:
        entry_dt = intraday_df.index[0]

    bars = intraday_df[intraday_df.index >= entry_dt]
    if bars.empty:
        result.exit_price  = entry_price
        result.exit_reason = "END"
        return result

    current_sl = sl
    be_moved = False

    for ts, bar in bars.iterrows():
        o = float(bar["Open"])
        h = float(bar["High"])
        l = float(bar["Low"])
        c = float(bar["Close"])

        if direction == "long":
            # TP
            if h >= tp:
                result.exit_price = tp
                result.exit_reason = "TP"
                result.pnl_pct = (tp - entry_price) / entry_price * 100
                result.be_moved = be_moved
                return result
            # SL / BE
            if l <= current_sl:
                result.exit_price = current_sl
                result.exit_reason = "BE" if be_moved else "SL"
                result.pnl_pct = (current_sl - entry_price) / entry_price * 100
                result.be_moved = be_moved
                return result
            # BE 이동
            unrealized = (c - entry_price) / entry_price * 100
            if not be_moved and unrealized >= be_trigger_pct:
                current_sl = entry_price
                be_moved = True
            # 급변동: 캔들 내 하락 폭 (open → low)
            drop_pct = (o - l) / o * 100 if o > 0 else 0
            if drop_pct >= flash_pct:
                result.flash_hit = True
                if auto_close:
                    result.exit_price  = l   # 최악 체결 가정
                    result.exit_reason = "FLASH_CLOSE"
                    result.pnl_pct = (l - entry_price) / entry_price * 100
                    result.be_moved = be_moved
                    return result
            elif drop_pct >= warn_pct:
                result.warn_hit = True  # 경보만, 계속 진행

        else:  # short
            if l <= tp:
                result.exit_price = tp
                result.exit_reason = "TP"
                result.pnl_pct = (entry_price - tp) / entry_price * 100
                result.be_moved = be_moved
                return result
            if h >= current_sl:
                result.exit_price = current_sl
                result.exit_reason = "BE" if be_moved else "SL"
                result.pnl_pct = (entry_price - current_sl) / entry_price * 100
                result.be_moved = be_moved
                return result
            unrealized = (entry_price - c) / entry_price * 100
            if not be_moved and unrealized >= be_trigger_pct:
                current_sl = entry_price
                be_moved = True
            rise_pct = (h - o) / o * 100 if o > 0 else 0
            if rise_pct >= flash_pct:
                result.flash_hit = True
                if auto_close:
                    result.exit_price  = h
                    result.exit_reason = "FLASH_CLOSE"
                    result.pnl_pct = (entry_price - h) / entry_price * 100
                    result.be_moved = be_moved
                    return result
            elif rise_pct >= warn_pct:
                result.warn_hit = True

    last = float(bars.iloc[-1]["Close"])
    result.exit_price  = last
    result.exit_reason = "END"
    result.pnl_pct = (
        (last - entry_price) / entry_price * 100 if direction == "long"
        else (entry_price - last) / entry_price * 100
    )
    result.be_moved = be_moved
    return result


# ── 심볼 백테스트 ────────────────────────────────────────────────────

def run_symbol(symbol: str, interval: str, intraday_days: int) -> pd.DataFrame:
    signals_df = get_daily_signals(symbol)
    if signals_df is None:
        return pd.DataFrame()

    intraday = download_intraday(symbol, interval, intraday_days)
    if intraday.empty:
        return pd.DataFrame()

    # 시봉 기간 내 신호만
    cutoff = intraday.index[0]
    sigs = signals_df[
        (signals_df["signal"] != 0) &
        (signals_df.index >= cutoff)
    ]
    if sigs.empty:
        return pd.DataFrame()

    print(f"    {symbol}: {interval} {len(intraday)}봉  |  신호 {len(sigs)}건")

    # 파라미터 조합 (warn_pct는 flash_pct보다 작아야 의미있음)
    combos = [
        (be, fl, wn, ac)
        for be, fl, wn, ac in itertools.product(BE_TRIGGERS, FLASH_PCTS, WARN_PCTS, [False, True])
        if wn < fl
    ]

    rows = []
    for be_t, fl_p, wn_p, auto_c in combos:
        wins = losses = be_saves = flash_closes = warn_alerts = 0
        total_pnl = 0.0
        trade_count = 0

        for date_idx, row in sigs.iterrows():
            sig       = int(row["signal"])
            direction = "long" if sig == 1 else "short"
            ep        = float(row["Close"])
            atr       = float(row.get("atr", ep * 0.02))
            sl = ep - atr * SL_ATR_MULT if direction == "long" else ep + atr * SL_ATR_MULT
            tp = ep + atr * TP_ATR_MULT if direction == "long" else ep - atr * TP_ATR_MULT

            tr = simulate_trade(
                direction, str(date_idx), ep, sl, tp,
                intraday, be_t, fl_p, wn_p, auto_c,
            )
            tr.symbol = symbol

            trade_count += 1
            levered = tr.pnl_pct * LEVERAGE
            total_pnl += levered

            if tr.pnl_pct > 0:
                wins += 1
            elif tr.pnl_pct < 0:
                losses += 1
            if tr.be_moved and tr.exit_reason == "BE":
                be_saves += 1
            if tr.exit_reason == "FLASH_CLOSE":
                flash_closes += 1
            if tr.warn_hit:
                warn_alerts += 1

        if trade_count == 0:
            continue

        win_rate = wins / trade_count * 100
        avg_pnl  = total_pnl / trade_count
        rows.append({
            "symbol": symbol,
            "interval": interval,
            "be_trigger_pct": be_t,
            "flash_pct": fl_p,
            "warn_pct": wn_p,
            "auto_close": auto_c,
            "trades": trade_count,
            "win_rate": round(win_rate, 1),
            "avg_pnl_pct": round(avg_pnl, 2),
            "total_pnl_pct": round(total_pnl, 2),
            "be_saves": be_saves,
            "flash_closes": flash_closes,
            "warn_alerts": warn_alerts,
        })

    return pd.DataFrame(rows)


# ── 결과 출력 ────────────────────────────────────────────────────────

def print_top(df: pd.DataFrame, label: str, n: int = 10):
    if df.empty:
        print(f"  [{label}] 결과 없음")
        return

    grp = df.groupby(["be_trigger_pct", "flash_pct", "warn_pct", "auto_close"]).agg(
        trades      = ("trades", "sum"),
        win_rate    = ("win_rate", "mean"),
        avg_pnl_pct = ("avg_pnl_pct", "mean"),
        total_pnl   = ("total_pnl_pct", "sum"),
        be_saves    = ("be_saves", "sum"),
        flash_closes= ("flash_closes", "sum"),
        warn_alerts = ("warn_alerts", "sum"),
    ).reset_index()

    # PF 계산 (avg_pnl > 0 인 경우만 양수)
    grp["score"] = (
        grp["win_rate"]    * 0.30 +
        grp["avg_pnl_pct"] * 0.50 +
        (grp["avg_pnl_pct"] > 0).astype(int) * 5   # 수익이면 보너스
    )
    grp = grp.sort_values("score", ascending=False).reset_index(drop=True)

    print(f"\n{'='*72}")
    print(f"  [{label}] 상위 {n}개 파라미터 조합")
    print(f"{'='*72}")
    print(f"  {'순위':>3}  {'BE%':>4}  {'급변%':>5}  {'경보%':>5}  {'자동':>4}  "
          f"{'승률':>6}  {'평균PnL':>8}  {'BE절감':>6}  {'자동청산':>6}  {'경보':>5}")
    print("-" * 72)

    for i, row in grp.head(n).iterrows():
        ac = "O" if row["auto_close"] else "X"
        marker = " ★" if row["avg_pnl_pct"] > 0 else "  "
        print(
            f"  {i+1:>3}  {row['be_trigger_pct']:>4.1f}  "
            f"{row['flash_pct']:>5.1f}  "
            f"{row['warn_pct']:>5.1f}  {ac:>4}  "
            f"{row['win_rate']:>5.1f}%  "
            f"{row['avg_pnl_pct']:>+7.2f}%  "
            f"{int(row['be_saves']):>6}건  "
            f"{int(row['flash_closes']):>6}건  "
            f"{int(row['warn_alerts']):>5}건{marker}"
        )

    best_pos = grp[grp["avg_pnl_pct"] > 0]
    best = best_pos.iloc[0] if not best_pos.empty else grp.iloc[0]

    print(f"\n  [추천 — {label}]")
    print(f"  BE 이동    : 수익 +{best['be_trigger_pct']:.1f}% 시 SL → 진입가")
    print(f"  경보 기준  : 1분봉 {best['warn_pct']:.1f}% 이상 역방향 → 텔레그램 경보")
    print(f"  청산 기준  : 1분봉 {best['flash_pct']:.1f}% 이상 역방향 → {'즉시 청산' if best['auto_close'] else '경보만'}")
    print(f"  예상 승률  : {best['win_rate']:.1f}%")
    print(f"  평균 PnL   : {best['avg_pnl_pct']:+.2f}% (5x 레버리지)")
    print(f"  BE 절감    : {int(best['be_saves'])}건")
    print(f"  자동 청산  : {int(best['flash_closes'])}건")

    return grp, best


# ── 메인 ─────────────────────────────────────────────────────────────

def main():
    print("=" * 72)
    print("  1분봉 기준 급등/급락 보호 파라미터 백테스트")
    print(f"  심볼: {len(SYMBOLS)}개")
    print(f"  BE 기준: {BE_TRIGGERS}%")
    print(f"  급변동(청산): {FLASH_PCTS}%")
    print(f"  급변동(경보): {WARN_PCTS}%")
    print("=" * 72)

    frames_1m = []
    frames_5m = []

    for symbol in SYMBOLS:
        print(f"\n  [{symbol}]")
        df1 = run_symbol(symbol, "1m", 6)
        if not df1.empty:
            frames_1m.append(df1)
        df5 = run_symbol(symbol, "5m", 59)
        if not df5.empty:
            frames_5m.append(df5)

    # ── 1분봉 결과 ───────────────────────────────────────────────────
    rec_1m = rec_5m = None
    if frames_1m:
        all_1m = pd.concat(frames_1m, ignore_index=True)
        result = print_top(all_1m, "1분봉 (7일)", n=10)
        if result:
            grp_1m, rec_1m = result
            all_1m.to_csv(
                os.path.join(OUTPUT_DIR, "results_1m.csv"),
                index=False, encoding="utf-8-sig"
            )
    else:
        print("\n  1분봉 데이터 부족 (7일 내 신호 없음)")

    # ── 5분봉 결과 ───────────────────────────────────────────────────
    if frames_5m:
        all_5m = pd.concat(frames_5m, ignore_index=True)
        result = print_top(all_5m, "5분봉 (60일 — 보완)", n=10)
        if result:
            grp_5m, rec_5m = result
            all_5m.to_csv(
                os.path.join(OUTPUT_DIR, "results_5m.csv"),
                index=False, encoding="utf-8-sig"
            )

    # ── 최종 권장 파라미터 ───────────────────────────────────────────
    print("\n" + "=" * 72)
    print("  [최종 권장 파라미터]")
    print("=" * 72)

    # 1분봉 결과 우선, 없으면 5분봉 사용
    rec = rec_1m if rec_1m is not None else rec_5m
    if rec is not None:
        recommendation = {
            "be_trigger_pct":  float(rec["be_trigger_pct"]),
            "warn_pct":        float(rec["warn_pct"]),
            "flash_pct":       float(rec["flash_pct"]),
            "auto_close":      bool(rec["auto_close"]),
            "monitor_interval_sec": 60,
            "expected_win_rate":   float(rec["win_rate"]),
            "expected_avg_pnl":    float(rec["avg_pnl_pct"]),
        }
        json_path = os.path.join(OUTPUT_DIR, "recommendation.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(recommendation, f, indent=2, ensure_ascii=False)

        print(f"  모니터링 주기  : 1분마다 체크")
        print(f"  BE 이동        : 수익 +{rec['be_trigger_pct']:.1f}% 도달 시 SL → 진입가")
        print(f"  경보 기준      : 1분봉 {rec['warn_pct']:.1f}% 이상 역방향 → 텔레그램 경보")
        print(f"  청산 기준      : 1분봉 {rec['flash_pct']:.1f}% 이상 역방향 → 즉시 청산 + 경보")
        print(f"  자동청산       : {'YES' if rec['auto_close'] else 'NO'}")
        print(f"  저장           : {json_path}")
    print("=" * 72)


if __name__ == "__main__":
    main()

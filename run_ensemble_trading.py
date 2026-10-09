# -*- coding: utf-8 -*-
"""
규칙 D (앙상블 추세추종) 리밸런싱 봇 + 기준선 B2 가상 운용 + 피드백 로그

봉 마감마다 실행된다 (config timeframe: 4h면 4시간마다 = 한국 01·05·09·13·17·21시 5분).
실행 사이에도 급락 대비 비상 손절은 거래소에 걸려 있으므로 상시 감시 프로세스가 필요 없다.

  1) Bybit에서 10종목 완성봉(4h) + 일봉 수집
  2) 1배 목표 비중 계산 (backtester/rule_sim.ensemble_weights — 백테스트와 '같은 함수')
  3) 자동 노출 배수 k (변동성 목표 + 낙폭 브레이크, 최대 10배) × 총노출 상한
  4) 현재 포지션과 비교 → 20% 밴드를 넘는 종목만 시장가 조정 (최소 주문 미달은 건너뛰고 기록)
  5) 보유 포지션마다 비상 손절(현재가 ± 3×일봉ATR) 갱신
  6) 거래소 실현손익 내역 누적
  7) 모델 D(이상적 체결) · 기준선 B2(터틀 55/20+롱우위, 일봉)를 같은 시작일부터 가상 계산
  8) bot_logs/ 에 실행별 JSON · 자산곡선 CSV · 주문 CSV + 텔레그램(주문/오류 시, 하루 1회 요약)

실행:
    python -X utf8 run_ensemble_trading.py            # 실주문
    python -X utf8 run_ensemble_trading.py --dry-run  # 주문 없이 계산·로그만 (처음 1~2주 권장)
    환경변수 DRY_RUN=true 도 같은 효과
"""
import os
import sys
import json
import argparse
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from backtester import rule_sim as R
from utils.logger import get_logger

logger = get_logger("ensemble")
LOG_DIR = ROOT / "bot_logs"

# timeframe → (하루당 봉 수, Bybit interval 문자열)
TIMEFRAMES = {"1d": (1, "D"), "4h": (6, "240"), "1h": (24, "60")}


# ── 로그 유틸 ────────────────────────────────────────────────────────
def upsert_csv(path: Path, row: dict, key: str = "bar"):
    """같은 봉 행이 있으면 교체, 없으면 추가 (같은 봉에 두 번 실행돼도 중복 없음)."""
    df = pd.read_csv(path) if path.exists() else pd.DataFrame()
    if not df.empty and key in df.columns:
        df = df[df[key].astype(str) != str(row[key])]
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    df.to_csv(path, index=False)


def append_csv(path: Path, rows: list):
    if not rows:
        return
    pd.DataFrame(rows).to_csv(path, mode="a", header=not path.exists(), index=False)


# ── 핵심 계산 (거래소와 무관 — 오프라인 테스트 가능) ──────────────────
def tf_info(cfg: dict):
    return TIMEFRAMES[cfg.get("timeframe", "1d")]


def btc_bull_series(index, D_daily):
    """BTC 일봉 200일선 강세 여부(전날 확정값)를 임의 인덱스에 매핑."""
    btc = D_daily["BTC"].Close
    bull = btc > btc.rolling(200).mean()
    bull.index = bull.index + pd.Timedelta(days=1)          # 그날 마감 후에야 확정
    return bull.reindex(index, method="ffill").fillna(False)


def compute_targets(D: dict, cfg: dict, D_daily: dict = None) -> pd.DataFrame:
    """1배(또는 exposure_multiplier배) 목표 비중. 단기봉이면 BTC 추세는 일봉 200일선 사용."""
    bpd, _ = tf_info(cfg)
    k = float(cfg.get("exposure_multiplier", 1.0))
    bull = btc_bull_series(D["BTC"].index, D_daily) if (bpd > 1 and D_daily is not None) else None
    return R.ensemble_weights(D, cfg["lookbacks"], cfg["vol_target"] * k, cfg["lev_cap"] * k,
                              short_frac=cfg["short_frac"], short_bear_only=cfg["short_bear_only"],
                              bpd=bpd, bull=bull)


def exchange_leverage_for(df_daily: pd.DataFrame, cfg: dict, margin_mode: str = "UNKNOWN") -> int:
    """종목별 거래소 레버리지 설정값 (손익이 아니라 '묶이는 증거금'과 '청산 거리'만 바꾼다).

    · 교차 마진(REGULAR_MARGIN): 청산은 계좌 전체 기준이라, 총노출 10배까지 증거금이 부족하지
      않도록 exchange_leverage_cross(기본 20)로 설정.
    · 격리 마진/불명: 종목별 청산(≈1/레버리지 하락)이 비상 손절(3×일봉ATR)보다 먼저 오지 않게
      청산 거리의 70% 안쪽에 손절이 오는 레버리지로 제한.
    """
    if margin_mode == "REGULAR_MARGIN":
        return int(cfg.get("exchange_leverage_cross", 20))
    a = float(R.atr(df_daily, 14).iloc[-1]); px = float(df_daily.Close.iloc[-1])
    stop_dist = cfg["disaster_stop_atr"] * a / px
    lev = int(np.floor(0.7 / stop_dist)) if stop_dist > 0 else cfg["exchange_leverage_max"]
    return int(np.clip(lev, 1, cfg["exchange_leverage_max"]))


def plan_orders(targets: dict, current: dict, prices: dict, equity: float, instruments: dict,
                cfg: dict, halted: bool) -> list:
    """종목별 조정 계획. 반환 항목: action, symbol, side, qty, reason, target/current 명목."""
    band = cfg["rebalance_band"]
    ratio = cfg["min_order_round_up_ratio"]
    plans = []
    for s, w in targets.items():
        px = prices[s]
        tgt = w * equity                       # 부호 있는 목표 명목(USDT)
        cur = current.get(s, 0.0)              # 부호 있는 현재 명목
        ins = instruments[s]
        min_n = max(ins["min_qty"] * px, ins["min_notional"])
        base = dict(symbol=s, target_usdt=round(tgt, 2), current_usdt=round(cur, 2),
                    min_order_usdt=round(min_n, 2), price=px)

        def add(action, side, notional, reason, pos_side, reduce_only=False):
            plans.append(dict(base, action=action, side=side, qty=abs(notional) / px,
                              notional=round(abs(notional), 2), pos_side=pos_side,
                              reduce_only=reduce_only, reason=reason))

        if tgt == 0 or abs(tgt) < 1e-9:                       # 목표 0 → 전량 청산
            if cur != 0:
                add("close", "Sell" if cur > 0 else "Buy", cur, "신호 소멸",
                    "long" if cur > 0 else "short", True)
            continue
        if cur != 0 and np.sign(cur) != np.sign(tgt):          # 방향 전환
            add("close", "Sell" if cur > 0 else "Buy", cur, "방향 전환",
                "long" if cur > 0 else "short", True)
            cur = 0.0
        delta = tgt - cur
        if cur != 0 and abs(delta) <= band * abs(cur):
            plans.append(dict(base, action="hold", reason=f"밴드 이내({abs(delta)/abs(cur)*100:.0f}%)"))
            continue
        increasing = abs(tgt) > abs(cur)
        if increasing and halted:
            plans.append(dict(base, action="skip_halt", reason="일일 손실 한도 → 증액 중단"))
            continue
        side_long = tgt > 0
        if increasing:
            notional = abs(delta)
            if notional < min_n:
                if notional >= ratio * min_n and (abs(cur) + min_n) <= 2.0 * abs(tgt):
                    notional = min_n
                    reason = "최소 주문으로 올림"
                else:
                    plans.append(dict(base, action="skip_min_order",
                                      reason=f"필요 {abs(delta):.2f} < 최소 {min_n:.2f} USDT"))
                    continue
            else:
                reason = "신규 진입" if cur == 0 else "증액"
            add("open", "Buy" if side_long else "Sell", notional, reason,
                "long" if side_long else "short")
        else:
            notional = abs(delta)
            if notional < min_n:
                plans.append(dict(base, action="skip_min_order",
                                  reason=f"축소 {notional:.2f} < 최소 {min_n:.2f} USDT"))
                continue
            add("reduce", "Sell" if side_long else "Buy", notional, "감액",
                "long" if side_long else "short", True)
    return plans


def auto_params(cfg: dict) -> dict:
    a = cfg["auto_leverage"]
    return dict(vol_target=a["portfolio_vol_target"], dd_limit=a["dd_limit"],
                k_min=a["k_min"], k_max=a["k_max"])


def decide_multiplier(D: dict, W1: pd.DataFrame, cfg: dict, equity: float, peak: float):
    """이번 봉에 적용할 노출 배수 k와 그 근거.

    leverage_mode=auto : 변동성 목표 + 낙폭 브레이크 (backtester/rule_sim.auto_multiplier)
    leverage_mode=fixed: exposure_multiplier 고정
    """
    if cfg.get("leverage_mode", "fixed") != "auto":
        k = float(cfg.get("exposure_multiplier", 1.0))
        return k, {"mode": "fixed", "k": k}
    bpd, _ = tf_info(cfg)
    vol = float(R.model_vol(D, W1, bpd=bpd).iloc[-1])
    dd = max(0.0, 1 - equity / peak) if peak and peak > 0 else 0.0
    k, comp = R.auto_multiplier(vol, dd, auto_params(cfg))
    return k, dict(comp, mode="auto", k=round(k, 3))


def cap_gross(W_row: pd.Series, max_gross: float):
    """총노출(|비중| 합)이 상한을 넘으면 비례 축소."""
    g = float(W_row.abs().sum())
    return (W_row * (max_gross / g), True) if g > max_gross > 0 else (W_row, False)


def shadow_curves(D: dict, W1: pd.DataFrame, start: pd.Timestamp, cfg: dict, D_daily: dict):
    """모델 D(같은 배수 규칙·이상적 체결)와 기준선 B2(가상, 일봉)의 시작일 대비 자산 배수."""
    bpd, _ = tf_info(cfg)
    end = D["BTC"].index[-1]
    out = {"d_model": 1.0, "b2": 1.0, "b2_trades": 0, "b2_last_trades": []}
    if start <= end:
        if cfg.get("leverage_mode", "fixed") == "auto":
            d_eq, _ = R.run_weights_auto(D, W1, start, end, auto_params(cfg), bpd=bpd)
        else:
            d_eq, _ = R.run_weights(D, W1 * float(cfg.get("exposure_multiplier", 1.0)), start, end,
                                    band=0.2, bpd=bpd)
        out["d_model"] = float(d_eq.iloc[-1]) if len(d_eq) else 1.0
    dend = D_daily["BTC"].index[-1]
    if start <= dend:
        b2_eq, b2_tr = R.run_discrete(D_daily, R.RULE_B2, start, dend)
        out.update(b2=float(b2_eq.iloc[-1]) if len(b2_eq) else 1.0, b2_trades=len(b2_tr),
                   b2_last_trades=[dict(t, d0=str(t["d0"].date()), d1=str(t["d1"].date()),
                                        pnl=round(float(t["pnl"]), 5)) for t in b2_tr[-5:]])
    return out


# ── 실행 ─────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--offline-data", help="테스트용: 거래소 대신 이 폴더의 CSV(설정 timeframe) + 가짜 브로커")
    ap.add_argument("--offline-daily", default=str(ROOT / "research" / "coin_rules_sim" / "data"),
                    help="테스트용 일봉 CSV 폴더")
    ap.add_argument("--offline-equity", type=float, default=140.0)
    args = ap.parse_args()
    dry = args.dry_run or os.environ.get("DRY_RUN", "").lower() == "true" or bool(args.offline_data)

    cfg = yaml.safe_load(open(ROOT / "config" / "ensemble.yaml", encoding="utf-8"))
    syms = cfg["symbols"]
    bpd, interval = tf_info(cfg)
    (LOG_DIR / "runs").mkdir(parents=True, exist_ok=True)
    run_ts = datetime.now(timezone.utc)

    telegram = None
    try:
        from utils.telegram_notifier import TelegramNotifier
        telegram = TelegramNotifier()
    except Exception:
        pass

    report = {"run_at_utc": run_ts.isoformat(timespec="seconds"), "dry_run": dry,
              "timeframe": cfg.get("timeframe", "1d"), "config": cfg, "errors": []}
    try:
        # 1) 데이터 (설정 봉 + 일봉)
        if args.offline_data:
            from tests.fake_broker import FakeBroker
            D = R.load(args.offline_data)
            D_daily = R.load(args.offline_daily)
            broker = FakeBroker(D, equity=args.offline_equity)
        else:
            from broker.bybit_futures_broker import BybitFuturesBroker
            broker = BybitFuturesBroker()
            need = int(cfg.get("bars_history", 2000 if bpd > 1 else 1000))
            raw = {s: broker.get_klines(f"{s}-USD", interval, need) for s in syms}
            idx = sorted(set.intersection(*[set(v.index) for v in raw.values()]))
            D = {s: raw[s].loc[idx] for s in syms}
            if bpd == 1:
                D_daily = D
            else:
                rawd = {s: broker.get_klines(f"{s}-USD", "D", 1000) for s in syms}
                idxd = sorted(set.intersection(*[set(v.index) for v in rawd.values()]))
                D_daily = {s: rawd[s].loc[idxd] for s in syms}
        last_bar = D["BTC"].index[-1]
        bar_key = last_bar.strftime("%Y-%m-%d %H:%M")
        report["last_closed_bar"] = bar_key

        # 2) 1배 기준 목표 비중
        W1 = compute_targets(D, dict(cfg, exposure_multiplier=1), D_daily)

        # 3) 계좌 상태
        equity = broker.get_total_equity()
        margin_mode = broker.get_margin_mode()
        report["margin_mode"] = margin_mode
        positions = broker.get_positions()
        current, pos_detail, outside = {}, [], []
        for p in positions:
            s = p.symbol.replace("USDT", "")
            signed = p.size * p.mark_price * (1 if p.side == "Buy" else -1)
            pos_detail.append(dict(symbol=s, side=p.side, size=p.size, avg=p.avg_price,
                                   mark=p.mark_price, upnl=round(p.unrealized_pnl, 4),
                                   sl=p.stop_loss, tp=p.take_profit))
            if s in syms:
                current[s] = current.get(s, 0.0) + signed
            else:
                outside.append(s)
        prices = {s: broker.get_price(f"{s}-USD") for s in syms}
        instruments = {s: broker.get_instrument(f"{s}-USD") for s in syms}

        # 24시간 손실 중단 판정 + 낙폭 기준 최고 자산
        eq_path = LOG_DIR / "equity.csv"
        halted, peak = False, equity
        if eq_path.exists():
            hist = pd.read_csv(eq_path)
            if len(hist):
                peak = max(peak, float(hist["real_equity"].max()))
                ts = pd.to_datetime(hist["run_at_utc"], utc=True, errors="coerce")
                old = hist[ts <= pd.Timestamp(run_ts) - pd.Timedelta(hours=20)]
                if len(old):
                    ref = float(old["real_equity"].iloc[-1])
                    if ref > 0 and (ref - equity) / ref >= cfg["daily_loss_halt"]:
                        halted = True

        # 자동 노출 배수 + 총노출 상한
        k, k_info = decide_multiplier(D, W1, cfg, equity, peak)
        W_last, capped = cap_gross(W1.iloc[-1] * k, float(cfg.get("max_gross_exposure", 10)))
        targets = W_last.to_dict()
        k_info["gross_capped"] = capped
        report["leverage"] = k_info

        # 4) 주문 계획 → 실행
        plans = plan_orders(targets, current, prices, equity, instruments, cfg, halted)
        executed = []
        if not dry:
            for s in {pl["symbol"] for pl in plans if pl["action"] == "open"}:
                lev = exchange_leverage_for(D_daily[s], cfg, margin_mode)
                report.setdefault("exchange_leverage", {})[s] = lev
                broker.set_leverage(f"{s}-USD", lev)
        for pl in plans:
            if pl["action"] not in ("open", "reduce", "close"):
                continue
            rec = dict(run_at_utc=report["run_at_utc"], bar=bar_key, symbol=pl["symbol"],
                       action=pl["action"], side=pl["side"], notional=pl["notional"], price=pl["price"],
                       reason=pl["reason"], dry_run=dry, status="", order_id="", error="")
            if dry:
                rec["status"] = "dry_run"
            else:
                try:
                    sym = f"{pl['symbol']}-USD"
                    if pl["action"] == "close":
                        o = broker.close_position(sym, pl["pos_side"])
                        rec.update(status="sent", order_id=getattr(o, "order_id", ""))
                    else:
                        o = broker.place_qty_order(sym, pl["side"], pl["qty"], pl["pos_side"],
                                                   reduce_only=pl["reduce_only"])
                        rec.update(status="sent", order_id=o["order_id"])
                except Exception as e:
                    rec.update(status="failed", error=str(e)[:300])
                    report["errors"].append(f"{pl['symbol']} {pl['action']}: {e}")
            executed.append(rec)

        # 5) 비상 손절 갱신 (일봉 ATR 기준, 구 봇이 남긴 익절은 제거)
        stops = []
        if not dry:
            for p in broker.get_positions():
                s = p.symbol.replace("USDT", "")
                if s not in syms:
                    continue
                a = float(R.atr(D_daily[s], 14).iloc[-1])
                long_ = p.side == "Buy"
                sl = p.mark_price - cfg["disaster_stop_atr"] * a if long_ else p.mark_price + cfg["disaster_stop_atr"] * a
                ok = broker.update_stop_loss(f"{s}-USD", "long" if long_ else "short", sl)
                if p.take_profit and p.take_profit > 0:
                    try:
                        broker.update_take_profit(f"{s}-USD", "long" if long_ else "short", 0)
                    except Exception as e:
                        report["errors"].append(f"{s} TP 제거 실패: {e}")
                stops.append(dict(symbol=s, side=p.side, stop=round(sl, 6), ok=ok))

        # 6) 실현손익 내역 누적 (거래소 기준 = 진짜 체결 기록)
        new_closed = []
        try:
            closed = broker.get_closed_pnl(limit=100)
            cp_path = LOG_DIR / "closed_pnl.csv"
            seen = set(pd.read_csv(cp_path)["orderId"].astype(str)) if cp_path.exists() else set()
            for c in closed:
                if str(c.get("orderId")) in seen:
                    continue
                new_closed.append({kk: c.get(kk) for kk in ("orderId", "symbol", "side", "qty",
                                   "avgEntryPrice", "avgExitPrice", "closedPnl", "leverage",
                                   "updatedTime")})
            append_csv(cp_path, sorted(new_closed, key=lambda r: int(r["updatedTime"] or 0)))
        except Exception as e:
            report["errors"].append(f"실현손익 조회 실패: {e}")

        # 7) 가상 곡선 (모델 D / 기준선 B2)
        shadow = shadow_curves(D, W1, pd.Timestamp(cfg["shadow_start"]), cfg, D_daily)
        first_eq = None
        if eq_path.exists():
            prev_all = pd.read_csv(eq_path)
            if len(prev_all):
                first_eq = float(prev_all["real_equity"].iloc[0])
        first_eq = first_eq or equity
        skipped = [p for p in plans if p["action"] == "skip_min_order"]
        row = dict(run_at_utc=report["run_at_utc"], bar=bar_key,
                   real_equity=round(equity, 4), real_index=round(equity / first_eq, 4),
                   d_model_index=round(shadow["d_model"], 4), b2_index=round(shadow["b2"], 4),
                   btc_close=float(D["BTC"].Close.iloc[-1]),
                   gross_target=round(float(W_last.abs().sum()), 4),
                   multiplier=round(k, 3), k_vol=k_info.get("k_vol"), k_dd=k_info.get("k_dd"),
                   drawdown=k_info.get("drawdown"),
                   n_orders=len([e for e in executed if e["status"] in ("sent", "dry_run")]),
                   n_failed=len([e for e in executed if e["status"] == "failed"]),
                   n_skipped_min=len(skipped), halted=halted, margin_mode=margin_mode, dry_run=dry)
        upsert_csv(eq_path, row)
        append_csv(LOG_DIR / "orders.csv", executed)

        report.update(equity=equity, halted=halted, targets={kk: round(v, 5) for kk, v in targets.items()},
                      positions_before=pos_detail, outside_universe_positions=outside,
                      plans=plans, executed=executed, stops=stops, new_closed_pnl=new_closed,
                      shadow=shadow, summary=row)
        (LOG_DIR / "runs" / f"{last_bar.strftime('%Y-%m-%d_%H%M')}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

        # 텔레그램: 주문·오류·중단이 있을 때 + 하루 1회(UTC 0시대 실행) 요약
        kst = (run_ts + timedelta(hours=9)).strftime("%m-%d %H:%M")
        lines = [f"📊 <b>앙상블 봇 ({cfg.get('timeframe', '1d')})</b> {kst} KST {'(DRY RUN)' if dry else ''}",
                 f"자산 {equity:.2f} USDT · 실계좌 {row['real_index']:.3f}x | 모델D {row['d_model_index']:.3f}x | B2 {row['b2_index']:.3f}x",
                 f"노출 {k:.2f}배 ({k_info['mode']}"
                 + (f": 변동성기준 {k_info['k_vol']:.2f} · 낙폭기준 {k_info['k_dd']:.2f}, 계좌낙폭 {k_info['drawdown']*100:.1f}%)"
                    if k_info["mode"] == "auto" else ")")
                 + f" · 총노출 {row['gross_target']:.2f}배",
                 f"주문 {row['n_orders']}건 · 실패 {row['n_failed']} · 최소주문 미달 {row['n_skipped_min']}"]
        for e in executed:
            lines.append(f"  {e['symbol']} {e['action']} {e['side']} {e['notional']}U ({e['reason']}) {e['status']}")
        if halted:
            lines.append("⛔ 24시간 손실 한도 → 증액 중단")
        if outside:
            lines.append(f"⚠️ 관리 대상 밖 포지션: {', '.join(outside)} (수동 확인)")
        if margin_mode not in ("REGULAR_MARGIN", "UNKNOWN"):
            lines.append(f"⚠️ 마진 모드 {margin_mode}: 교차 마진(Cross) 권장 — 레버리지를 보수적으로 제한 중")
        if report["errors"]:
            lines.append("❗ " + " / ".join(report["errors"])[:500])
        msg = "\n".join(lines)
        print(msg.replace("<b>", "").replace("</b>", ""))
        notify = executed or report["errors"] or halted or run_ts.hour == 0
        if telegram and getattr(telegram, "_enabled", False) and notify:
            telegram.send(msg)
        return 0 if not report["errors"] else 2

    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"앙상블 봇 실행 실패: {e}\n{tb}")
        report["errors"].append(tb)
        (LOG_DIR / "runs" / f"{run_ts.strftime('%Y-%m-%d_%H%M')}_ERROR.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        if telegram and getattr(telegram, "_enabled", False):
            telegram.send(f"❗ 앙상블 봇 실행 실패: {str(e)[:300]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

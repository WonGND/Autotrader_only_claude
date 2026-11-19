# -*- coding: utf-8 -*-
"""
봇 계좌(Unified Trading)에 입출금·이체를 한 뒤 실행 — 자산 기록을 이체 비율만큼 맞춘다.

왜 필요한가: 앙상블 봇은 bot_logs/equity.csv의 최고 자산 대비 낙폭과 20시간 전 대비 손실로
레버리지 브레이크·24시간 손실 중단을 판단한다. 이체로 잔고가 줄면 이를 '손실'로 오해하고,
늘면 낙폭이 실제보다 작아 보인다. 과거 기록 전체를 같은 비율로 조정하면 수익률 곡선은 그대로 두고
기준점만 새 잔고에 맞출 수 있다.

사용법 (서버에서, 이체를 '마친 뒤'):
    .venv/bin/python -X utf8 tools/record_transfer.py --out 36      # 봇 계좌에서 36 USDT를 뺐을 때
    .venv/bin/python -X utf8 tools/record_transfer.py --in 50       # 봇 계좌에 50 USDT를 넣었을 때
    (--dry-run 을 붙이면 계산만 보여주고 파일은 건드리지 않음)
현재 잔고는 거래소에서 직접 읽고, 원본은 bot_logs/equity_backup_<시각>.csv 로 남긴다.
"""
import sys
import argparse
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LOG_DIR = ROOT / "bot_logs"


def scale_factor(equity_now: float, amount_in: float) -> float:
    """이체 후 잔고와 이체 금액(+입금, −출금)으로 '이체 전 → 이체 후' 비율을 구한다."""
    before = equity_now - amount_in
    if before <= 0 or equity_now <= 0:
        raise ValueError(f"잔고가 이상합니다: 이체 후 {equity_now}, 계산된 이체 전 {before}")
    return equity_now / before


def apply(eq_path: Path, factor: float, amount_in: float, equity_now: float, dry: bool = False) -> dict:
    df = pd.read_csv(eq_path)
    before_last, before_peak = float(df["real_equity"].iloc[-1]), float(df["real_equity"].max())
    out = dict(rows=len(df), factor=round(factor, 6),
               last=(round(before_last, 4), round(before_last * factor, 4)),
               peak=(round(before_peak, 4), round(before_peak * factor, 4)))
    if dry:
        return out
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%MZ")
    df.to_csv(eq_path.parent / f"equity_backup_{stamp}.csv", index=False)
    df["real_equity"] = (df["real_equity"] * factor).round(4)
    df.to_csv(eq_path, index=False)
    with open(eq_path.parent / "transfers.csv", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')},"
                f"{'deposit' if amount_in > 0 else 'withdraw'},{abs(amount_in)},"
                f"equity_after={equity_now},scaled_by={factor:.6f}\n")
    return out


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--out", type=float, help="봇 계좌에서 뺀 USDT")
    g.add_argument("--in", dest="inn", type=float, help="봇 계좌에 넣은 USDT")
    ap.add_argument("--equity", type=float, help="이체 후 잔고 (생략하면 거래소에서 읽음)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    amount_in = a.inn if a.inn is not None else -a.out

    equity_now = a.equity
    if equity_now is None:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        from broker.bybit_futures_broker import BybitFuturesBroker
        equity_now = BybitFuturesBroker().get_total_equity()

    eq_path = LOG_DIR / "equity.csv"
    if not eq_path.exists():
        print("equity.csv가 없습니다 (봇이 아직 한 번도 안 돌았으면 조정할 필요 없음)")
        return
    f = scale_factor(equity_now, amount_in)
    r = apply(eq_path, f, amount_in, equity_now, dry=a.dry_run)
    print(f"{'(시험) ' if a.dry_run else ''}이체 후 잔고 {equity_now:.4f} · 비율 {r['factor']} · 기록 {r['rows']}건")
    print(f"  최근 자산 {r['last'][0]} → {r['last'][1]} / 최고 자산 {r['peak'][0]} → {r['peak'][1]}")


if __name__ == "__main__":
    main()

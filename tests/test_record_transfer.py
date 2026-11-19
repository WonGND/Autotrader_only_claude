"""이체 후 자산 기록 조정(tools/record_transfer.py) 테스트."""
import pandas as pd
import pytest

from tools import record_transfer as T


def test_factor_withdraw_and_deposit():
    assert round(T.scale_factor(28.27, -36), 4) == round(28.27 / 64.27, 4)
    assert T.scale_factor(150, 50) == 1.5
    with pytest.raises(ValueError):
        T.scale_factor(10, 20)          # 이체 전 잔고가 음수가 되는 입력


def test_apply_scales_backs_up_and_logs(tmp_path):
    p = tmp_path / "equity.csv"
    pd.DataFrame({"run_at_utc": ["a", "b"], "real_equity": [100.0, 80.0], "real_index": [1.0, 0.8]}).to_csv(p, index=False)
    r = T.apply(p, 0.5, -40, 40.0)
    d = pd.read_csv(p)
    assert list(d["real_equity"]) == [50.0, 40.0] and list(d["real_index"]) == [1.0, 0.8]
    assert r["peak"] == (100.0, 50.0)
    assert len(list(tmp_path.glob("equity_backup_*.csv"))) == 1
    assert "withdraw,40" in (tmp_path / "transfers.csv").read_text()


def test_dry_run_changes_nothing(tmp_path):
    p = tmp_path / "equity.csv"
    pd.DataFrame({"real_equity": [100.0]}).to_csv(p, index=False)
    T.apply(p, 0.5, -50, 50.0, dry=True)
    assert pd.read_csv(p)["real_equity"].iloc[0] == 100.0 and not (tmp_path / "transfers.csv").exists()

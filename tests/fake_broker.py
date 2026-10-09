"""오프라인 테스트용 가짜 브로커 (실제 Bybit 호출 없음). run_ensemble_trading.py --offline-data 에서 사용."""
from types import SimpleNamespace

LOTS = {"BTC": 0.001, "ETH": 0.01, "BNB": 0.01, "SOL": 0.1, "XRP": 1, "DOGE": 1,
        "ADA": 1, "AVAX": 0.1, "LINK": 0.1, "DOT": 0.1}


class FakeBroker:
    def __init__(self, D, equity=140.0, positions=None):
        self.D, self.equity = D, equity
        self.pos = positions or []   # SimpleNamespace(symbol, side, size, avg_price, mark_price, ...)

    def _px(self, symbol):
        return float(self.D[symbol.replace("-USD", "").replace("USDT", "")].Close.iloc[-1])

    def get_total_equity(self): return self.equity
    def get_positions(self, symbol=None): return list(self.pos)
    def get_price(self, symbol): return self._px(symbol)
    def get_instrument(self, symbol):
        s = symbol.replace("-USD", "")
        return {"qty_step": LOTS[s], "min_qty": LOTS[s], "min_notional": 5.0}
    def get_closed_pnl(self, limit=100): return []
    def set_leverage(self, *a): return True


def pos(symbol, side, size, price):
    return SimpleNamespace(symbol=f"{symbol}USDT", side=side, size=size, avg_price=price,
                           mark_price=price, unrealized_pnl=0.0, leverage=3,
                           stop_loss=0.0, take_profit=0.0)

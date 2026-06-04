"""
MA Crossover Backtest Analysis for Korean Stocks, US Stocks, and Crypto
Period: 2024-01-01 to 2024-12-31
Strategy: MA Crossover (short=5, long=20)
Initial Capital: 10,000,000 KRW
"""

import sys
import os
import json
import traceback
import warnings
warnings.filterwarnings("ignore")

# Add project root to path
sys.path.insert(0, "/home/user/Autotrader_only_claude")

from backtester.engine import BacktestEngine
from strategies.ma_crossover import MACrossoverStrategy
from data.fetcher import DataFetcher

START_DATE = "2024-01-01"
END_DATE = "2024-12-31"
INITIAL_CAPITAL = 10_000_000

KOREAN_STOCKS = [
    "005930", "000660", "035420", "051910", "006400", "207940", "005380", "068270", "028260", "035720",
    "003550", "096770", "017670", "066570", "009150", "032830", "105560", "055550", "316140", "086790",
    "018260", "010950", "015760", "034730", "011200", "036570", "000270", "010130", "090430", "033780",
    "004020", "047050", "012330", "000810", "011170", "008770", "251270", "035250", "003490", "097950",
    "006800", "002790", "005490", "011780", "028050", "030200", "003240", "000120", "011140", "009830",
]

US_STOCKS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "BRK-B", "JPM", "JNJ",
    "V", "PG", "UNH", "MA", "HD", "CVX", "MRK", "ABBV", "PEP", "KO",
    "AVGO", "COST", "WMT", "TMO", "MCD", "CSCO", "ACN", "LLY", "TXN", "NEE",
    "ORCL", "DHR", "PM", "RTX", "NFLX", "ADBE", "BMY", "AMGN", "QCOM", "HON",
    "IBM", "GE", "CAT", "BA", "MMM", "SBUX", "PYPL", "INTC", "AMD", "CRM",
]

CRYPTO = [
    "BTC-USD", "ETH-USD", "BNB-USD", "XRP-USD", "ADA-USD",
    "SOL-USD", "DOGE-USD", "DOT-USD", "AVAX-USD", "MATIC-USD",
]

KR_NAMES = {
    "005930": "삼성전자", "000660": "SK하이닉스", "035420": "NAVER", "051910": "LG화학",
    "006400": "삼성SDI", "207940": "삼성바이오로직스", "005380": "현대차", "068270": "셀트리온",
    "028260": "삼성물산", "035720": "카카오", "003550": "LG", "096770": "SK이노베이션",
    "017670": "SK텔레콤", "066570": "LG전자", "009150": "삼성전기", "032830": "삼성생명",
    "105560": "KB금융", "055550": "신한지주", "316140": "우리금융지주", "086790": "하나금융지주",
    "018260": "삼성에스디에스", "010950": "S-Oil", "015760": "한국전력", "034730": "SK",
    "011200": "HMM", "036570": "엔씨소프트", "000270": "기아", "010130": "고려아연",
    "090430": "아모레퍼시픽", "033780": "KT&G", "004020": "현대제철", "047050": "포스코인터내셔널",
    "012330": "현대모비스", "000810": "삼성화재", "011170": "롯데케미칼", "008770": "호텔신라",
    "251270": "넷마블", "035250": "강원랜드", "003490": "대한항공", "097950": "CJ제일제당",
    "006800": "미래에셋증권", "002790": "아모레G", "005490": "POSCO홀딩스", "011780": "금호석유",
    "028050": "삼성엔지니어링", "030200": "KT", "003240": "태광산업", "000120": "CJ대한통운",
    "011140": "한세실업", "009830": "한화솔루션",
}

US_NAMES = {
    "AAPL": "Apple", "MSFT": "Microsoft", "GOOGL": "Alphabet", "AMZN": "Amazon",
    "META": "Meta", "NVDA": "NVIDIA", "TSLA": "Tesla", "BRK-B": "Berkshire Hathaway",
    "JPM": "JPMorgan Chase", "JNJ": "Johnson & Johnson", "V": "Visa", "PG": "Procter & Gamble",
    "UNH": "UnitedHealth", "MA": "Mastercard", "HD": "Home Depot", "CVX": "Chevron",
    "MRK": "Merck", "ABBV": "AbbVie", "PEP": "PepsiCo", "KO": "Coca-Cola",
    "AVGO": "Broadcom", "COST": "Costco", "WMT": "Walmart", "TMO": "Thermo Fisher",
    "MCD": "McDonald's", "CSCO": "Cisco", "ACN": "Accenture", "LLY": "Eli Lilly",
    "TXN": "Texas Instruments", "NEE": "NextEra Energy", "ORCL": "Oracle",
    "DHR": "Danaher", "PM": "Philip Morris", "RTX": "Raytheon", "NFLX": "Netflix",
    "ADBE": "Adobe", "BMY": "Bristol-Myers Squibb", "AMGN": "Amgen", "QCOM": "Qualcomm",
    "HON": "Honeywell", "IBM": "IBM", "GE": "GE", "CAT": "Caterpillar",
    "BA": "Boeing", "MMM": "3M", "SBUX": "Starbucks", "PYPL": "PayPal",
    "INTC": "Intel", "AMD": "AMD", "CRM": "Salesforce",
}

CRYPTO_NAMES = {
    "BTC-USD": "Bitcoin", "ETH-USD": "Ethereum", "BNB-USD": "Binance Coin",
    "XRP-USD": "XRP", "ADA-USD": "Cardano", "SOL-USD": "Solana",
    "DOGE-USD": "Dogecoin", "DOT-USD": "Polkadot", "AVAX-USD": "Avalanche",
    "MATIC-USD": "Polygon",
}


def run_backtest_for_symbol(symbol, asset_class, name):
    """Run backtest for a single symbol and return result dict."""
    strategy = MACrossoverStrategy({"short_window": 5, "long_window": 20})
    engine = BacktestEngine(
        strategy=strategy,
        initial_capital=INITIAL_CAPITAL,
        commission_rate=0.00015,
        sell_commission_rate=0.003,
        position_size_pct=0.2,
    )

    try:
        result = engine.run(symbol, START_DATE, END_DATE, save_to_db=False)

        # Get price data for start/end prices
        fetcher = DataFetcher()
        data = fetcher.get_ohlcv(symbol, START_DATE, END_DATE)

        start_price = float(data["Close"].iloc[0]) if not data.empty else None
        end_price = float(data["Close"].iloc[-1]) if not data.empty else None
        price_change_pct = ((end_price - start_price) / start_price * 100) if (start_price and end_price) else None

        return {
            "symbol": symbol,
            "name": name,
            "asset_class": asset_class,
            "success": True,
            "total_return_pct": round(result.total_return * 100, 4),
            "max_drawdown_pct": round(result.max_drawdown * 100, 4),
            "sharpe_ratio": round(result.sharpe_ratio, 4),
            "win_rate_pct": round(result.win_rate * 100, 2),
            "num_trades": result.total_trades,
            "winning_trades": result.winning_trades,
            "losing_trades": result.losing_trades,
            "final_capital": round(result.final_capital, 2),
            "initial_capital": INITIAL_CAPITAL,
            "start_price": round(start_price, 4) if start_price else None,
            "end_price": round(end_price, 4) if end_price else None,
            "price_change_pct": round(price_change_pct, 4) if price_change_pct else None,
            "error": None,
        }
    except Exception as e:
        return {
            "symbol": symbol,
            "name": name,
            "asset_class": asset_class,
            "success": False,
            "total_return_pct": None,
            "max_drawdown_pct": None,
            "sharpe_ratio": None,
            "win_rate_pct": None,
            "num_trades": None,
            "winning_trades": None,
            "losing_trades": None,
            "final_capital": None,
            "initial_capital": INITIAL_CAPITAL,
            "start_price": None,
            "end_price": None,
            "price_change_pct": None,
            "error": str(e),
        }


def print_table(results, title):
    print(f"\n{'='*120}")
    print(f"  {title}")
    print(f"{'='*120}")
    header = f"{'Symbol':<12} {'Name':<25} {'Return%':>9} {'MaxDD%':>9} {'Sharpe':>8} {'WinRate%':>9} {'Trades':>7} {'FinalCapital':>15} {'PriceChg%':>10} {'Status':<10}"
    print(header)
    print("-"*120)
    for r in results:
        if r["success"]:
            print(
                f"{r['symbol']:<12} {r['name'][:24]:<25} "
                f"{r['total_return_pct']:>+9.2f} "
                f"{r['max_drawdown_pct']:>9.2f} "
                f"{r['sharpe_ratio']:>8.3f} "
                f"{r['win_rate_pct']:>9.1f} "
                f"{r['num_trades']:>7d} "
                f"{r['final_capital']:>15,.0f} "
                f"{(r['price_change_pct'] if r['price_change_pct'] is not None else 0):>+10.2f} "
                f"{'OK':<10}"
            )
        else:
            print(f"{r['symbol']:<12} {r['name'][:24]:<25} {'FAILED':>9} {'':>9} {'':>8} {'':>9} {'':>7} {'':>15} {'':>10} {str(r['error'])[:20]:<10}")


def compute_class_stats(results):
    successful = [r for r in results if r["success"]]
    if not successful:
        return {}
    returns = [r["total_return_pct"] for r in successful]
    drawdowns = [r["max_drawdown_pct"] for r in successful]
    sharpes = [r["sharpe_ratio"] for r in successful]
    win_rates = [r["win_rate_pct"] for r in successful]
    trades = [r["num_trades"] for r in successful]
    return {
        "count": len(results),
        "success_count": len(successful),
        "avg_return_pct": round(sum(returns)/len(returns), 4),
        "avg_max_drawdown_pct": round(sum(drawdowns)/len(drawdowns), 4),
        "avg_sharpe": round(sum(sharpes)/len(sharpes), 4),
        "avg_win_rate_pct": round(sum(win_rates)/len(win_rates), 4),
        "avg_trades": round(sum(trades)/len(trades), 2),
        "best_return_pct": round(max(returns), 4),
        "worst_return_pct": round(min(returns), 4),
        "positive_return_count": sum(1 for r in returns if r > 0),
    }


def main():
    all_results = []

    print("\n" + "="*60)
    print("  MA Crossover Backtest (5/20)")
    print(f"  Period: {START_DATE} ~ {END_DATE}")
    print(f"  Initial Capital: {INITIAL_CAPITAL:,} KRW")
    print("="*60)

    # Korean Stocks
    print(f"\n[1/3] Running Korean Stocks ({len(KOREAN_STOCKS)} symbols)...")
    kr_results = []
    for i, sym in enumerate(KOREAN_STOCKS):
        name = KR_NAMES.get(sym, sym)
        print(f"  [{i+1:02d}/{len(KOREAN_STOCKS)}] {sym} ({name})...", end=" ", flush=True)
        r = run_backtest_for_symbol(sym, "Korean", name)
        kr_results.append(r)
        all_results.append(r)
        if r["success"]:
            print(f"Return: {r['total_return_pct']:+.2f}%, Trades: {r['num_trades']}")
        else:
            print(f"FAILED: {r['error'][:60]}")

    # US Stocks
    print(f"\n[2/3] Running US Stocks ({len(US_STOCKS)} symbols)...")
    us_results = []
    for i, sym in enumerate(US_STOCKS):
        name = US_NAMES.get(sym, sym)
        print(f"  [{i+1:02d}/{len(US_STOCKS)}] {sym} ({name})...", end=" ", flush=True)
        r = run_backtest_for_symbol(sym, "US", name)
        us_results.append(r)
        all_results.append(r)
        if r["success"]:
            print(f"Return: {r['total_return_pct']:+.2f}%, Trades: {r['num_trades']}")
        else:
            print(f"FAILED: {r['error'][:60]}")

    # Crypto
    print(f"\n[3/3] Running Crypto ({len(CRYPTO)} symbols)...")
    crypto_results = []
    for i, sym in enumerate(CRYPTO):
        name = CRYPTO_NAMES.get(sym, sym)
        print(f"  [{i+1:02d}/{len(CRYPTO)}] {sym} ({name})...", end=" ", flush=True)
        r = run_backtest_for_symbol(sym, "Crypto", name)
        crypto_results.append(r)
        all_results.append(r)
        if r["success"]:
            print(f"Return: {r['total_return_pct']:+.2f}%, Trades: {r['num_trades']}")
        else:
            print(f"FAILED: {r['error'][:60]}")

    # Print result tables
    print_table(kr_results, "Korean Stocks Results")
    print_table(us_results, "US Stocks Results")
    print_table(crypto_results, "Crypto Results")

    # Top 10 / Bottom 10
    successful_all = [r for r in all_results if r["success"]]
    successful_all.sort(key=lambda x: x["total_return_pct"], reverse=True)

    print(f"\n{'='*80}")
    print("  TOP 10 BEST PERFORMING (by total return)")
    print(f"{'='*80}")
    for i, r in enumerate(successful_all[:10], 1):
        print(f"  {i:2d}. [{r['asset_class']:7s}] {r['symbol']:<12} {r['name'][:25]:<25} Return: {r['total_return_pct']:+8.2f}%  Sharpe: {r['sharpe_ratio']:6.3f}  Trades: {r['num_trades']}")

    print(f"\n{'='*80}")
    print("  TOP 10 WORST PERFORMING (by total return)")
    print(f"{'='*80}")
    for i, r in enumerate(successful_all[-10:][::-1], 1):
        print(f"  {i:2d}. [{r['asset_class']:7s}] {r['symbol']:<12} {r['name'][:25]:<25} Return: {r['total_return_pct']:+8.2f}%  Sharpe: {r['sharpe_ratio']:6.3f}  Trades: {r['num_trades']}")

    # Asset class stats
    print(f"\n{'='*80}")
    print("  AVERAGE METRICS BY ASSET CLASS")
    print(f"{'='*80}")
    for label, res in [("Korean Stocks", kr_results), ("US Stocks", us_results), ("Crypto", crypto_results)]:
        stats = compute_class_stats(res)
        if stats:
            print(f"\n  {label}:")
            print(f"    Symbols tested:     {stats['count']} (success: {stats['success_count']})")
            print(f"    Avg Return:         {stats['avg_return_pct']:+.2f}%")
            print(f"    Avg Max Drawdown:   {stats['avg_max_drawdown_pct']:.2f}%")
            print(f"    Avg Sharpe:         {stats['avg_sharpe']:.3f}")
            print(f"    Avg Win Rate:       {stats['avg_win_rate_pct']:.1f}%")
            print(f"    Avg Trades:         {stats['avg_trades']:.1f}")
            print(f"    Best Return:        {stats['best_return_pct']:+.2f}%")
            print(f"    Worst Return:       {stats['worst_return_pct']:+.2f}%")
            print(f"    Positive Returns:   {stats['positive_return_count']}/{stats['success_count']}")

    # Overall stats
    print(f"\n{'='*80}")
    print("  OVERALL STATISTICS")
    print(f"{'='*80}")
    overall_stats = compute_class_stats(all_results)
    print(f"  Total symbols:      {overall_stats['count']}")
    print(f"  Successful:         {overall_stats['success_count']}")
    print(f"  Failed:             {overall_stats['count'] - overall_stats['success_count']}")
    print(f"  Avg Return:         {overall_stats['avg_return_pct']:+.2f}%")
    print(f"  Avg Max Drawdown:   {overall_stats['avg_max_drawdown_pct']:.2f}%")
    print(f"  Avg Sharpe:         {overall_stats['avg_sharpe']:.3f}")
    print(f"  Avg Win Rate:       {overall_stats['avg_win_rate_pct']:.1f}%")
    print(f"  Best Return:        {overall_stats['best_return_pct']:+.2f}%  ({successful_all[0]['symbol']} - {successful_all[0]['name']})")
    print(f"  Worst Return:       {overall_stats['worst_return_pct']:+.2f}%  ({successful_all[-1]['symbol']} - {successful_all[-1]['name']})")
    print(f"  Positive Returns:   {overall_stats['positive_return_count']}/{overall_stats['success_count']}")

    # Save results
    output = {
        "meta": {
            "strategy": "MA Crossover (5/20)",
            "start_date": START_DATE,
            "end_date": END_DATE,
            "initial_capital": INITIAL_CAPITAL,
            "total_symbols": len(all_results),
            "successful": overall_stats["success_count"],
            "failed": overall_stats["count"] - overall_stats["success_count"],
        },
        "class_stats": {
            "Korean": compute_class_stats(kr_results),
            "US": compute_class_stats(us_results),
            "Crypto": compute_class_stats(crypto_results),
            "Overall": overall_stats,
        },
        "results": all_results,
    }

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analysis_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n  Results saved to: {out_path}")
    print(f"\n{'='*80}\n")


if __name__ == "__main__":
    main()

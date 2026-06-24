"""
FastAPI 웹 애플리케이션

대시보드, 백테스트, 전략, 거래 내역 등의 웹 UI를 제공합니다.
"""

import os
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from database.models import BacktestResult, Trade, init_db, get_session
from utils.logger import get_logger
from utils.helpers import load_settings, load_strategies_config, format_currency, format_pct

logger = get_logger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="AutoTrader", description="자동 주식 거래 시스템")

# 정적 파일 & 템플릿 설정
app.mount(
    "/static",
    StaticFiles(directory=os.path.join(BASE_DIR, "static")),
    name="static",
)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
templates.env.globals["format_currency"] = format_currency

# 전역 트레이딩 엔진 (main.py에서 주입)
_trading_engine = None
_broker = None


def set_trading_engine(engine, broker):
    global _trading_engine, _broker
    _trading_engine = engine
    _broker = broker


def get_broker():
    if _broker is None:
        # 기본 paper broker
        from broker.paper_broker import PaperBroker
        from utils.helpers import load_settings
        settings = load_settings()
        capital = settings.get("trading", {}).get("initial_capital", 10_000_000)
        return PaperBroker(initial_capital=capital)
    return _broker


# ─── 페이지 라우트 ─────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """메인 대시보드"""
    broker = get_broker()
    try:
        account = broker.get_account_info()
        positions = broker.get_positions()

        positions_list = [
            {
                "symbol": p.symbol,
                "quantity": p.quantity,
                "avg_price": p.avg_price,
                "current_price": p.current_price,
                "unrealized_pnl": p.unrealized_pnl,
                "unrealized_pnl_pct": p.unrealized_pnl_pct * 100,
            }
            for p in positions.values()
        ]

        # 최근 거래 내역
        session = get_session()
        recent_trades = (
            session.query(Trade)
            .order_by(Trade.timestamp.desc())
            .limit(10)
            .all()
        )
        trades_list = [t.to_dict() for t in recent_trades]
        session.close()

        # 포트폴리오 P&L 히스토리 (DB에서)
        pnl_chart_json = _generate_pnl_chart()

        context = {
            "total_value": account.total_value,
            "cash": account.cash,
            "profit_loss": account.profit_loss,
            "profit_loss_pct": account.profit_loss_pct * 100,
            "num_positions": len(positions),
            "positions": positions_list,
            "recent_trades": trades_list,
            "pnl_chart_json": pnl_chart_json,
            "trading_active": _trading_engine.is_running if _trading_engine else False,
        }
    except Exception as e:
        logger.error(f"대시보드 오류: {e}")
        context = {
            "total_value": 0,
            "cash": 0,
            "profit_loss": 0,
            "profit_loss_pct": 0,
            "num_positions": 0,
            "positions": [],
            "recent_trades": [],
            "pnl_chart_json": "{}",
            "trading_active": False,
            "error": str(e),
        }

    return templates.TemplateResponse(request=request, name="dashboard.html", context=context)


@app.get("/strategies", response_class=HTMLResponse)
async def strategies_page(request: Request):
    """전략 목록 페이지"""
    strategies_config = load_strategies_config()
    settings = load_settings()
    active = strategies_config.get("active_strategy", "ma_crossover")

    return templates.TemplateResponse(request=request, name="strategies.html", context={
        "strategies": strategies_config.get("strategies", {}),
        "active_strategy": active,
        "trading_active": _trading_engine.is_running if _trading_engine else False,
    })


@app.get("/backtest", response_class=HTMLResponse)
async def backtest_page(request: Request):
    """백테스트 페이지"""
    strategies_config = load_strategies_config()

    # 최근 백테스트 결과
    session = get_session()
    recent_results = (
        session.query(BacktestResult)
        .order_by(BacktestResult.created_at.desc())
        .limit(5)
        .all()
    )
    results_list = [r.to_dict() for r in recent_results]
    session.close()

    return templates.TemplateResponse(request=request, name="backtest.html", context={
        "strategies": strategies_config.get("strategies", {}),
        "recent_results": results_list,
        "trading_active": _trading_engine.is_running if _trading_engine else False,
    })


@app.get("/trades", response_class=HTMLResponse)
async def trades_page(
    request: Request,
    symbol: Optional[str] = None,
    strategy: Optional[str] = None,
    days: int = 30,
):
    """거래 내역 페이지"""
    session = get_session()
    query = session.query(Trade)

    since = datetime.now() - timedelta(days=days)
    query = query.filter(Trade.timestamp >= since)

    if symbol:
        query = query.filter(Trade.symbol == symbol)
    if strategy:
        query = query.filter(Trade.strategy == strategy)

    trades = query.order_by(Trade.timestamp.desc()).all()
    trades_list = [t.to_dict() for t in trades]
    session.close()

    return templates.TemplateResponse(request=request, name="trades.html", context={
        "trades": trades_list,
        "filter_symbol": symbol or "",
        "filter_strategy": strategy or "",
        "filter_days": days,
        "trading_active": _trading_engine.is_running if _trading_engine else False,
    })


# ─── API 라우트 ────────────────────────────────────────────────


@app.get("/api/portfolio")
async def api_portfolio():
    """포트폴리오 데이터 JSON"""
    broker = get_broker()
    try:
        account = broker.get_account_info()
        positions = broker.get_positions()
        return {
            "total_value": account.total_value,
            "cash": account.cash,
            "invested": account.invested,
            "profit_loss": account.profit_loss,
            "profit_loss_pct": account.profit_loss_pct,
            "positions": [
                {
                    "symbol": p.symbol,
                    "quantity": p.quantity,
                    "avg_price": p.avg_price,
                    "current_price": p.current_price,
                    "unrealized_pnl": p.unrealized_pnl,
                    "unrealized_pnl_pct": p.unrealized_pnl_pct,
                }
                for p in positions.values()
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/prices/{symbol}")
async def api_price(symbol: str):
    """종목 현재가 조회"""
    from data.fetcher import DataFetcher
    fetcher = DataFetcher()
    price = fetcher.get_current_price(symbol)
    if price == 0:
        raise HTTPException(status_code=404, detail=f"{symbol} 가격을 가져올 수 없습니다.")
    return {"symbol": symbol, "price": price}


@app.post("/backtest/run")
async def run_backtest(
    strategy: str = Form(...),
    symbol: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
):
    """백테스트 실행"""
    from backtester.engine import BacktestEngine
    from backtester.report import BacktestReport
    from strategies import STRATEGY_MAP
    from utils.helpers import load_strategies_config

    try:
        strategies_config = load_strategies_config()
        strategy_params = strategies_config.get("strategies", {}).get(strategy, {})

        if strategy not in STRATEGY_MAP:
            raise HTTPException(status_code=400, detail=f"알 수 없는 전략: {strategy}")

        strategy_cls = STRATEGY_MAP[strategy]
        strategy_obj = strategy_cls(params=strategy_params)

        settings = load_settings()
        initial_capital = settings.get("trading", {}).get("initial_capital", 10_000_000)

        engine = BacktestEngine(
            strategy=strategy_obj,
            initial_capital=initial_capital,
        )

        result = engine.run(symbol, start_date, end_date)
        summary = BacktestReport.generate_summary(result)
        equity_chart = BacktestReport.generate_equity_curve(result)

        return JSONResponse({
            "success": True,
            "summary": summary,
            "equity_chart": equity_chart,
            "result": result.to_dict(),
        })

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"백테스트 실행 실패: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/trading/start")
async def start_trading():
    """자동 매매 시작"""
    if _trading_engine is None:
        raise HTTPException(status_code=400, detail="트레이딩 엔진이 초기화되지 않았습니다.")
    if _trading_engine.is_running:
        return {"status": "already_running", "message": "이미 실행 중입니다."}
    _trading_engine.start_scheduled()
    return {"status": "started", "message": "자동 매매가 시작되었습니다."}


@app.post("/api/trading/stop")
async def stop_trading():
    """자동 매매 중지"""
    if _trading_engine is None:
        raise HTTPException(status_code=400, detail="트레이딩 엔진이 초기화되지 않았습니다.")
    _trading_engine.stop()
    return {"status": "stopped", "message": "자동 매매가 중지되었습니다."}


@app.get("/api/trading/status")
async def trading_status():
    """트레이딩 엔진 상태"""
    if _trading_engine is None:
        return {
            "is_running": False,
            "message": "트레이딩 엔진이 초기화되지 않았습니다.",
        }
    return _trading_engine.get_status()


# ─── 앱 시작 이벤트 ────────────────────────────────────────────


@app.on_event("startup")
async def startup_event():
    """앱 시작 시 DB 초기화"""
    init_db()
    logger.info("AutoTrader 웹 서버 시작")


# ─── 선물거래 라우트 ───────────────────────────────────────────────

# 인메모리 선물 포지션/거래 내역 저장소 (실거래 엔진 연결 전 임시)
_futures_positions: list = []
_futures_trade_history: list = []


class FuturesBacktestRequest(BaseModel):
    symbol: str
    start_date: str
    end_date: str
    leverage_mode: str = "dynamic"  # "dynamic" | "1" | "3" | "5" | "10"


@app.get("/futures", response_class=HTMLResponse)
async def futures_page(request: Request):
    """선물거래 대시보드 페이지"""
    settings = load_settings()
    portfolio_value = settings.get("trading", {}).get("initial_capital", 10_000_000)

    # 현재 포지션 데이터 준비
    positions_display = []
    total_margin = 0.0
    total_notional = 0.0
    total_unrealized_pnl = 0.0
    at_risk_positions = 0

    for pos in _futures_positions:
        try:
            from data.fetcher import DataFetcher
            fetcher = DataFetcher()
            current_price = fetcher.get_current_price(pos["symbol"])
            if current_price <= 0:
                current_price = pos["entry_price"]

            unrealized_pnl = pos.get("unrealized_pnl", 0.0)
            unrealized_pnl_pct = (unrealized_pnl / pos["margin"] * 100) if pos["margin"] > 0 else 0
            notional = pos.get("quantity", pos["margin"] * pos["leverage"])

            # 청산가까지 거리
            if pos["side"] == "long":
                dist_pct = (current_price - pos["liquidation_price"]) / current_price * 100
            else:
                dist_pct = (pos["liquidation_price"] - current_price) / current_price * 100

            entry = {**pos, "current_price": current_price,
                     "unrealized_pnl": unrealized_pnl,
                     "unrealized_pnl_pct": unrealized_pnl_pct,
                     "distance_to_liq_pct": dist_pct}
            positions_display.append(entry)

            total_margin += pos["margin"]
            total_notional += notional
            total_unrealized_pnl += unrealized_pnl
            if dist_pct < 20:
                at_risk_positions += 1
        except Exception as e:
            logger.error(f"포지션 데이터 처리 오류: {e}")

    margin_pct = (total_margin / portfolio_value * 100) if portfolio_value > 0 else 0
    total_unrealized_pnl_pct = (total_unrealized_pnl / total_margin * 100) if total_margin > 0 else 0

    # 워치리스트 설정 로드
    try:
        import yaml
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config", "futures_config.yaml"
        )
        with open(config_path, "r", encoding="utf-8") as f:
            futures_cfg = yaml.safe_load(f)
        watchlist = futures_cfg.get("futures", {}).get("watchlist", ["BTC-USD", "ETH-USD"])
        max_leverage = futures_cfg.get("futures", {}).get("max_leverage", 10)
        max_positions = futures_cfg.get("futures", {}).get("max_open_positions", 3)
    except Exception:
        watchlist = ["BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "XRP-USD",
                     "DOGE-USD", "ADA-USD", "AVAX-USD", "DOT-USD", "MATIC-USD"]
        max_leverage = 10
        max_positions = 3

    default_start = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    default_end = datetime.now().strftime("%Y-%m-%d")

    return templates.TemplateResponse(request=request, name="futures.html", context={
        "positions": positions_display,
        "positions_json": json.dumps(positions_display),
        "trade_history": _futures_trade_history[-50:],
        "total_margin": total_margin,
        "total_notional": total_notional,
        "total_unrealized_pnl": total_unrealized_pnl,
        "total_unrealized_pnl_pct": total_unrealized_pnl_pct,
        "margin_pct": margin_pct,
        "at_risk_positions": at_risk_positions,
        "max_leverage": max_leverage,
        "max_positions": max_positions,
        "watchlist": watchlist,
        "default_start": default_start,
        "default_end": default_end,
        "trading_active": _trading_engine.is_running if _trading_engine else False,
    })


@app.get("/api/futures/positions")
async def api_futures_positions():
    """현재 선물 포지션 JSON"""
    return JSONResponse({"positions": _futures_positions, "count": len(_futures_positions)})


@app.post("/api/futures/backtest")
async def api_futures_backtest(req: FuturesBacktestRequest):
    """선물 백테스트 실행 API"""
    try:
        from strategies.futures_strategy import FuturesStrategy
        from trader.leverage_manager import LeverageManager
        from backtester.futures_backtester import FuturesBacktester

        # 레버리지 모드 파싱
        fixed_leverage = None
        if req.leverage_mode != "dynamic":
            try:
                fixed_leverage = int(req.leverage_mode)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"잘못된 레버리지 모드: {req.leverage_mode}")

        settings = load_settings()
        initial_capital = settings.get("trading", {}).get("initial_capital", 10_000_000)

        strategy = FuturesStrategy()
        leverage_manager = LeverageManager()
        backtester = FuturesBacktester(
            strategy=strategy,
            leverage_manager=leverage_manager,
            initial_capital=initial_capital,
            fixed_leverage=fixed_leverage,
        )

        result = backtester.run(req.symbol, req.start_date, req.end_date)

        return JSONResponse({
            "success": True,
            "result": result.to_dict(),
            "equity_curve": result.equity_curve,
            "trades_log": result.trades_log[:100],  # 최대 100건
        })

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"선물 백테스트 오류: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/strategy-monitor/status")
async def api_monitor_status():
    """전략별 성과 모니터 상태 조회"""
    from strategies.performance_monitor import get_monitor
    monitor = get_monitor()
    return JSONResponse({"strategies": monitor.get_all_status()})


@app.post("/api/strategy-monitor/toggle")
async def api_monitor_toggle(strategy: str, enabled: bool):
    """전략 수동 활성화/비활성화"""
    from strategies.performance_monitor import get_monitor
    monitor = get_monitor()
    monitor.set_enabled(strategy, enabled)
    return JSONResponse({
        "strategy": strategy,
        "enabled": enabled,
        "message": f"전략 {'활성화' if enabled else '비활성화'} 완료: {strategy}",
    })


@app.get("/api/monthly-returns")
async def api_monthly_returns(year: int = None, month: int = None):
    """월간 수익률 분석"""
    from datetime import date

    now = datetime.now()
    target_year = year or now.year
    target_month = month or now.month

    session = get_session()
    try:
        start = datetime(target_year, target_month, 1)
        if target_month == 12:
            end = datetime(target_year + 1, 1, 1)
        else:
            end = datetime(target_year, target_month + 1, 1)

        trades = (
            session.query(Trade)
            .filter(Trade.timestamp >= start, Trade.timestamp < end)
            .order_by(Trade.timestamp.asc())
            .all()
        )
        trades_data = [t.to_dict() for t in trades]

        total_pnl = sum(t.pnl or 0 for t in trades)
        win_trades = sum(1 for t in trades if (t.pnl or 0) > 0)
        loss_trades = sum(1 for t in trades if (t.pnl or 0) < 0)
        total_closed = win_trades + loss_trades
        win_rate = win_trades / total_closed if total_closed > 0 else None

        # 전략별 집계
        by_strategy: dict = {}
        for t in trades:
            name = t.strategy or "unknown"
            if name not in by_strategy:
                by_strategy[name] = {"pnl": 0.0, "trades": 0, "wins": 0}
            by_strategy[name]["pnl"] += t.pnl or 0
            by_strategy[name]["trades"] += 1
            if (t.pnl or 0) > 0:
                by_strategy[name]["wins"] += 1

        return JSONResponse({
            "period": f"{target_year}-{target_month:02d}",
            "total_trades": len(trades),
            "total_pnl": total_pnl,
            "win_trades": win_trades,
            "loss_trades": loss_trades,
            "win_rate": win_rate,
            "win_rate_pct": f"{win_rate*100:.1f}%" if win_rate is not None else "N/A",
            "by_strategy": by_strategy,
            "trades": trades_data,
        })
    finally:
        session.close()


@app.get("/api/macro-sentiment")
async def api_macro_sentiment():
    """현재 매크로 + 뉴스 감성 지표 반환 (실거래 대시보드용)"""
    try:
        from data.macro_fetcher import MacroDataFetcher
        from strategies.macro_filter import MacroFilter, score_to_label

        fetcher = MacroDataFetcher()
        filt    = MacroFilter()
        today   = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        fg_df  = fetcher.get_fear_greed(yesterday, today)
        vix_df = fetcher.get_vix(yesterday, today)

        fg_val   = float(fg_df["fg_value"].iloc[-1])  if not fg_df.empty  else 50
        fg_class = str(fg_df["fg_class"].iloc[-1])    if not fg_df.empty  else "N/A"
        vix_val  = float(vix_df["vix_close"].iloc[-1]) if not vix_df.empty else 20.0

        msig = filt.evaluate(fg_val, vix_val, 0.0, today)

        # 뉴스 감성 (BTC + 전체 시장)
        news_result = {}
        try:
            from data.news_fetcher import NewsFetcher
            nf = NewsFetcher()
            news_result = nf.get_market_sentiment()
        except Exception:
            news_result = {"score": 0, "reasoning": "뉴스 API 미설정"}

        return JSONResponse({
            "date": today,
            "fear_greed": {
                "value": fg_val,
                "label": fg_class,
                "interpretation": (
                    "매수 기회 (역발상)" if fg_val < 20
                    else "공포 → 롱 유리" if fg_val < 35
                    else "중립" if fg_val < 65
                    else "탐욕 → 주의" if fg_val < 80
                    else "과열 → 롱 위험"
                ),
            },
            "vix": {
                "value": round(vix_val, 2),
                "level": (
                    "정상" if vix_val < 20
                    else "상승" if vix_val < 25
                    else "고공포" if vix_val < 35
                    else "극단공포"
                ),
            },
            "macro_filter": {
                "allow_long":  msig.allow_long,
                "allow_short": msig.allow_short,
                "position_modifier": round(msig.position_modifier, 2),
                "reason": msig.reason,
                "score_label": score_to_label(msig.macro_score),
            },
            "news_sentiment": {
                "score": news_result.get("score", 0),
                "reasoning": news_result.get("reasoning", ""),
                "bullish": news_result.get("bullish", [])[:3],
                "bearish": news_result.get("bearish", [])[:3],
            },
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


def _generate_pnl_chart() -> str:
    """간단한 P&L 차트 생성 (거래 내역 기반)"""
    try:
        import plotly.graph_objects as go
        import plotly.utils

        session = get_session()
        trades = (
            session.query(Trade)
            .order_by(Trade.timestamp.asc())
            .limit(200)
            .all()
        )
        session.close()

        if not trades:
            return "{}"

        dates = [t.timestamp.strftime("%Y-%m-%d") if t.timestamp else "" for t in trades]
        cumulative_pnl = []
        running_pnl = 0
        for t in trades:
            running_pnl += (t.pnl or 0)
            cumulative_pnl.append(running_pnl)

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=dates,
            y=cumulative_pnl,
            mode="lines+markers",
            name="누적 손익",
            line=dict(color="#00d4ff", width=2),
            marker=dict(size=4),
        ))
        fig.add_hline(y=0, line_dash="dash", line_color="rgba(255,255,255,0.3)")
        fig.update_layout(
            title="누적 손익 (₩)",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#e0e0e0"),
            xaxis=dict(gridcolor="rgba(255,255,255,0.1)"),
            yaxis=dict(gridcolor="rgba(255,255,255,0.1)", tickformat=",.0f"),
            margin=dict(l=10, r=10, t=40, b=10),
            showlegend=False,
        )
        return json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)
    except Exception as e:
        logger.error(f"P&L 차트 생성 실패: {e}")
        return "{}"

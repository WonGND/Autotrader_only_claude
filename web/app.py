"""
FastAPI 웹 애플리케이션

대시보드, 백테스트, 전략, 거래 내역 등의 웹 UI를 제공합니다.
"""

import os
import json
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

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
            "request": request,
            "total_value": account.total_value,
            "cash": account.cash,
            "profit_loss": account.profit_loss,
            "profit_loss_pct": account.profit_loss_pct * 100,
            "num_positions": len(positions),
            "positions": positions_list,
            "recent_trades": trades_list,
            "pnl_chart_json": pnl_chart_json,
            "trading_active": _trading_engine.is_running if _trading_engine else False,
            "format_currency": format_currency,
        }
    except Exception as e:
        logger.error(f"대시보드 오류: {e}")
        context = {
            "request": request,
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

    return templates.TemplateResponse("dashboard.html", context)


@app.get("/strategies", response_class=HTMLResponse)
async def strategies_page(request: Request):
    """전략 목록 페이지"""
    strategies_config = load_strategies_config()
    settings = load_settings()
    active = strategies_config.get("active_strategy", "ma_crossover")

    return templates.TemplateResponse("strategies.html", {
        "request": request,
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

    return templates.TemplateResponse("backtest.html", {
        "request": request,
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

    return templates.TemplateResponse("trades.html", {
        "request": request,
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

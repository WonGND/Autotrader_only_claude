"""
SQLAlchemy 데이터베이스 모델

SQLite를 백엔드로 사용하여 거래 내역, 백테스트 결과 등을 저장합니다.
"""

import os
from datetime import datetime

from sqlalchemy import (
    create_engine, Column, Integer, Float, String,
    DateTime, Text, Boolean
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session

# DB 파일 경로
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "autotrader.db")
DB_URL = f"sqlite:///{DB_PATH}"


class Base(DeclarativeBase):
    pass


class Trade(Base):
    """거래 내역 테이블"""
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(50), unique=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    side = Column(String(10), nullable=False)          # "buy" or "sell"
    quantity = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    filled_price = Column(Float, nullable=True)
    commission = Column(Float, default=0.0)
    pnl = Column(Float, default=0.0)                   # 실현 손익
    strategy = Column(String(50), nullable=True)
    timestamp = Column(DateTime, default=datetime.now, index=True)
    note = Column(Text, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "price": self.price,
            "filled_price": self.filled_price,
            "commission": self.commission,
            "pnl": self.pnl,
            "strategy": self.strategy,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "note": self.note,
        }


class Position(Base):
    """현재 포지션 테이블"""
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, unique=True, index=True)
    quantity = Column(Integer, nullable=False)
    avg_price = Column(Float, nullable=False)
    current_price = Column(Float, default=0.0)
    unrealized_pnl = Column(Float, default=0.0)
    unrealized_pnl_pct = Column(Float, default=0.0)
    strategy = Column(String(50), nullable=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "quantity": self.quantity,
            "avg_price": self.avg_price,
            "current_price": self.current_price,
            "unrealized_pnl": self.unrealized_pnl,
            "unrealized_pnl_pct": self.unrealized_pnl_pct,
            "strategy": self.strategy,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class BacktestResult(Base):
    """백테스트 결과 테이블"""
    __tablename__ = "backtest_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy = Column(String(50), nullable=False, index=True)
    symbol = Column(String(20), nullable=False)
    start_date = Column(String(20), nullable=False)
    end_date = Column(String(20), nullable=False)
    initial_capital = Column(Float, nullable=False)
    final_capital = Column(Float, nullable=False)
    total_return = Column(Float, nullable=False)        # 소수 (예: 0.15 = 15%)
    max_drawdown = Column(Float, nullable=False)        # 소수 (예: -0.10 = -10%)
    sharpe_ratio = Column(Float, nullable=True)
    win_rate = Column(Float, nullable=True)             # 소수
    total_trades = Column(Integer, default=0)
    winning_trades = Column(Integer, default=0)
    losing_trades = Column(Integer, default=0)
    equity_curve_json = Column(Text, nullable=True)    # JSON 직렬화된 equity curve
    created_at = Column(DateTime, default=datetime.now)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "strategy": self.strategy,
            "symbol": self.symbol,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "initial_capital": self.initial_capital,
            "final_capital": self.final_capital,
            "total_return": self.total_return,
            "max_drawdown": self.max_drawdown,
            "sharpe_ratio": self.sharpe_ratio,
            "win_rate": self.win_rate,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class AppConfig(Base):
    """앱 설정 테이블 (런타임 설정 저장)"""
    __tablename__ = "app_config"

    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "value": self.value,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# Engine & Session factory
_engine = None
_SessionFactory = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
    return _engine


def init_db():
    """데이터베이스 초기화 (테이블 생성)"""
    engine = get_engine()
    Base.metadata.create_all(engine)
    return engine


def get_session() -> Session:
    """새 DB 세션 반환"""
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine())
    return _SessionFactory()

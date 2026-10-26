
from sqlalchemy import (
    BigInteger, String, Text, Date, DateTime, Numeric,
    Integer, Boolean, ForeignKey, UniqueConstraint,
    Index, text, Column, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.mysql import BIGINT
from app.db.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    username: Mapped[str] = mapped_column(String(50), unique=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str | None] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(
        String(20), default="user", server_default="user"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("1")
    )
    created_at: Mapped[object] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[object] = mapped_column(
        DateTime,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )
    last_login_at: Mapped[object | None] = mapped_column(DateTime)


class Stock(Base):
    __tablename__ = "stocks"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    symbol: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    exchange: Mapped[str] = mapped_column(String(10))
    industry: Mapped[str | None] = mapped_column(String(150))
    company_type: Mapped[str | None] = mapped_column(String(50))
    reference_price = Column(Numeric(15, 2), nullable=True)
    current_price = Column(Numeric(15, 2), nullable=True)
    change_percent = Column(Numeric(10, 4), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default="active", server_default="active"
    )
    listed_date: Mapped[object | None] = mapped_column(Date)
    website: Mapped[str | None] = mapped_column(String(255))
    url_logo: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    icb_code1: Mapped[str | None] = mapped_column(String(10))
    icb_name1: Mapped[str | None] = mapped_column(String(150))
    icb_code2: Mapped[str | None] = mapped_column(String(10))
    icb_name2: Mapped[str | None] = mapped_column(String(150))
    icb_code3: Mapped[str | None] = mapped_column(String(10))
    icb_name3: Mapped[str | None] = mapped_column(String(150))
    icb_code4: Mapped[str | None] = mapped_column(String(10))
    icb_name4: Mapped[str | None] = mapped_column(String(150))
    created_at: Mapped[object] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[object] = mapped_column(
        DateTime,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )


class StockPrice(Base):
    __tablename__ = "stock_prices"
    __table_args__ = (
        UniqueConstraint(
            "stock_id", "trading_date",
            name="uq_stock_prices_stock_date"
        ),
        Index("idx_stock_prices_date", "trading_date"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    stock_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("stocks.id", ondelete="CASCADE")
    )
    trading_date: Mapped[object] = mapped_column(Date)
    open_price: Mapped[object | None] = mapped_column(Numeric(18, 4))
    high_price: Mapped[object | None] = mapped_column(Numeric(18, 4))
    low_price: Mapped[object | None] = mapped_column(Numeric(18, 4))
    close_price: Mapped[object] = mapped_column(Numeric(18, 4))
    adjusted_close: Mapped[object | None] = mapped_column(Numeric(18, 4))
    volume: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), default=0, server_default="0"
    )
    value_traded: Mapped[object | None] = mapped_column(Numeric(22, 2))
    created_at: Mapped[object] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )


class StockFinancial(Base):
    __tablename__ = "stock_financials"
    __table_args__ = (
        UniqueConstraint(
            "stock_id", "period_end", "period_type",
            name="uq_stock_financials_period"
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    stock_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("stocks.id", ondelete="CASCADE")
    )
    period_end: Mapped[object] = mapped_column(Date)
    period_type: Mapped[str] = mapped_column(String(10))
    fiscal_quarter: Mapped[object | None] = mapped_column(Numeric(22, 2))
    fiscal_quarter_txt: Mapped[str] = mapped_column(String(100))
    revenue: Mapped[object | None] = mapped_column(Numeric(22, 2))
    operating_profit: Mapped[object | None] = mapped_column(Numeric(22, 2))
    net_profit: Mapped[object | None] = mapped_column(Numeric(22, 2))
    equity: Mapped[object | None] = mapped_column(Numeric(22, 2))
    total_assets: Mapped[object | None] = mapped_column(Numeric(22, 2))
    total_debt: Mapped[object | None] = mapped_column(Numeric(22, 2))
    eps: Mapped[object | None] = mapped_column(Numeric(18, 4))
    roe: Mapped[object | None] = mapped_column(Numeric(10, 4))
    roa: Mapped[object | None] = mapped_column(Numeric(10, 4))
    roic: Mapped[object | None] = mapped_column(Numeric(10, 4))
    source: Mapped[str | None] = mapped_column(String(50))
    updated_at: Mapped[object] = mapped_column(
        DateTime,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )


class StockValuation(Base):
    __tablename__ = "stock_valuations"
    __table_args__ = (
        UniqueConstraint(
            "stock_id", "valuation_date",
            name="uq_stock_valuations_date"
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    stock_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("stocks.id", ondelete="CASCADE")
    )
    valuation_date: Mapped[object] = mapped_column(Date)
    period_type: Mapped[str] = mapped_column(String(10))
    fiscal_quarter: Mapped[object | None] = mapped_column(Numeric(22, 2))
    fiscal_quarter_txt: Mapped[str] = mapped_column(String(100))
    market_cap: Mapped[object | None] = mapped_column(Numeric(22, 2))
    pe: Mapped[object | None] = mapped_column(Numeric(12, 4))
    pb: Mapped[object | None] = mapped_column(Numeric(12, 4))
    ps: Mapped[object | None] = mapped_column(Numeric(12, 4))
    source: Mapped[str | None] = mapped_column(String(50))
    updated_at: Mapped[object] = mapped_column(
        DateTime,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )


class SyncLog(Base):
    __tablename__ = "sync_logs"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    task_name: Mapped[str] = mapped_column(String(100))
    symbol: Mapped[str | None] = mapped_column(String(20))
    started_at: Mapped[object] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )
    finished_at: Mapped[object | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(
        String(20), default="running", server_default="running"
    )
    records_affected: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    error_message: Mapped[str | None] = mapped_column(Text)

class Watchlist(Base):
    __tablename__ = "watchlists"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    stock_id = Column(
        Integer,
        ForeignKey("stocks.id", ondelete="CASCADE"),
        nullable=False,
    )
    note = Column(String(255), nullable=True)
    created_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "stock_id",
            name="uq_watchlist_user_stock",
        ),
    )

    user = relationship("User")
    stock = relationship("Stock")



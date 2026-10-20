from datetime import date, datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select, func, desc, text
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import (
    User, Stock, StockPrice, StockFinancial,
    StockValuation, SyncLog, Watchlist,
)
from app.schemas.schemas import RegisterRequest, StockCreate, WatchlistCreate
from app.core.security import (
    password_hash, create_access_token, get_current_user, require_admin,
    JWT_EXPIRE_MINUTES,
)
from app.utils.serializers import to_dict

router = APIRouter()
@router.get("/api/stocks/{symbol}/financials")
def financials(
    symbol: str,
    period_type: str | None = None,
    limit: int = Query(12, ge=1, le=100),
    db: Session = Depends(get_db),
):
    stock = db.scalar(
        select(Stock).where(Stock.symbol == symbol.upper())
    )
    if not stock:
        raise HTTPException(404, "Không tìm thấy cổ phiếu")

    stmt = select(StockFinancial).where(
        StockFinancial.stock_id == stock.id
    )

    if period_type:
        stmt = stmt.where(
            StockFinancial.period_type == period_type.upper()
        )

    rows = db.scalars(
        stmt.order_by(desc(StockFinancial.period_end))
        .limit(limit)
    ).all()

    return {
        "symbol": stock.symbol,
        "items": [
            to_dict(
                row,
                ["period_end", "period_type", "revenue",
                 "operating_profit", "net_profit", "equity",
                 "total_assets", "total_debt", "eps",
                 "roe", "roa", "roic", "source"]
            )
            for row in rows
        ],
    }

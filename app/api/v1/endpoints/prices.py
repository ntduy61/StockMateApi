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
@router.get("/api/stocks/{symbol}/price/latest")
def latest_price(symbol: str, db: Session = Depends(get_db)):
    stock = db.scalar(
        select(Stock).where(Stock.symbol == symbol.upper())
    )
    if not stock:
        raise HTTPException(404, "Không tìm thấy cổ phiếu")

    price = db.scalar(
        select(StockPrice)
        .where(StockPrice.stock_id == stock.id)
        .order_by(desc(StockPrice.trading_date))
        .limit(1)
    )

    if not price:
        raise HTTPException(404, "Chưa có dữ liệu giá")

    return {
        "symbol": stock.symbol,
        "name": stock.name,
        **to_dict(
            price,
            ["trading_date", "open_price", "high_price",
             "low_price", "close_price", "adjusted_close",
             "volume", "value_traded"]
        ),
    }


@router.get("/api/stocks/{symbol}/prices")
def price_history(
    symbol: str,
    from_date: date | None = None,
    to_date: date | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    stock = db.scalar(
        select(Stock).where(Stock.symbol == symbol.upper())
    )
    if not stock:
        raise HTTPException(404, "Không tìm thấy cổ phiếu")

    if from_date and to_date and from_date > to_date:
        raise HTTPException(400, "from_date phải nhỏ hơn to_date")

    stmt = select(StockPrice).where(
        StockPrice.stock_id == stock.id
    )

    if from_date:
        stmt = stmt.where(StockPrice.trading_date >= from_date)
    if to_date:
        stmt = stmt.where(StockPrice.trading_date <= to_date)

    total = db.scalar(
        select(func.count()).select_from(stmt.subquery())
    ) or 0

    rows = db.scalars(
        stmt.order_by(desc(StockPrice.trading_date))
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    return {
        "symbol": stock.symbol,
        "items": [
            to_dict(
                row,
                ["trading_date", "open_price", "high_price",
                 "low_price", "close_price", "adjusted_close",
                 "volume", "value_traded"]
            )
            for row in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }

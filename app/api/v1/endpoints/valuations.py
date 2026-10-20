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
@router.get("/api/stocks/{symbol}/valuation/latest")
def latest_valuation(
    symbol: str,
    db: Session = Depends(get_db),
):
    stock = db.scalar(
        select(Stock).where(Stock.symbol == symbol.upper())
    )
    if not stock:
        raise HTTPException(404, "Không tìm thấy cổ phiếu")

    row = db.scalar(
        select(StockValuation)
        .where(StockValuation.stock_id == stock.id)
        .order_by(desc(StockValuation.valuation_date))
        .limit(1)
    )

    if not row:
        raise HTTPException(404, "Chưa có dữ liệu định giá")

    return {
        "symbol": stock.symbol,
        **to_dict(
            row,
            ["valuation_date", "market_cap", "pe",
             "pb", "ps", "source"]
        ),
    }


@router.get("/api/stocks/{symbol}/valuation")
def symbol_valuation(
    symbol: str,
    db: Session = Depends(get_db),
):
    stock = db.scalar(
        select(Stock).where(Stock.symbol == symbol.upper())
    )

    if not stock:
        raise HTTPException(404, "Không tìm thấy cổ phiếu")

    rows = db.scalars(
        select(StockValuation)
        .where(StockValuation.stock_id == stock.id)
        .order_by(StockValuation.valuation_date.asc())
    ).all()

    if not rows:
        raise HTTPException(404, "Chưa có dữ liệu định giá")

    return {
        "symbol": stock.symbol,
        "total": len(rows),
        "items": [
            to_dict(
                row,
                [
                    "valuation_date",
                    "market_cap",
                    "pe",
                    "pb",
                    "ps",
                    "source",
                ],
            )
            for row in rows
        ],
    }

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
@router.get("/api/watchlist")
def get_watchlist(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = (
        select(Watchlist, Stock)
        .join(Stock, Watchlist.stock_id == Stock.id)
        .where(Watchlist.user_id == user.id)
        .order_by(desc(Watchlist.created_at))
    )

    rows = db.execute(stmt).all()

    return {
        "total": len(rows),
        "items": [
            {
                "id": item.id,
                "symbol": stock.symbol,
                "name": stock.name,
                "exchange": stock.exchange,
                "note": item.note,
                "created_at": item.created_at,
            }
            for item, stock in rows
        ],
    }


@router.post("/api/watchlist", status_code=201)
def add_watchlist(
    data: WatchlistCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    symbol = data.symbol.strip().upper()

    stock = db.scalar(
        select(Stock).where(Stock.symbol == symbol)
    )

    if stock is None:
        raise HTTPException(
            status_code=404,
            detail=f"Không tìm thấy mã cổ phiếu {symbol}",
        )

    existing = db.scalar(
        select(Watchlist).where(
            Watchlist.user_id == user.id,
            Watchlist.stock_id == stock.id,
        )
    )

    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Mã {symbol} đã có trong watchlist",
        )

    item = Watchlist(
        user_id=user.id,
        stock_id=stock.id,
        note=data.note,
    )

    db.add(item)
    db.commit()
    db.refresh(item)

    return {
        "success": True,
        "message": f"Đã thêm {symbol} vào watchlist",
        "data": {
            "id": item.id,
            "symbol": stock.symbol,
            "name": stock.name,
            "exchange": stock.exchange,
            "note": item.note,
            "created_at": item.created_at,
        },
    }


@router.delete("/api/watchlist/{watchlist_id}")
def delete_watchlist(
    watchlist_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = db.scalar(
        select(Watchlist).where(
            Watchlist.id == watchlist_id,
            Watchlist.user_id == user.id,
        )
    )

    if item is None:
        raise HTTPException(
            status_code=404,
            detail="Không tìm thấy mã trong watchlist",
        )

    db.delete(item)
    db.commit()

    return {
        "success": True,
        "message": "Đã xóa mã khỏi watchlist",
    }


@router.get("/api/watchlist/check/{symbol}")
def check_watchlist(
    symbol: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    symbol = symbol.strip().upper()

    stmt = (
        select(Watchlist.id)
        .join(Stock, Watchlist.stock_id == Stock.id)
        .where(
            Watchlist.user_id == user.id,
            Stock.symbol == symbol,
        )
    )

    is_watched = db.scalar(stmt) is not None

    return {
        "symbol": symbol,
        "is_watched": is_watched,
    }

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import (User, Stock)
from app.schemas.schemas import StockCreate
from app.core.security import (require_admin)

from app.utils.serializers import to_dict

router = APIRouter()
@router.get("/api/stocks")
def get_stocks(
    exchange: str | None = None,
    q: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    stmt = select(Stock)

    if exchange:
        stmt = stmt.where(Stock.exchange == exchange.upper())

    if q:
        keyword = q.strip()
        stmt = stmt.where(
            (Stock.symbol.like(f"%{keyword}%"))
            | (Stock.name.like(f"%{keyword}%"))
        )

    # total = db.scalar(
    #     select(func.count()).select_from(stmt.subquery())
    # ) or 0
    total = db.execute(
        text("""
            SELECT COUNT(*)
            FROM stocks
            WHERE (:exchange IS NULL OR exchange = :exchange)
        """),
        {"exchange": exchange.upper() if exchange else None}
    ).scalar() or 0

    items = db.scalars(
        stmt.order_by(Stock.symbol)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    return {
        "items": [
            to_dict(
                stock,
                ["id", "symbol", "name", "exchange",
                 "industry", "status", "reference_price", 
                 "current_price", "change_percent", "icb_code1", "icb_name1", "icb_code2", 
                 "icb_name2", "icb_code3", "icb_name3", "icb_code4", "icb_name4"]
            )
            for stock in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/api/stocks/{symbol}")
def get_stock(symbol: str, db: Session = Depends(get_db)):
    stock = db.scalar(
        select(Stock).where(Stock.symbol == symbol.upper())
    )

    if not stock:
        raise HTTPException(404, "Không tìm thấy cổ phiếu")
    return to_dict(
        stock,
        ["id", "symbol", "name", "exchange", "industry",
         "company_type", "status", "listed_date", "website",
         "description", "reference_price", "current_price", "change_percent", "icb_code1", "icb_name1", "icb_code2", 
                 "icb_name2", "icb_code3", "icb_name3", "icb_code4", "icb_name4"]
    )


@router.post("/api/stocks", status_code=201)
def create_stock(
    data: StockCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    symbol = data.symbol.strip().upper()

    if db.scalar(select(Stock.id).where(Stock.symbol == symbol)):
        raise HTTPException(409, "Mã cổ phiếu đã tồn tại")

    stock = Stock(
        symbol=symbol,
        name=data.name.strip(),
        exchange=data.exchange.strip().upper(),
        industry=data.industry,
    )
    db.add(stock)
    db.commit()
    db.refresh(stock)

    return to_dict(
        stock, ["id", "symbol", "name", "exchange", "industry"]
    )
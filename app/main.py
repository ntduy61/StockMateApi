
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import jwt
from pwdlib import PasswordHash
from fastapi import (
    FastAPI, Depends, HTTPException, Query, status
)
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select, func, desc, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    User, Stock, StockPrice, StockFinancial,
    StockValuation, SyncLog, Watchlist
)

app = FastAPI(
    title="StockMate API",
    version="1.0.0",
    description="API dữ liệu cổ phiếu Việt Nam",
)

password_hash = PasswordHash.recommended()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "1440"))

if not JWT_SECRET_KEY:
    raise RuntimeError("Chưa cấu hình JWT_SECRET_KEY trong .env")


# -----------------------------------------------------
# Helpers
# -----------------------------------------------------

def to_dict(obj, fields):
    return {
        field: getattr(obj, field)
        for field in fields
    }


def create_access_token(user: User):
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "iat": now,
        "exp": now + timedelta(minutes=JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token không hợp lệ hoặc đã hết hạn",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(
            token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM]
        )
        user_id = int(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError, TypeError):
        raise unauthorized

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise unauthorized

    return user


def require_admin(user: User = Depends(get_current_user)):
    if user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Bạn không có quyền thực hiện thao tác này",
        )
    return user


# -----------------------------------------------------
# Request schemas
# -----------------------------------------------------

class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=100)


class StockCreate(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=255)
    exchange: str = Field(min_length=1, max_length=10)
    industry: str | None = None


# -----------------------------------------------------
# Health check
# -----------------------------------------------------

@app.get("/")
def home():
    return {"app": "StockMate API", "version": "1.0.0"}


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    db.execute(select(1))
    return {
        "status": "healthy",
        "database": "connected",
        "time": datetime.now(timezone.utc),
    }

@app.get("/api/health/db")
def check_database(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {
            "status": "success",
            "database": "connected",
            "message": "Kết nối MariaDB thành công"
        }
    except Exception as e:
        return {
            "status": "error",
            "database": "disconnected",
            "message": str(e)
        }

# -----------------------------------------------------
# Authentication
# -----------------------------------------------------

@app.post("/api/auth/register", status_code=201)
def register(
    data: RegisterRequest,
    db: Session = Depends(get_db),
):
    username = data.username.strip()
    email = str(data.email).strip().lower()

    existing = db.scalar(
        select(User).where(
            (User.username == username) | (User.email == email)
        )
    )

    if existing:
        raise HTTPException(
            status_code=409,
            detail="Username hoặc email đã tồn tại",
        )

    user = User(
        username=username,
        email=email,
        password_hash=password_hash.hash(data.password),
        full_name=data.full_name,
        role="user",
        is_active=True,
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "role": user.role,
    }


@app.post("/api/auth/login")
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    user = db.scalar(
        select(User).where(User.username == form.username)
    )

    if (
        user is None
        or not user.is_active
        or not password_hash.verify(form.password, user.password_hash)
    ):
        raise HTTPException(
            status_code=401,
            detail="Sai tài khoản hoặc mật khẩu",
        )

    user.last_login_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()

    return {
        "access_token": create_access_token(user),
        "token_type": "bearer",
        "expires_in": JWT_EXPIRE_MINUTES * 60,
    }


@app.get("/api/auth/me")
def me(user: User = Depends(get_current_user)):
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "is_active": user.is_active,
    }


# -----------------------------------------------------
# Stocks
# -----------------------------------------------------

@app.get("/api/stocks")
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

    total = db.scalar(
        select(func.count()).select_from(stmt.subquery())
    ) or 0

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
                 "industry", "status", "reference_price", "current_price", "change_percent"]
            )
            for stock in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.get("/api/stocks/{symbol}")
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
         "description", "reference_price", "current_price", "change_percent"]
    )


@app.post("/api/stocks", status_code=201)
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


# -----------------------------------------------------
# Latest prices and price history
# -----------------------------------------------------

@app.get("/api/stocks/{symbol}/price/latest")
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


@app.get("/api/stocks/{symbol}/prices")
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


# -----------------------------------------------------
# Financial indicators
# -----------------------------------------------------

@app.get("/api/stocks/{symbol}/financials")
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


# -----------------------------------------------------
# Valuation
# -----------------------------------------------------

@app.get("/api/stocks/{symbol}/valuation/latest")
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
@app.get("/api/stocks/{symbol}/valuation")
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

# -----------------------------------------------------
# Sync logs - admin only
# -----------------------------------------------------

@app.get("/api/sync-logs")
def get_sync_logs(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    rows = db.scalars(
        select(SyncLog)
        .order_by(desc(SyncLog.id))
        .limit(limit)
    ).all()

    return [
        to_dict(
            row,
            ["id", "task_name", "symbol", "started_at",
             "finished_at", "status", "records_affected",
             "error_message"]
        )
        for row in rows
    ]



# -----------------------------------------------------
# Watchlist
# -----------------------------------------------------

class WatchlistCreate(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    note: str | None = Field(default=None, max_length=255)


@app.get("/api/watchlist")
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


@app.post("/api/watchlist", status_code=201)
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


@app.delete("/api/watchlist/{watchlist_id}")
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


@app.get("/api/watchlist/check/{symbol}")
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

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
@router.get("/api/health")
def health(db: Session = Depends(get_db)):
    db.execute(select(1))
    return {
        "status": "healthy",
        "database": "connected",
        "time": datetime.now(timezone.utc),
    }


@router.get("/api/health/db")
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
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
@router.get("/api/sync-logs")
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

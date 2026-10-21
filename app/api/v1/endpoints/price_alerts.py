
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import User
from app.core.security import get_current_user
from app.schemas.schemas import PriceAlertCreate, PriceAlertUpdate

router = APIRouter()





def alert_dict(row):
    return {
        "id": row["id"],
        "symbol": row["symbol"],
        "target_price": float(row["target_price"]),
        "is_enabled": bool(row["is_enabled"]),
        "is_triggered": bool(row["is_triggered"]),
        "last_checked_price": (
            float(row["last_checked_price"])
            if row["last_checked_price"] is not None else None
        ),
        "triggered_at": str(row["triggered_at"])
        if row["triggered_at"] else None,
        "created_at": str(row["created_at"])
        if row["created_at"] else None,
    }


@router.get("/api/price-alerts")
def get_price_alerts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = db.execute(
        text("""
            SELECT id, symbol, target_price, is_enabled,
                   is_triggered, last_checked_price,
                   triggered_at, created_at
            FROM price_alerts
            WHERE user_id = :user_id
            ORDER BY created_at DESC
        """),
        {"user_id": current_user.id},
    ).mappings().all()

    return {"items": [alert_dict(row) for row in rows]}


@router.post("/api/price-alerts", status_code=201)
def create_price_alert(
    data: PriceAlertCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    symbol = data.symbol.strip().upper()

    stock_exists = db.execute(
        text("SELECT id FROM stocks WHERE symbol = :symbol LIMIT 1"),
        {"symbol": symbol},
    ).first()

    if not stock_exists:
        raise HTTPException(404, "Không tìm thấy cổ phiếu")

    existing = db.execute(
        text("""
            SELECT id FROM price_alerts
            WHERE user_id = :user_id AND symbol = :symbol
        """),
        {"user_id": current_user.id, "symbol": symbol},
    ).first()

    if existing:
        raise HTTPException(409, "Bạn đã tạo cảnh báo cho mã này")

    result = db.execute(
        text("""
            INSERT INTO price_alerts
                (user_id, symbol, target_price, is_enabled,
                 is_triggered, created_at, updated_at)
            VALUES
                (:user_id, :symbol, :target_price, 1,
                 0, NOW(), NOW())
        """),
        {
            "user_id": current_user.id,
            "symbol": symbol,
            "target_price": data.target_price,
        },
    )
    db.commit()

    row = db.execute(
        text("""
            SELECT id, symbol, target_price, is_enabled,
                   is_triggered, last_checked_price,
                   triggered_at, created_at
            FROM price_alerts WHERE id = :id
        """),
        {"id": result.lastrowid},
    ).mappings().one()

    return alert_dict(row)


@router.put("/api/price-alerts/{alert_id}")
def update_price_alert(
    alert_id: int,
    data: PriceAlertUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    values = data.model_dump(exclude_unset=True)

    if not values:
        raise HTTPException(400, "Không có dữ liệu cần cập nhật")

    if values.get("target_price") is None and "target_price" in values:
        raise HTTPException(400, "target_price không được để trống")

    existing = db.execute(
        text("""
            SELECT id FROM price_alerts
            WHERE id = :id AND user_id = :user_id
        """),
        {"id": alert_id, "user_id": current_user.id},
    ).first()

    if not existing:
        raise HTTPException(404, "Không tìm thấy cảnh báo")

    updates = []
    params = {"id": alert_id, "user_id": current_user.id}

    if "target_price" in values:
        updates.append("target_price = :target_price")
        params["target_price"] = values["target_price"]

        # Cho phép cảnh báo được kích hoạt lại theo giá mục tiêu mới.
        updates.append("is_triggered = 0")
        updates.append("triggered_at = NULL")

    if "is_enabled" in values:
        updates.append("is_enabled = :is_enabled")
        params["is_enabled"] = values["is_enabled"]

        if values["is_enabled"]:
            updates.append("is_triggered = 0")
            updates.append("triggered_at = NULL")

    updates.append("updated_at = NOW()")

    db.execute(
        text(f"""
            UPDATE price_alerts
            SET {", ".join(updates)}
            WHERE id = :id AND user_id = :user_id
        """),
        params,
    )
    db.commit()

    row = db.execute(
        text("""
            SELECT id, symbol, target_price, is_enabled,
                   is_triggered, last_checked_price,
                   triggered_at, created_at
            FROM price_alerts
            WHERE id = :id AND user_id = :user_id
        """),
        {"id": alert_id, "user_id": current_user.id},
    ).mappings().one()

    return alert_dict(row)


@router.delete("/api/price-alerts/{alert_id}")
def delete_price_alert(
    alert_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = db.execute(
        text("""
            DELETE FROM price_alerts
            WHERE id = :id AND user_id = :user_id
        """),
        {"id": alert_id, "user_id": current_user.id},
    )
    db.commit()

    if result.rowcount == 0:
        raise HTTPException(404, "Không tìm thấy cảnh báo")

    return {"message": "Đã xóa cảnh báo"}


from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import User
from app.core.security import get_current_user

router = APIRouter()


def notification_dict(row):
    return {
        "id": row["id"],
        "alert_id": row["alert_id"],
        "symbol": row["symbol"],
        "title": row["title"],
        "message": row["message"],
        "current_price": (
            float(row["current_price"])
            if row["current_price"] is not None else None
        ),
        "target_price": (
            float(row["target_price"])
            if row["target_price"] is not None else None
        ),
        "is_read": bool(row["is_read"]),
        "created_at": str(row["created_at"])
        if row["created_at"] else None,
    }


@router.get("/api/notifications")
def get_notifications(
    unread_only: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conditions = ["user_id = :user_id"]
    params = {
        "user_id": current_user.id,
        "offset": (page - 1) * page_size,
        "page_size": page_size,
    }

    if unread_only:
        conditions.append("is_read = 0")

    where_clause = " AND ".join(conditions)

    total = db.execute(
        text(f"""
            SELECT COUNT(*) FROM notifications
            WHERE {where_clause}
        """),
        params,
    ).scalar() or 0

    rows = db.execute(
        text(f"""
            SELECT id, alert_id, symbol, title, message,
                   current_price, target_price, is_read, created_at
            FROM notifications
            WHERE {where_clause}
            ORDER BY created_at DESC, id DESC
            LIMIT :page_size OFFSET :offset
        """),
        params,
    ).mappings().all()

    return {
        "items": [notification_dict(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/api/notifications/unread-count")
def get_unread_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    count = db.execute(
        text("""
            SELECT COUNT(*) FROM notifications
            WHERE user_id = :user_id AND is_read = 0
        """),
        {"user_id": current_user.id},
    ).scalar() or 0

    return {"unread_count": count}


@router.patch("/api/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = db.execute(
        text("""
            UPDATE notifications
            SET is_read = 1
            WHERE id = :id AND user_id = :user_id
        """),
        {
            "id": notification_id,
            "user_id": current_user.id,
        },
    )
    db.commit()

    if result.rowcount == 0:
        raise HTTPException(
            404, "Không tìm thấy thông báo"
        )

    return {"message": "Đã đánh dấu thông báo đã đọc"}


@router.patch("/api/notifications/read-all")
def mark_all_notifications_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = db.execute(
        text("""
            UPDATE notifications
            SET is_read = 1
            WHERE user_id = :user_id AND is_read = 0
        """),
        {"user_id": current_user.id},
    )
    db.commit()

    return {
        "message": "Đã đánh dấu tất cả thông báo đã đọc",
        "updated_count": result.rowcount,
    }

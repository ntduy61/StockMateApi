
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import User
from app.core.security import get_current_user
from app.schemas.schemas import NotificationSettingsUpdate

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

@router.get("/api/notifications/settings")
def notification_settings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = db.execute(
        text("""
            SELECT
                id,
                user_id,
                news_notification,
                price_notification,
                watchlist_notification,
                system_notification
            FROM stockmate.notification_settings
            WHERE user_id = :user_id
            LIMIT 1
        """),
        {"user_id": current_user.id},
    ).mappings().first()

    # Chưa có cấu hình thì trả về mặc định bật tất cả
    if row is None:
        return {
            "id": None,
            "user_id": current_user.id,
            "news_notification": True,
            "price_notification": True,
            "watchlist_notification": True,
            "system_notification": True,
        }

    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "news_notification": bool(row["news_notification"]),
        "price_notification": bool(row["price_notification"]),
        "watchlist_notification": bool(row["watchlist_notification"]),
        "system_notification": bool(row["system_notification"]),
    }


@router.put("/api/notifications/settings")
def update_notification_settings(
    settings: NotificationSettingsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    params = {
        "user_id": current_user.id,
        "news_notification": settings.news_notification,
        "price_notification": settings.price_notification,
        "watchlist_notification": settings.watchlist_notification,
        "system_notification": settings.system_notification,
    }

    row = db.execute(
        text("""
            SELECT id
            FROM stockmate.notification_settings
            WHERE user_id = :user_id
            LIMIT 1
        """),
        {"user_id": current_user.id},
    ).first()

    if row:
        # Đã có cấu hình -> cập nhật
        db.execute(
            text("""
                UPDATE stockmate.notification_settings
                SET
                    news_notification = :news_notification,
                    price_notification = :price_notification,
                    watchlist_notification = :watchlist_notification,
                    system_notification = :system_notification
                WHERE user_id = :user_id
            """),
            params,
        )
    else:
        # Chưa có cấu hình -> thêm mới
        db.execute(
            text("""
                INSERT INTO stockmate.notification_settings (
                    user_id,
                    news_notification,
                    price_notification,
                    watchlist_notification,
                    system_notification
                )
                VALUES (
                    :user_id,
                    :news_notification,
                    :price_notification,
                    :watchlist_notification,
                    :system_notification
                )
            """),
            params,
        )

    db.commit()

    return {
        "success": True,
        "message": "Cập nhật cấu hình thông báo thành công",
        "user_id": current_user.id,
        "news_notification": settings.news_notification,
        "price_notification": settings.price_notification,
        "watchlist_notification": settings.watchlist_notification,
        "system_notification": settings.system_notification,
    }
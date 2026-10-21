
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import User
from app.core.security import get_current_user

router = APIRouter()


class PushRegisterRequest(BaseModel):
    expo_push_token: str = Field(min_length=1, max_length=255)
    platform: Literal["android", "ios"] = "android"
    device_name: str | None = Field(default=None, max_length=150)


class PushUnregisterRequest(BaseModel):
    expo_push_token: str = Field(min_length=1, max_length=255)


def validate_expo_token(token: str):
    token = token.strip()

    if not (
        token.startswith("ExponentPushToken[")
        or token.startswith("ExpoPushToken[")
    ) or not token.endswith("]"):
        raise HTTPException(400, "Expo Push Token không hợp lệ")

    return token


@router.post("/api/push/register")
def register_push_device(
    data: PushRegisterRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    token = validate_expo_token(data.expo_push_token)

    # Token duy nhất trên toàn hệ thống.
    existing = db.execute(
        text("""
            SELECT id, user_id FROM push_devices
            WHERE expo_push_token = :token
            LIMIT 1
        """),
        {"token": token},
    ).mappings().first()

    if existing:
        db.execute(
            text("""
                UPDATE push_devices
                SET user_id = :user_id,
                    platform = :platform,
                    device_name = :device_name,
                    is_active = 1,
                    last_seen_at = NOW(),
                    updated_at = NOW()
                WHERE id = :id
            """),
            {
                "id": existing["id"],
                "user_id": current_user.id,
                "platform": data.platform,
                "device_name": data.device_name,
            },
        )
    else:
        db.execute(
            text("""
                INSERT INTO push_devices
                    (user_id, expo_push_token, platform,
                     device_name, is_active, last_seen_at,
                     created_at, updated_at)
                VALUES
                    (:user_id, :token, :platform,
                     :device_name, 1, NOW(), NOW(), NOW())
            """),
            {
                "user_id": current_user.id,
                "token": token,
                "platform": data.platform,
                "device_name": data.device_name,
            },
        )

    db.commit()

    return {
        "message": "Đăng ký thiết bị thành công",
        "is_active": True,
    }


@router.delete("/api/push/unregister")
def unregister_push_device(
    data: PushUnregisterRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    token = validate_expo_token(data.expo_push_token)

    result = db.execute(
        text("""
            UPDATE push_devices
            SET is_active = 0, updated_at = NOW()
            WHERE user_id = :user_id
              AND expo_push_token = :token
        """),
        {
            "user_id": current_user.id,
            "token": token,
        },
    )
    db.commit()

    if result.rowcount == 0:
        raise HTTPException(404, "Không tìm thấy thiết bị")

    return {"message": "Đã hủy đăng ký thiết bị"}




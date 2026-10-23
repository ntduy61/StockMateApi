from datetime import date, datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select, func, desc, text
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import (
    User
)
from app.schemas.schemas import RegisterRequest
from app.core.security import (
    password_hash, create_access_token, get_current_user,
    verify_password,
    JWT_EXPIRE_MINUTES,
)
from app.utils.serializers import to_dict

from pydantic import BaseModel, Field



router = APIRouter()


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=128)


router = APIRouter()
@router.post("/api/auth/register", status_code=201)
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

    # Add notify setting
    params = {
        "user_id": user.id,
        "news_notification": 1,
        "price_notification": 1,
        "watchlist_notification": 1,
        "system_notification": 1,
    }
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
    # Add notify setting

    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "role": user.role,
    }


@router.post("/api/auth/login")
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


@router.get("/api/auth/me")
def me(user: User = Depends(get_current_user)):
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "is_active": user.is_active,
    }

@router.post("/api/auth/change-password")
def change_password(
    request: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    # Kiểm tra mật khẩu mới tối thiểu 8 ký tự
    if len(request.new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mật khẩu mới phải có ít nhất 8 ký tự",
        )

    # Kiểm tra mật khẩu hiện tại
    if not verify_password(
        request.current_password,
        current_user.password_hash,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mật khẩu hiện tại không chính xác",
        )

    # Không cho phép dùng lại mật khẩu hiện tại
    if request.current_password == request.new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mật khẩu mới phải khác mật khẩu hiện tại",
        )

    # Cập nhật mật khẩu mới
    current_user.password_hash = password_hash.hash(
        request.new_password
    )

    db.commit()
    db.refresh(current_user)

    return {
        "message": "Đổi mật khẩu thành công"
    }
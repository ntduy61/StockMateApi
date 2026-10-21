from decimal import Decimal

from pydantic import BaseModel, EmailStr, Field


class UserResponse(BaseModel):
    id: int
    email: str
    full_name: str | None = None
    is_active: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"



class WatchlistItemCreate(BaseModel):
    symbol: str = Field(min_length=3, max_length=10)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=100)

class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=128)


class ChangePasswordResponse(BaseModel):
    message: str

class StockCreate(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=255)
    exchange: str = Field(min_length=1, max_length=10)
    industry: str | None = None

class WatchlistCreate(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    note: str | None = Field(default=None, max_length=255)

class PriceAlertCreate(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    target_price: Decimal = Field(gt=0)


class PriceAlertUpdate(BaseModel):
    target_price: Decimal | None = Field(default=None, gt=0)
    is_enabled: bool | None = None

class PushSendRequest(BaseModel):
    user_id: int
    title: str = Field(min_length=1, max_length=100)
    body: str = Field(min_length=1, max_length=500)
    data: dict = Field(default_factory=dict)
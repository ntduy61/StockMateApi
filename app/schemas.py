from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=100)


class UserResponse(BaseModel):
    id: int
    email: str
    full_name: str | None = None
    is_active: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class WatchlistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class WatchlistItemCreate(BaseModel):
    symbol: str = Field(min_length=3, max_length=10)
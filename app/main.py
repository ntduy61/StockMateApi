from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router

app = FastAPI(
    title="StockMate API",
    version="1.0.0",
    description="API dữ liệu cổ phiếu Việt Nam",
)

# CORS cho website DUYVERSE
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://duyverse.com",
        "https://www.duyverse.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/")
def home():
    return {"app": "StockMate API", "version": "1.0.0"}
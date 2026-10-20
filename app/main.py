from fastapi import FastAPI

from app.api.v1.router import api_router

app = FastAPI(
    title="StockMate API",
    version="1.0.0",
    description="API dữ liệu cổ phiếu Việt Nam",
)

app.include_router(api_router)


@app.get("/")
def home():
    return {"app": "StockMate API", "version": "1.0.0"}
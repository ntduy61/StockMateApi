from fastapi import APIRouter

from app.api.v1.endpoints import (
    health, auth, stocks, prices, financials, valuations, watchlist, sync_logs,
    price_alerts,
    notifications,
    push,
)
api_router = APIRouter()

api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(stocks.router)
api_router.include_router(prices.router)
api_router.include_router(financials.router)
api_router.include_router(valuations.router)
api_router.include_router(watchlist.router)
api_router.include_router(sync_logs.router)
api_router.include_router(price_alerts.router)
api_router.include_router(notifications.router)
api_router.include_router(push.router)

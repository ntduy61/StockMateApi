
# app/tasks/check_price_alerts.py

from app.scripts.check_news_notifications import send_news_notification
from sqlalchemy import text
from app.db.database import get_db


if __name__ == "__main__":
    send_news_notification(11854)

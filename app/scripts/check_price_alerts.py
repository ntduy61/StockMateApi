
# app/tasks/check_price_alerts.py

import json
import logging
import os
import urllib.error
import urllib.request

from sqlalchemy import text
from app.db.database import get_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


def send_expo_push(token: str, title: str, message: str, data=None) -> bool:
    """Gửi một push notification qua Expo."""
    if not token or not token.startswith("ExponentPushToken["):
        logging.warning("Expo push token không hợp lệ")
        return False

    payload = {
        "to": token,
        "title": title,
        "body": message,
        "sound": "default",
        "data": data or {},
    }

    request = urllib.request.Request(
        EXPO_PUSH_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))

        ticket = result.get("data", {})
        if isinstance(ticket, list):
            ticket = ticket[0] if ticket else {}

        if ticket.get("status") == "ok":
            logging.info("Đã gửi Expo push thành công")
            return True

        logging.error("Expo từ chối push: %s", result)
        return False

    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        logging.error("Lỗi gửi Expo push: %s", exc)
        return False


def check_price_alerts():
    """
    Kiểm tra cảnh báo giá đã bật.

    Giả định:
      price_alerts: id, user_id, symbol, target_price,
                    is_enabled, is_triggered
      stock_prices: symbol, price, date
      notifications: user_id, symbol, title, message,
                     current_price, target_price, is_read
      push_devices: user_id, expo_push_token
    """

    db = next(get_db())

    try:
        # Lấy danh sách cảnh báo chưa kích hoạt.
        alerts = db.execute(
            text("""
                SELECT id, user_id, symbol, target_price
                FROM price_alerts
                WHERE is_enabled = 1
                  AND is_triggered = 0
            """)
        ).mappings().all()

        logging.info("Có %s cảnh báo cần kiểm tra", len(alerts))

        for alert in alerts:
            alert_id = alert["id"]
            user_id = alert["user_id"]
            symbol = alert["symbol"]
            target_price = float(alert["target_price"])

            # Lấy giá mới nhất trong DB.
            latest = db.execute(
                text("""
                    SELECT current_price
                    FROM stocks
                    WHERE symbol = :symbol
                    ORDER BY created_at DESC
                    LIMIT 1
                """),
                {"symbol": symbol},
            ).mappings().first()

            if not latest or latest["current_price"] is None:
                logging.warning("%s: chưa có giá trong DB", symbol)
                continue

            current_price = float(latest["current_price"])

            # Ví dụ cảnh báo khi giá tăng đến/vượt giá mục tiêu.
            if current_price > target_price:
                continue

            title = f"Cảnh báo giá {symbol}"
            message = (
                f"{symbol} đã đạt tới giá mục tiêu cần mua"
                f"{current_price:,.0f} <= {target_price:,.0f}"
            )

            # Đánh dấu có điều kiện để tránh hai lần chạy
            # cùng kích hoạt một cảnh báo.
            # Điều kiện is_triggered = 0 giúp lần chạy sau bỏ qua.
            updated = db.execute(
                text("""
                    UPDATE price_alerts
                    SET is_triggered = 1
                    WHERE id = :id
                      AND is_enabled = 1
                      AND is_triggered = 0
                """),
                {"id": alert_id},
            )

            if updated.rowcount != 1:
                db.rollback()
                continue

            # Lưu thông báo trong MariaDB.
            db.execute(
                text("""
                    INSERT INTO notifications
                        (user_id, symbol, title, message,
                         current_price, target_price, is_read)
                    VALUES
                        (:user_id, :symbol, :title, :message,
                         :current_price, :target_price, 0)
                """),
                {
                    "user_id": user_id,
                    "symbol": symbol,
                    "title": title,
                    "message": message,
                    "current_price": current_price,
                    "target_price": target_price,
                },
            )

            # Đọc token thiết bị đã đăng ký.
            devices = db.execute(
                text("""
                    SELECT expo_push_token
                    FROM push_devices
                    WHERE user_id = :user_id
                """),
                {"user_id": user_id},
            ).mappings().all()

            # Commit dữ liệu trước khi gọi dịch vụ push.
            db.commit()

            # Gửi tới các thiết bị của người dùng.
            for device in devices:
                logging.info("expo_push_token:%s",device["expo_push_token"])
                send_expo_push(
                    token=device["expo_push_token"],
                    title=title,
                    message=message,
                    data={
                        "symbol": symbol,
                        "alert_id": alert_id,
                    },
                )

            logging.info(
                "%s: giá=%s, mục tiêu=%s, user_id=%s",
                symbol, current_price, target_price, user_id,
            )

    except Exception:
        db.rollback()
        logging.exception("Lỗi kiểm tra cảnh báo giá")
        raise

    finally:
        db.close()


if __name__ == "__main__":
    check_price_alerts()

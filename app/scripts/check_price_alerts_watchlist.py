
import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import text

from app.db.database import get_db
from app.scripts.check_price_alerts import send_expo_push


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")

CAFEF_BASE_URL = "https://cafef.vn"
CAFEF_REALTIME_URL = (
    f"{CAFEF_BASE_URL}/du-lieu/Ajax/PageNew/RealtimePrice.ashx"
)

THRESHOLD_LEVELS = [3.0, 5.0, 7.0]

REQUEST_TIMEOUT = 10


def is_market_open():
    """Kiểm tra giờ giao dịch chứng khoán Việt Nam."""

    now = datetime.now(TIMEZONE)

    if now.weekday() >= 5:
        return False

    current_time = now.time()

    morning = time(9, 0) <= current_time <= time(11, 30)
    afternoon = time(13, 0) <= current_time <= time(15, 0)

    return morning or afternoon


def to_float(value):
    """Chuyển dữ liệu CafeF sang float an toàn."""

    if value is None or value == "":
        return None

    try:
        if isinstance(value, str):
            value = value.replace(",", "").strip()

        result = float(value)

        if result <= 0:
            return None

        return result

    except (ValueError, TypeError):
        return None


def get_cafef_realtime_price(session, symbol):
    """
    Lấy giá realtime từ CafeF.

    Lưu ý:
    Giá Gia và GiaThamChieu của CafeF được quy đổi từ nghìn đồng
    sang đồng bằng cách nhân 1000.
    """

    try:
        response = session.get(
            CAFEF_REALTIME_URL,
            params={
                "Symbol": symbol,
            },
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": f"{CAFEF_BASE_URL}/",
            },
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        result = response.json()

        if not result.get("Success"):
            logging.warning(
                "CafeF không trả dữ liệu thành công cho %s",
                symbol,
            )
            return None

        data = result.get("Data") or {}

        # Giá hiện tại: ưu tiên Gia.
        current_price_raw = None

        for key in (
            "Gia",
            "GiaHienTai",
            "GiaKhopLenh",
            "CurrentPrice",
            "Price",
        ):
            if data.get(key) is not None:
                current_price_raw = data.get(key)
                break

        reference_price_raw = data.get("GiaThamChieu")

        current_price = to_float(current_price_raw)
        reference_price = to_float(reference_price_raw)

        if current_price is None or reference_price is None:
            logging.warning(
                "Không tìm thấy giá hợp lệ của %s. "
                "Các trường CafeF trả về: %s",
                symbol,
                list(data.keys()),
            )
            return None

        # CafeF trả giá theo nghìn đồng.
        current_price *= 1000
        reference_price *= 1000

        return {
            "symbol": symbol,
            "current_price": current_price,
            "reference_price": reference_price,
            "change_percent": (
                (current_price - reference_price)
                / reference_price
            ) * 100,
        }

    except requests.RequestException:
        logging.exception(
            "Lỗi kết nối CafeF khi lấy giá %s",
            symbol,
        )
        return None

    except (ValueError, TypeError):
        logging.exception(
            "Dữ liệu CafeF không hợp lệ cho %s",
            symbol,
        )
        return None


def check_price_alerts_watchlist():

    if not is_market_open():
        logging.info("Ngoài giờ giao dịch, bỏ qua kiểm tra.")
        return

    db_generator = get_db()
    db = next(db_generator)

    session = requests.Session()

    try:
        today = datetime.now(TIMEZONE).date()

        # DISTINCT để tránh một user có nhiều bản ghi Watchlist
        # cho cùng một mã bị xử lý lặp.
        rows = db.execute(
            text("""
                SELECT DISTINCT
                    w.user_id,
                    s.id AS stock_id,
                    s.symbol
                FROM watchlists w
                INNER JOIN stocks s
                    ON s.id = w.stock_id
                INNER JOIN notification_settings ns
                    ON ns.user_id = w.user_id
                WHERE ns.price_notification = 1
                  AND s.status = 1
            """)
        ).mappings().all()

        if not rows:
            logging.info(
                "Không có cổ phiếu nào cần kiểm tra cảnh báo."
            )
            return

        logging.info(
            "Có %s cặp user/cổ phiếu cần kiểm tra.",
            len(rows),
        )

        # Lấy danh sách mã duy nhất.
        symbols = sorted({
            row["symbol"].upper()
            for row in rows
            if row["symbol"]
        })

        # Mỗi mã chỉ gọi CafeF một lần trong một lượt cron.
        realtime_prices = {}

        for symbol in symbols:
            price_data = get_cafef_realtime_price(
                session,
                symbol,
            )

            if price_data:
                realtime_prices[symbol] = price_data

        logging.info(
            "Lấy được giá realtime của %s/%s mã.",
            len(realtime_prices),
            len(symbols),
        )

        for row in rows:

            user_id = row["user_id"]
            stock_id = row["stock_id"]
            symbol = row["symbol"].upper()

            price_data = realtime_prices.get(symbol)

            if not price_data:
                continue

            reference_price = price_data["reference_price"]
            current_price = price_data["current_price"]
            change_percent = price_data["change_percent"]

            if reference_price <= 0 or current_price <= 0:
                continue

            if abs(change_percent) < THRESHOLD_LEVELS[0]:
                continue

            direction = "UP" if change_percent > 0 else "DOWN"

            direction_text = (
                "tăng" if direction == "UP" else "giảm"
            )

            logging.info(
                "%s | %s | %.2f%% | giá %.0f",
                symbol,
                direction,
                change_percent,
                current_price,
            )

            for threshold in THRESHOLD_LEVELS:

                if abs(change_percent) < threshold:
                    continue

                if threshold >= 7:
                    level_text = "rất mạnh"
                elif threshold >= 5:
                    level_text = "mạnh"
                else:
                    level_text = "đáng chú ý"

                title = (
                    f"{symbol} {direction_text} {level_text}"
                )

                message = (
                    f"{symbol} {direction_text} "
                    f"{abs(change_percent):.2f}% "
                    f"so với giá tham chiếu. "
                    f"Giá hiện tại: {current_price:,.0f} đồng."
                )

                try:
                    # Tạo log cảnh báo.
                    # UNIQUE KEY giúp chống cảnh báo trùng.
                    result = db.execute(
                        text("""
                            INSERT IGNORE INTO
                                watchlist_price_alert_logs
                            (
                                user_id,
                                stock_id,
                                symbol,
                                trading_date,
                                reference_price,
                                current_price,
                                change_percent,
                                direction,
                                threshold_level
                            )
                            VALUES
                            (
                                :user_id,
                                :stock_id,
                                :symbol,
                                :trading_date,
                                :reference_price,
                                :current_price,
                                :change_percent,
                                :direction,
                                :threshold
                            )
                        """),
                        {
                            "user_id": user_id,
                            "stock_id": stock_id,
                            "symbol": symbol,
                            "trading_date": today,
                            "reference_price": reference_price,
                            "current_price": current_price,
                            "change_percent": change_percent,
                            "direction": direction,
                            "threshold": threshold,
                        },
                    )

                    if result.rowcount != 1:
                        continue

                    # Tạo thông báo trong database.
                    notification_result = db.execute(
                        text("""
                            INSERT INTO notifications
                            (
                                user_id,
                                symbol,
                                title,
                                message,
                                current_price,
                                target_price,
                                is_read,
                                push_status
                            )
                            VALUES
                            (
                                :user_id,
                                :symbol,
                                :title,
                                :message,
                                :current_price,
                                NULL,
                                0,
                                'pending'
                            )
                        """),
                        {
                            "user_id": user_id,
                            "symbol": symbol,
                            "title": title,
                            "message": message,
                            "current_price": current_price,
                        },
                    )

                    notification_id = notification_result.lastrowid

                    # Liên kết log với notification.
                    db.execute(
                        text("""
                            UPDATE watchlist_price_alert_logs
                            SET notification_id = :notification_id
                            WHERE user_id = :user_id
                              AND stock_id = :stock_id
                              AND trading_date = :trading_date
                              AND direction = :direction
                              AND threshold_level = :threshold
                        """),
                        {
                            "notification_id": notification_id,
                            "user_id": user_id,
                            "stock_id": stock_id,
                            "trading_date": today,
                            "direction": direction,
                            "threshold": threshold,
                        },
                    )

                    # Lấy thiết bị của người dùng.
                    devices = db.execute(
                        text("""
                            SELECT expo_push_token
                            FROM push_devices
                            WHERE user_id = :user_id
                              AND expo_push_token IS NOT NULL
                              AND expo_push_token != ''
                        """),
                        {
                            "user_id": user_id,
                        },
                    ).mappings().all()

                    # Commit trước khi gửi Push.
                    db.commit()

                    success_count = 0

                    for device in devices:

                        token = device["expo_push_token"]

                        success = send_expo_push(
                            token=token,
                            title=title,
                            message=message,
                            data={
                                "type": "WATCHLIST_PRICE_ALERT",
                                "symbol": symbol,
                                "notification_id": notification_id,
                            },
                        )

                        if success:
                            success_count += 1

                    push_status = (
                        "sent" if success_count > 0 else "failed"
                    )

                    # Cập nhật trạng thái gửi Push.
                    db.execute(
                        text("""
                            UPDATE notifications
                            SET
                                push_status = :push_status,
                                push_sent_at = CASE
                                    WHEN :success_count > 0
                                    THEN NOW()
                                    ELSE NULL
                                END
                            WHERE id = :notification_id
                        """),
                        {
                            "push_status": push_status,
                            "success_count": success_count,
                            "notification_id": notification_id,
                        },
                    )

                    db.commit()

                    logging.info(
                        "Đã xử lý %s | %s | mức %.0f%% | "
                        "biến động %.2f%% | user=%s | "
                        "push=%s/%s",
                        symbol,
                        direction,
                        threshold,
                        change_percent,
                        user_id,
                        success_count,
                        len(devices),
                    )

                except Exception:
                    db.rollback()

                    logging.exception(
                        "Lỗi cảnh báo %s | user=%s | mức %.0f%%",
                        symbol,
                        user_id,
                        threshold,
                    )

                    continue

    except Exception:
        db.rollback()

        logging.exception(
            "Lỗi kiểm tra biến động giá Watchlist"
        )

        raise

    finally:
        session.close()
        db.close()

        try:
            db_generator.close()
        except Exception:
            pass


if __name__ == "__main__":
    check_price_alerts_watchlist()

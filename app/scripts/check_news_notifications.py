# app/tasks/check_news_notifications.py

import logging

from sqlalchemy import text
from app.db.database import get_db
from app.scripts.check_price_alerts import send_expo_push

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


def send_news_notification(news_id: int):
    """
    Gửi thông báo khi có tin tức mới.

    Chỉ gửi cho người dùng có mã cổ phiếu
    nằm trong Watchlist.
    """

    db = next(get_db())

    try:
        # 1. Lấy thông tin tin tức.
        news = db.execute(
            text("""
                SELECT
                    id,
                    category_id,
                    stock_symbol,
                    title,
                    summary
                FROM news
                WHERE id = :news_id
            """),
            {"news_id": news_id},
        ).mappings().first()

        if not news:
            logging.warning(
                "Không tìm thấy news_id=%s",
                news_id,
            )
            return

        symbol = news["stock_symbol"]

        if not symbol:
            logging.info(
                "Tin %s không có mã cổ phiếu liên quan",
                news_id,
            )
            return

        title = f"Tin mới về {symbol}"

        message = news["title"] or "Có tin tức mới"

        # 2. Lấy danh sách người dùng đang theo dõi mã này.
        #
        # TODO:
        # Điều chỉnh tên bảng/cột Watchlist theo DB thực tế.
        users = db.execute(
            text("""
                SELECT DISTINCT
                    w.user_id
                FROM watchlists w
                INNER JOIN stocks s
                    ON s.id = w.stock_id
                WHERE FIND_IN_SET(
                    s.symbol,
                    :symbols
                ) > 0
            """),
            {
                "symbols": symbol,
            },
        ).mappings().all()

        logging.info(
            "Tin %s - %s - có %s người theo dõi",
            news_id,
            symbol,
            len(users),
        )

        for user in users:
            user_id = user["user_id"]

            # 3. Kiểm tra đã gửi tin này cho user chưa.
            exists = db.execute(
                text("""
                    SELECT id
                    FROM news_push_logs
                    WHERE user_id = :user_id
                      AND news_id = :news_id
                    LIMIT 1
                """),
                {
                    "user_id": user_id,
                    "news_id": news_id,
                },
            ).first()

            if exists:
                continue

            # 4. Lấy Expo Push Token.
            devices = db.execute(
                text("""
                    SELECT expo_push_token
                    FROM push_devices
                    WHERE user_id = :user_id
                """),
                {"user_id": user_id},
            ).mappings().all()

            # 5. Gửi Push Notification.
            sent = False

            for device in devices:
                success = send_expo_push(
                    token=device["expo_push_token"],
                    title=title,
                    message=message,
                    data={
                        "type": "news",
                        "news_id": news_id,
                        "symbol": symbol,
                    },
                )

                if success:
                    sent = True

            # 6. Chỉ lưu lịch sử khi gửi thành công.
            if sent:
                db.execute(
                    text("""
                        INSERT IGNORE INTO news_push_logs
                            (user_id, news_id)
                        VALUES
                            (:user_id, :news_id)
                    """),
                    {
                        "user_id": user_id,
                        "news_id": news_id,
                    },
                )

                db.commit()

                logging.info(
                    "Đã gửi tin %s cho user_id=%s",
                    news_id,
                    user_id,
                )

    except Exception:
        db.rollback()
        logging.exception(
            "Lỗi gửi thông báo news_id=%s",
            news_id,
        )
        raise

    finally:
        db.close()
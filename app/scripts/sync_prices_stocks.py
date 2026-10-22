
# app/tasks/sync_info_stocks.py

import logging
import requests

from sqlalchemy import text
from app.db.database import get_db

from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

MAX_WORKERS = 15
BATCH_SIZE = 100


CAFEF_API_URL = (
    "https://cafef.vn/du-lieu/Ajax/PageNew/PriceRealTimeHeader.ashx"
)



def get_Company_Realtime_Price(symbol: str):
    """Lấy thông tin doanh nghiệp từ CafeF."""

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": "https://cafef.vn/",
    }

    try:
        response = requests.get(
            CAFEF_API_URL,
            params={"Symbol": symbol.upper()},
            headers=headers,
            timeout=15,
        )
        response.raise_for_status()

        result = response.json()

        if not result.get("Success") or not result.get("Data"):
            logging.warning(
                "CafeF không có thông tin cho mã %s",
                symbol,
            )
            return None

        data = result["Data"]

        return {
            "Gia": data.get("Gia"),
            "GiaThamChieu": data.get("GiaThamChieu")
        }

    except requests.RequestException:
        logging.exception(
            "Lỗi HTTP khi lấy thông tin mã %s",
            symbol,
        )
    except (ValueError, TypeError):
        logging.exception(
            "Lỗi đọc JSON CafeF cho mã %s",
            symbol,
        )

    return None


def fetch_stock_Realtime_Price(stock):
    """Lấy thông tin một mã cổ phiếu."""

    stock_id = stock["id"]
    symbol = stock["symbol"]

    try:
        info = get_Company_Realtime_Price(symbol)
        if not info:
            return None

        gia = info.get("Gia")
        gia_tham_chieu = info.get("GiaThamChieu")
        reference_price = (
            float(gia_tham_chieu) * 1000
            if gia_tham_chieu is not None
            else None
        )

        current_price = (
            float(gia) * 1000
            if gia is not None
            else None
        )

        change_percent = (
            (current_price - reference_price) / reference_price * 100
            if reference_price is not None and reference_price != 0
            and current_price is not None
            else None
        )

        return {
            "id": stock_id,
            "reference_price": reference_price,
            "current_price": current_price,
            "change_percent": change_percent,
        }

    except Exception:
        logging.exception("Lỗi lấy thông tin %s", symbol)
        return None


def get_price_stock():
    """Lấy thông tin song song và cập nhật database theo lô."""

    db = next(get_db())

    update_sql = text("""
        UPDATE stockmate.stocks
        SET reference_price = COALESCE(:reference_price, reference_price),
            current_price = COALESCE(:current_price, current_price),
            change_percent = COALESCE(:change_percent, change_percent)
        WHERE id = :id
    """)

    try:
        stocks = db.execute(
            text("""
                SELECT id, symbol
                FROM stockmate.stocks
                ORDER BY id
            """)
        ).mappings().all()

        total = len(stocks)
        success = 0
        failed = 0
        batch = []

        logging.info(
            "Bắt đầu lấy thông tin %s mã, workers=%s",
            total,
            MAX_WORKERS,
        )

        with ThreadPoolExecutor(
            max_workers=MAX_WORKERS
        ) as executor:

            futures = [
                executor.submit(fetch_stock_Realtime_Price, stock)
                for stock in stocks
            ]

            for future in as_completed(futures):
                result = future.result()

                if result is None:
                    failed += 1
                    continue

                batch.append(result)

                # Cập nhật database theo từng lô
                if len(batch) >= BATCH_SIZE:
                    db.execute(update_sql, batch)
                    db.commit()

                    success += len(batch)
                    batch.clear()

                processed = success + failed + len(batch)

                if processed % 100 == 0 or processed == total:
                    logging.info(
                        "Tiến độ: %s/%s | thành công: %s | lỗi: %s",
                        processed,
                        total,
                        success + len(batch),
                        failed,
                    )

        # Cập nhật phần dữ liệu còn lại
        if batch:
            db.execute(update_sql, batch)
            db.commit()
            success += len(batch)

        logging.info(
            "Hoàn tất: tổng=%s, thành công=%s, lỗi=%s",
            total,
            success,
            failed,
        )

    except Exception:
        db.rollback()
        logging.exception("Lỗi cập nhật thông tin cổ phiếu")
        raise

    finally:
        db.close()

if __name__ == "__main__":
    get_price_stock()

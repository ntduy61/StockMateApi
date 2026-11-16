
import hashlib
import json
import logging
import re
import time

import requests

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

from sqlalchemy import text

from app.db.database import get_db


# ============================================================
# CONFIG
# ============================================================

API_URL = (
    "https://cafef.vn/du-lieu/Ajax/PageNew/LichSuKien.ashx"
)

MAX_WORKERS = 5
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3

SOURCE = "CAFEF"

logger = logging.getLogger(__name__)


# ============================================================
# HELPERS
# ============================================================

def parse_cafef_time(value):
    if not value:
        return None, None

    match = re.search(r"/Date\((-?\d+)", str(value))

    if not match:
        return None, None

    timestamp_ms = int(match.group(1))

    # Bỏ qua timestamp bất thường hoặc ngày trước năm 1900
    try:
        utc_datetime = datetime.fromtimestamp(
            timestamp_ms / 1000,
            tz=timezone.utc
        )

        vn_datetime = (
            utc_datetime + timedelta(hours=7)
        ).replace(tzinfo=None)

        if vn_datetime.year < 1900:
            return None, None

        return timestamp_ms, vn_datetime

    except (ValueError, OverflowError, OSError):
        return None, None


def make_content_hash(content):
    return hashlib.sha256(
        content.strip().encode("utf-8")
    ).hexdigest()


# ============================================================
# FETCH CAFEF
# ============================================================

def fetch_stock_events(stock):

    symbol = stock["symbol"].strip().upper()

    params = {
        "Symbol": symbol,
    }

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
        "Referer": f"https://cafef.vn/du-lieu/{symbol}.chn",
    }

    for attempt in range(1, MAX_RETRIES + 1):

        try:
            response = requests.get(
                API_URL,
                params=params,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            result = response.json()

            if not isinstance(result, dict):
                raise ValueError("Response không hợp lệ")

            if result.get("Success") is not True:
                raise ValueError(
                    result.get("Message")
                    or "CafeF trả về Success=False"
                )

            events = result.get("Data")

            if not isinstance(events, list):
                raise ValueError(
                    "CafeF không trả về Data dạng danh sách"
                )

            return {
                "success": True,
                "symbol": symbol,
                "raw_data": result,
                "events": events,
                "error": None,
            }

        except Exception as exc:

            logger.warning(
                "[%s] Request lần %s/%s lỗi: %s",
                symbol,
                attempt,
                MAX_RETRIES,
                str(exc),
            )

            if attempt < MAX_RETRIES:
                time.sleep(attempt * 2)

            else:
                return {
                    "success": False,
                    "symbol": symbol,
                    "raw_data": None,
                    "events": [],
                    "error": str(exc),
                }


# ============================================================
# UPSERT EVENT
# ============================================================

def upsert_event(db, symbol, event):

    timestamp_ms, event_datetime = parse_cafef_time(
        event.get("Time")
    )

    if timestamp_ms is None:
        return None

    event_type = event.get("type")

    if event_type is None:
        raise ValueError(
            f"{symbol}: Sự kiện thiếu type"
        )

    event_type = int(event_type)

    sql = text("""
        INSERT INTO stock_events (
            symbol,
            event_timestamp_ms,
            event_datetime,
            event_type,
            source,
            last_synced_at
        )
        VALUES (
            :symbol,
            :timestamp_ms,
            :event_datetime,
            :event_type,
            :source,
            CURRENT_TIMESTAMP
        )
        ON DUPLICATE KEY UPDATE
            event_datetime = VALUES(event_datetime),
            last_synced_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
    """)

    db.execute(
        sql,
        {
            "symbol": symbol,
            "timestamp_ms": timestamp_ms,
            "event_datetime": event_datetime,
            "event_type": event_type,
            "source": SOURCE,
        },
    )

    event_id = db.execute(
        text("""
            SELECT id
            FROM stock_events
            WHERE symbol = :symbol
              AND event_timestamp_ms = :timestamp_ms
              AND event_type = :event_type
        """),
        {
            "symbol": symbol,
            "timestamp_ms": timestamp_ms,
            "event_type": event_type,
        },
    ).scalar_one()

    return event_id


# ============================================================
# UPSERT EVENT DETAILS
# ============================================================

def upsert_event_details(db, event_id, contents):

    if not isinstance(contents, list):
        return

    sql = text("""
        INSERT INTO stock_event_details (
            event_id,
            content,
            content_hash,
            sort_order
        )
        VALUES (
            :event_id,
            :content,
            :content_hash,
            :sort_order
        )
        ON DUPLICATE KEY UPDATE
            content = VALUES(content),
            sort_order = VALUES(sort_order)
    """)

    rows = []

    for index, content in enumerate(contents):

        if content is None:
            continue

        content = str(content).strip()

        if not content:
            continue

        rows.append({
            "event_id": event_id,
            "content": content,
            "content_hash": make_content_hash(content),
            "sort_order": index,
        })

    if rows:
        db.execute(sql, rows)


# ============================================================
# SAVE RAW DATA
# ============================================================

def save_raw_data(
    db,
    sync_run_id,
    symbol,
    raw_data,
    records_count,
):

    db.execute(
        text("""
            INSERT INTO stock_event_raw_data (
                sync_run_id,
                symbol,
                raw_json,
                records_count,
                fetched_at
            )
            VALUES (
                :sync_run_id,
                :symbol,
                :raw_json,
                :records_count,
                CURRENT_TIMESTAMP
            )
        """),
        {
            "sync_run_id": sync_run_id,
            "symbol": symbol,
            "raw_json": json.dumps(
                raw_data,
                ensure_ascii=False,
                default=str,
            ),
            "records_count": records_count,
        },
    )


# ============================================================
# SAVE LOG
# ============================================================

def save_sync_log(
    db,
    sync_run_id,
    symbol,
    status,
    records_count=0,
    error_message=None,
):

    db.execute(
        text("""
            INSERT INTO stock_event_sync_logs (
                sync_run_id,
                symbol,
                status,
                records_count,
                error_message,
                started_at,
                finished_at
            )
            VALUES (
                :sync_run_id,
                :symbol,
                :status,
                :records_count,
                :error_message,
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            )
        """),
        {
            "sync_run_id": sync_run_id,
            "symbol": symbol,
            "status": status,
            "records_count": records_count,
            "error_message": error_message,
        },
    )


# ============================================================
# SYNC ONE STOCK
# ============================================================

def sync_one_stock(db, sync_run_id, result):

    symbol = result["symbol"]

    if not result["success"]:

        save_sync_log(
            db,
            sync_run_id,
            symbol,
            "FAILED",
            error_message=result["error"],
        )

        db.commit()

        return {
            "success": False,
            "symbol": symbol,
            "records_count": 0,
        }

    try:

        processed = 0

        for event in result["events"]:

            if not isinstance(event, dict):
                continue

            event_id = upsert_event(
                db,
                symbol,
                event,
            )

            if event_id is None:
                continue

            upsert_event_details(
                db,
                event_id,
                event.get("Text", []),
            )

            processed += 1

        save_raw_data(
            db,
            sync_run_id,
            symbol,
            result["raw_data"],
            processed,
        )

        save_sync_log(
            db,
            sync_run_id,
            symbol,
            "SUCCESS",
            records_count=processed,
        )

        db.commit()

        logger.info(
            "[%s] Đồng bộ thành công: %s sự kiện",
            symbol,
            processed,
        )

        return {
            "success": True,
            "symbol": symbol,
            "records_count": processed,
        }

    except Exception as exc:

        db.rollback()

        logger.exception(
            "[%s] Lỗi lưu dữ liệu: %s",
            symbol,
            str(exc),
        )

        try:
            save_sync_log(
                db,
                sync_run_id,
                symbol,
                "FAILED",
                error_message=str(exc)[:60000],
            )

            db.commit()

        except Exception:
            db.rollback()

        return {
            "success": False,
            "symbol": symbol,
            "records_count": 0,
        }


# ============================================================
# MAIN
# ============================================================

def sync_stock_events():

    db_generator = get_db()
    db = next(db_generator)

    sync_run_id = None

    try:

        logger.info("=" * 60)
        logger.info("START SYNC STOCK EVENTS")

        # 1. Lấy danh sách mã cổ phiếu
        stocks = db.execute(
            text("""
                SELECT id, symbol
                FROM stockmate.stocks
                WHERE symbol IS NOT NULL
                  AND TRIM(symbol) <> ''
                ORDER BY id
            """)
        ).mappings().all()

        total_symbols = len(stocks)

        logger.info(
            "Tổng mã cổ phiếu: %s",
            total_symbols,
        )

        # 2. Tạo đợt đồng bộ
        result = db.execute(
            text("""
                INSERT INTO stock_event_sync_runs (
                    sync_type,
                    status,
                    total_symbols,
                    started_at
                )
                VALUES (
                    'FULL',
                    'RUNNING',
                    :total_symbols,
                    CURRENT_TIMESTAMP
                )
            """),
            {"total_symbols": total_symbols},
        )

        sync_run_id = result.lastrowid

        db.commit()

        success_symbols = 0
        failed_symbols = 0
        total_events = 0

        # 3. Gọi CafeF song song
        with ThreadPoolExecutor(
            max_workers=MAX_WORKERS
        ) as executor:

            futures = {
                executor.submit(
                    fetch_stock_events,
                    stock,
                ): stock
                for stock in stocks
            }

            for future in as_completed(futures):

                stock = futures[future]

                try:
                    fetch_result = future.result()

                except Exception as exc:

                    fetch_result = {
                        "success": False,
                        "symbol": stock["symbol"],
                        "raw_data": None,
                        "events": [],
                        "error": str(exc),
                    }

                # 4. Ghi database ở thread chính
                sync_result = sync_one_stock(
                    db,
                    sync_run_id,
                    fetch_result,
                )

                if sync_result["success"]:
                    success_symbols += 1
                    total_events += sync_result["records_count"]
                else:
                    failed_symbols += 1

                logger.info(
                    "Progress: %s/%s | Success: %s | Failed: %s",
                    success_symbols + failed_symbols,
                    total_symbols,
                    success_symbols,
                    failed_symbols,
                )

        # 5. Cập nhật trạng thái đợt đồng bộ
        if failed_symbols == 0:
            final_status = "SUCCESS"
        elif success_symbols > 0:
            final_status = "PARTIAL"
        else:
            final_status = "FAILED"

        db.execute(
            text("""
                UPDATE stock_event_sync_runs
                SET
                    status = :status,
                    success_symbols = :success,
                    failed_symbols = :failed,
                    total_events = :total_events,
                    finished_at = CURRENT_TIMESTAMP
                WHERE id = :sync_run_id
            """),
            {
                "status": final_status,
                "success": success_symbols,
                "failed": failed_symbols,
                "total_events": total_events,
                "sync_run_id": sync_run_id,
            },
        )

        db.commit()

        logger.info("=" * 60)
        logger.info("FINISH SYNC STOCK EVENTS")
        logger.info("Run ID: %s", sync_run_id)
        logger.info("Status: %s", final_status)
        logger.info("Total symbols: %s", total_symbols)
        logger.info("Success: %s", success_symbols)
        logger.info("Failed: %s", failed_symbols)
        logger.info("Total events: %s", total_events)
        logger.info("=" * 60)

    except Exception as exc:

        db.rollback()

        logger.exception(
            "Lỗi đồng bộ Stock Events: %s",
            str(exc),
        )

        if sync_run_id:

            try:
                db.execute(
                    text("""
                        UPDATE stock_event_sync_runs
                        SET
                            status = 'FAILED',
                            error_message = :error,
                            finished_at = CURRENT_TIMESTAMP
                        WHERE id = :sync_run_id
                    """),
                    {
                        "error": str(exc)[:60000],
                        "sync_run_id": sync_run_id,
                    },
                )

                db.commit()

            except Exception:
                db.rollback()

        raise

    finally:

        db.close()

        try:
            db_generator.close()
        except Exception:
            pass


if __name__ == "__main__":

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    sync_stock_events()

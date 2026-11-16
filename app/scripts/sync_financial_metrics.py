
import json
import logging
import time

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from decimal import Decimal, InvalidOperation

import requests

from sqlalchemy import text

from app.db.database import get_db


# ============================================================
# CONFIG
# ============================================================

API_URL = (
    "https://apiweb.cafef.vn/api/v1/BCTC/FinancialIndicators"
)

PAGE_INDEX = 1
PAGE_SIZE = 10

MAX_WORKERS = 5
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3

BATCH_SOURCE = "CAFEF"

logger = logging.getLogger(__name__)


# ============================================================
# HELPERS
# ============================================================

def convert_decimal(value):
    """
    Chuyển giá trị CafeF sang Decimal.
    Giữ nguyên số 0, không tự chuyển thành NULL.
    """

    if value is None:
        return None

    try:
        value = str(value).strip()

        if not value:
            return None

        value = value.replace(",", "").replace("%", "")

        return Decimal(value)

    except (InvalidOperation, ValueError, TypeError):
        return None


def convert_datetime(value):
    """
    Chuyển thời gian CafeF sang datetime.
    Nếu không xác định được định dạng thì trả về None.
    """

    if not value:
        return None

    if isinstance(value, datetime):
        return value

    value = str(value).strip()

    try:
        return datetime.fromisoformat(
            value.replace("Z", "+00:00")
        ).replace(tzinfo=None)

    except ValueError:
        pass

    formats = [
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S",
        "%d/%m/%Y",
        "%d/%m/%Y %H:%M:%S",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue

    return None


def convert_int(value, default=None):
    try:
        if value is None or str(value).strip() == "":
            return default

        return int(value)

    except (ValueError, TypeError):
        return default


# ============================================================
# FETCH DATA FROM CAFEF
# ============================================================

def fetch_financial_metrics(stock):
    """
    Gọi API CafeF cho một mã cổ phiếu.

    Không sử dụng database trong thread.
    Chỉ thực hiện HTTP request và xử lý dữ liệu.
    """

    symbol = stock["symbol"]

    params = {
        "symbol": symbol,
        "pageIndex": PAGE_INDEX,
        "pageSize": PAGE_SIZE,
    }

    for attempt in range(1, MAX_RETRIES + 1):

        try:
            response = requests.get(
                API_URL,
                params=params,
                timeout=REQUEST_TIMEOUT,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 "
                        "(Windows NT 10.0; Win64; x64)"
                    ),
                    "Accept": "application/json",
                },
            )

            response.raise_for_status()

            response_data = response.json()

            if not isinstance(response_data, dict):
                raise ValueError("API trả về dữ liệu không hợp lệ")

            if not response_data.get("isSuccess"):
                raise ValueError(
                    f"CafeF trả về isSuccess=False: {symbol}"
                )

            value = response_data.get("value")

            if not isinstance(value, dict):
                raise ValueError(
                    f"Không tìm thấy value trong response: {symbol}"
                )

            periods = value.get("data", [])

            if not isinstance(periods, list):
                raise ValueError(
                    f"value.data không phải danh sách: {symbol}"
                )

            definitions = value.get("templace", [])

            if not isinstance(definitions, list):
                definitions = []

            return {
                "success": True,
                "stock_id": stock["id"],
                "symbol": symbol,
                "raw_data": response_data,
                "definitions": definitions,
                "periods": periods,
                "error": None,
            }

        except Exception as exc:

            logger.warning(
                "[%s] Lỗi gọi CafeF lần %s/%s: %s",
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
                    "stock_id": stock["id"],
                    "symbol": symbol,
                    "raw_data": None,
                    "definitions": [],
                    "periods": [],
                    "error": str(exc),
                }


# ============================================================
# PARSE DATA
# ============================================================

def parse_definitions(definitions):
    """
    Parse danh mục chỉ tiêu từ value.templace.
    """

    result = {}

    for item in definitions:

        if not isinstance(item, dict):
            continue

        code = item.get("code")

        if not code:
            continue

        code = str(code).strip()

        result[code] = {
            "code": code,
            "name": str(item.get("name") or code),
            "number": convert_int(item.get("number")),
        }

    return list(result.values())


def parse_metric_records(symbol, periods):
    """
    Parse các chỉ tiêu trong từng kỳ báo cáo.

    Cấu trúc:
    value.data[]
        -> year
        -> quater
        -> type
        -> time
        -> data[]
            -> code
            -> name
            -> value
    """

    records = []

    for period in periods:

        if not isinstance(period, dict):
            continue

        fiscal_year = convert_int(period.get("year"))

        if not fiscal_year:
            continue

        # CafeF sử dụng tên trường quater.
        fiscal_quarter = convert_int(
            period.get("quater"),
            default=0,
        )

        if fiscal_quarter is None:
            fiscal_quarter = 0

        period_type = period.get("type")

        if period_type is not None:
            period_type = str(period_type)

        data_time = convert_datetime(
            period.get("time")
        )

        metrics = period.get("data", [])

        if not isinstance(metrics, list):
            continue

        for metric in metrics:

            if not isinstance(metric, dict):
                continue

            metric_code = metric.get("code")

            if not metric_code:
                continue

            metric_code = str(metric_code).strip()

            metric_value = convert_decimal(
                metric.get("value")
            )

            # Không bỏ qua giá trị 0.
            # Chỉ bỏ qua giá trị không thể chuyển đổi.
            if metric_value is None:
                continue

            records.append({
                "symbol": symbol,
                "metric_code": metric_code,
                "metric_value": metric_value,
                "fiscal_year": fiscal_year,
                "fiscal_quarter": fiscal_quarter,
                "period_type": period_type,
                "data_time": data_time,
            })

    return records


# ============================================================
# DATABASE UPSERT
# ============================================================

def upsert_definitions(db, definitions):
    """
    Thêm hoặc cập nhật danh mục chỉ tiêu.
    """

    if not definitions:
        return

    sql = text("""
        INSERT INTO financial_metric_definitions (
            code,
            name,
            metric_number
        )
        VALUES (
            :code,
            :name,
            :number
        )
        ON DUPLICATE KEY UPDATE
            name = VALUES(name),
            metric_number = VALUES(metric_number),
            updated_at = CURRENT_TIMESTAMP
    """)

    db.execute(sql, definitions)


def upsert_financial_metrics(db, records):
    """
    Thêm mới hoặc cập nhật chỉ tiêu tài chính.

    UNIQUE:
        symbol
        metric_code
        fiscal_year
        fiscal_quarter
    """

    if not records:
        return

    sql = text("""
        INSERT INTO stock_financial_metrics (
            symbol,
            metric_code,
            metric_value,
            fiscal_year,
            fiscal_quarter,
            period_type,
            source,
            data_time,
            last_synced_at
        )
        VALUES (
            :symbol,
            :metric_code,
            :metric_value,
            :fiscal_year,
            :fiscal_quarter,
            :period_type,
            :source,
            :data_time,
            CURRENT_TIMESTAMP
        )
        ON DUPLICATE KEY UPDATE
            metric_value = VALUES(metric_value),
            period_type = VALUES(period_type),
            data_time = VALUES(data_time),
            last_synced_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
    """)

    for record in records:
        record["source"] = BATCH_SOURCE

    db.execute(sql, records)


def save_raw_data(db, sync_run_id, symbol, raw_data, records_count):
    """
    Lưu response JSON gốc của CafeF.
    """

    sql = text("""
        INSERT INTO financial_metric_raw_data (
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
    """)

    db.execute(
        sql,
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


def save_sync_log(
    db,
    sync_run_id,
    symbol,
    status,
    records_count=0,
    error_message=None,
    started_at=None,
):
    """
    Lưu trạng thái đồng bộ của từng mã.
    """

    sql = text("""
        INSERT INTO financial_metric_sync_logs (
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
            :started_at,
            CURRENT_TIMESTAMP
        )
    """)

    db.execute(
        sql,
        {
            "sync_run_id": sync_run_id,
            "symbol": symbol,
            "status": status,
            "records_count": records_count,
            "error_message": error_message,
            "started_at": started_at or datetime.now(),
        },
    )


# ============================================================
# SYNC ONE STOCK
# ============================================================

def sync_one_stock(db, sync_run_id, result):
    """
    Lưu toàn bộ dữ liệu của một mã trong một transaction.
    """

    symbol = result["symbol"]

    started_at = datetime.now()

    if not result["success"]:

        save_sync_log(
            db=db,
            sync_run_id=sync_run_id,
            symbol=symbol,
            status="FAILED",
            error_message=result["error"],
            started_at=started_at,
        )

        db.commit()

        return {
            "success": False,
            "symbol": symbol,
            "records_count": 0,
        }

    try:

        definitions = parse_definitions(
            result["definitions"]
        )

        records = parse_metric_records(
            symbol,
            result["periods"],
        )

        # 1. Cập nhật danh mục chỉ tiêu.
        upsert_definitions(
            db,
            definitions,
        )

        # 2. Cập nhật dữ liệu tài chính.
        upsert_financial_metrics(
            db,
            records,
        )

        # 3. Lưu JSON gốc.
        save_raw_data(
            db=db,
            sync_run_id=sync_run_id,
            symbol=symbol,
            raw_data=result["raw_data"],
            records_count=len(records),
        )

        # 4. Ghi log thành công.
        save_sync_log(
            db=db,
            sync_run_id=sync_run_id,
            symbol=symbol,
            status="SUCCESS",
            records_count=len(records),
            started_at=started_at,
        )

        # Một mã được lưu trong một transaction.
        db.commit()

        logger.info(
            "[%s] Đồng bộ thành công - %s chỉ tiêu",
            symbol,
            len(records),
        )

        return {
            "success": True,
            "symbol": symbol,
            "records_count": len(records),
        }

    except Exception as exc:

        db.rollback()

        logger.exception(
            "[%s] Lỗi lưu database: %s",
            symbol,
            str(exc),
        )

        # Ghi log lỗi trong transaction mới.
        try:
            save_sync_log(
                db=db,
                sync_run_id=sync_run_id,
                symbol=symbol,
                status="FAILED",
                error_message=str(exc)[:60000],
                started_at=started_at,
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
# MAIN SYNC
# ============================================================

def sync_financial_metrics():

    logger.info("=" * 70)
    logger.info("START SYNC FINANCIAL METRICS")

    db_generator = get_db()
    db = next(db_generator)

    sync_run_id = None

    try:

        # ----------------------------------------------------
        # 1. Lấy danh sách mã cổ phiếu.
        # ----------------------------------------------------

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
            "Tổng số mã cổ phiếu cần đồng bộ: %s",
            total_symbols,
        )

        # ----------------------------------------------------
        # 2. Tạo sync run.
        # ----------------------------------------------------

        result = db.execute(
            text("""
                INSERT INTO financial_metric_sync_runs (
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
            {
                "total_symbols": total_symbols,
            },
        )

        sync_run_id = result.lastrowid

        db.commit()

        # ----------------------------------------------------
        # 3. Gọi API CafeF song song.
        # ----------------------------------------------------

        success_symbols = 0
        failed_symbols = 0
        total_records = 0

        with ThreadPoolExecutor(
            max_workers=MAX_WORKERS
        ) as executor:

            futures = {
                executor.submit(
                    fetch_financial_metrics,
                    stock,
                ): stock
                for stock in stocks
            }

            for future in as_completed(futures):

                stock = futures[future]

                try:
                    fetch_result = future.result()

                except Exception as exc:

                    logger.exception(
                        "[%s] Lỗi xử lý future: %s",
                        stock["symbol"],
                        str(exc),
                    )

                    fetch_result = {
                        "success": False,
                        "stock_id": stock["id"],
                        "symbol": stock["symbol"],
                        "raw_data": None,
                        "definitions": [],
                        "periods": [],
                        "error": str(exc),
                    }

                # Lưu DB ở thread chính.
                sync_result = sync_one_stock(
                    db,
                    sync_run_id,
                    fetch_result,
                )

                if sync_result["success"]:
                    success_symbols += 1
                    total_records += sync_result["records_count"]

                else:
                    failed_symbols += 1

                logger.info(
                    "Progress: %s/%s | Success: %s | Failed: %s",
                    success_symbols + failed_symbols,
                    total_symbols,
                    success_symbols,
                    failed_symbols,
                )

        # ----------------------------------------------------
        # 4. Cập nhật trạng thái toàn bộ đợt đồng bộ.
        # ----------------------------------------------------

        if failed_symbols == 0:
            final_status = "SUCCESS"

        elif success_symbols > 0:
            final_status = "PARTIAL"

        else:
            final_status = "FAILED"

        db.execute(
            text("""
                UPDATE financial_metric_sync_runs
                SET
                    status = :status,
                    success_symbols = :success_symbols,
                    failed_symbols = :failed_symbols,
                    total_records = :total_records,
                    finished_at = CURRENT_TIMESTAMP
                WHERE id = :sync_run_id
            """),
            {
                "status": final_status,
                "success_symbols": success_symbols,
                "failed_symbols": failed_symbols,
                "total_records": total_records,
                "sync_run_id": sync_run_id,
            },
        )

        db.commit()

        logger.info("=" * 70)
        logger.info("FINISH SYNC FINANCIAL METRICS")
        logger.info("Sync Run ID: %s", sync_run_id)
        logger.info("Status: %s", final_status)
        logger.info("Total symbols: %s", total_symbols)
        logger.info("Success: %s", success_symbols)
        logger.info("Failed: %s", failed_symbols)
        logger.info("Total records: %s", total_records)
        logger.info("=" * 70)

    except Exception as exc:

        db.rollback()

        logger.exception(
            "Lỗi đồng bộ Financial Metrics: %s",
            str(exc),
        )

        if sync_run_id:

            try:
                db.execute(
                    text("""
                        UPDATE financial_metric_sync_runs
                        SET
                            status = 'FAILED',
                            error_message = :error_message,
                            finished_at = CURRENT_TIMESTAMP
                        WHERE id = :sync_run_id
                    """),
                    {
                        "error_message": str(exc)[:60000],
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


# ============================================================
# ENTRY POINT
# ============================================================

def main():
    sync_financial_metrics()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s - %(levelname)s - %(message)s"
        ),
    )

    main()

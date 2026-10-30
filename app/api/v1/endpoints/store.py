
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.core.security import get_current_user
from app.models.models import User
import re
router = APIRouter()


# Danh sách Stored Procedure được phép gọi
# Có thể chuyển sang quản lý bằng bảng api_stores sau
STORE_REGISTRY = {
    "sp_get_stock": {
        "params": ["symbol"],
        "methods": ["GET", "POST"],
    },
    "sp_get_stock_count": {
        "params": [],
        "methods": ["GET", "POST"],
    },
}


def serialize_value(value: Any):
    """Chuẩn hóa dữ liệu trả về JSON."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")

    return value


@router.api_route(
    "/api/v1/store/{store_name}",
    methods=["GET", "POST"],
)
async def execute_store(
    store_name: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 1. Kiểm tra tên Stored Procedure
    if not re.fullmatch(r"[a-zA-Z0-9_]+", store_name):
        raise HTTPException(
            status_code=400,
            detail="Tên Stored Procedure không hợp lệ",
        )

    # 2. Lấy danh sách tham số từ MariaDB
    try:
        param_rows = db.execute(
            text("""
                SELECT
                    PARAMETER_NAME,
                    PARAMETER_MODE
                FROM information_schema.PARAMETERS
                WHERE SPECIFIC_SCHEMA = DATABASE()
                  AND SPECIFIC_NAME = :store_name
                  AND ROUTINE_TYPE = 'PROCEDURE'
                ORDER BY ORDINAL_POSITION
            """),
            {"store_name": store_name},
        ).mappings().all()

        # Kiểm tra Store tồn tại
        procedure_exists = db.execute(
            text("""
                SELECT COUNT(*)
                FROM information_schema.ROUTINES
                WHERE ROUTINE_SCHEMA = DATABASE()
                  AND ROUTINE_NAME = :store_name
                  AND ROUTINE_TYPE = 'PROCEDURE'
            """),
            {"store_name": store_name},
        ).scalar()

        if not procedure_exists:
            raise HTTPException(
                status_code=404,
                detail=f"Stored Procedure '{store_name}' không tồn tại",
            )

        # Hiện tại hỗ trợ các tham số IN
        unsupported = [
            p["PARAMETER_NAME"]
            for p in param_rows
            if p["PARAMETER_MODE"] in ("OUT", "INOUT")
        ]

        if unsupported:
            raise HTTPException(
                status_code=400,
                detail="Store hiện chỉ hỗ trợ tham số IN",
            )

        params = [
            p["PARAMETER_NAME"]
            for p in param_rows
        ]

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi đọc thông tin Stored Procedure: {str(e)}",
        )

    # 3. Nhận parameters
    if request.method == "GET":
        input_data = dict(request.query_params)
    else:
        try:
            input_data = await request.json()
        except Exception:
            raise HTTPException(
                status_code=400,
                detail="JSON không hợp lệ",
            )

        if not isinstance(input_data, dict):
            raise HTTPException(
                status_code=400,
                detail="Dữ liệu phải là JSON object",
            )

    # 4. Kiểm tra tham số
    missing = [
        key for key in params
        if key not in input_data
    ]

    extra = [
        key for key in input_data
        if key not in params
    ]

    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Thiếu tham số: {', '.join(missing)}",
        )

    if extra:
        raise HTTPException(
            status_code=400,
            detail=f"Tham số không hợp lệ: {', '.join(extra)}",
        )

    values = [input_data[key] for key in params]

    # 5. Thực thi Stored Procedure bằng PyMySQL
    cursor = None

    try:
        connection = db.connection().connection
        cursor = connection.cursor()

        placeholders = ", ".join(["%s"] * len(values))

        sql = f"CALL `{store_name}`({placeholders})"

        cursor.execute(sql, values)

        # 6. Đọc tất cả result sets
        data = {}
        index = 1

        while True:
            if cursor.description:
                columns = [
                    column[0]
                    for column in cursor.description
                ]

                rows = cursor.fetchall()

                data[f"result_{index}"] = [
                    {
                        column: serialize_value(value)
                        for column, value in zip(columns, row)
                    }
                    for row in rows
                ]

                index += 1

            if not cursor.nextset():
                break

        db.commit()

        return {
            "success": True,
            "message": "Thành công",
            "data": data,
            "meta": {
                "store": store_name,
                "total_results": len(data),
            },
        }

    except Exception as e:
        db.rollback()

        # Ghi lỗi chi tiết vào log server
        import logging
        logging.exception("Stored Procedure execution failed")

        raise HTTPException(
            status_code=500,
            detail=f"Lỗi thực thi Stored Procedure: {str(e)}",
        )

    finally:
        if cursor:
            cursor.close()
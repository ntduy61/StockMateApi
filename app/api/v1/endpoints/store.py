
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
    # 1. Kiểm tra Store
    config = STORE_REGISTRY.get(store_name)

    # Kiểm tra tên Stored Procedure
    if not re.fullmatch(r"[a-zA-Z0-9_]+", store_name):
        raise HTTPException(
            status_code=400,
            detail="Tên Stored Procedure không hợp lệ",
        )

   
    # 2. Nhận parameters
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

    params = config["params"]

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

    # 3. Chuẩn bị câu lệnh CALL
    values = [input_data[key] for key in params]

    placeholders = ", ".join(
        [f":p{i}" for i in range(len(values))]
    )

    sql = f"CALL `{store_name}`({placeholders})"

    bind_params = {
        f"p{i}": value
        for i, value in enumerate(values)
    }

    try:
        # 4. Thực thi Stored Procedure
        result = db.connection().exec_driver_sql(
            sql,
            tuple(values),
        )

        # 5. Đọc tất cả result sets
        # Dùng DBAPI cursor để hỗ trợ nhiều SELECT
        cursor = result.cursor

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

        raise HTTPException(
            status_code=500,
            detail=f"Lỗi thực thi Stored Procedure: {str(e)}",
        )

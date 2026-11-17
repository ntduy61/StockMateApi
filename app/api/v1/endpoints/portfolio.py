
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Body, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import User
from app.core.security import get_current_user


router = APIRouter()


# =========================================================
# HELPERS
# =========================================================

ZERO = Decimal("0")


def to_decimal(value, default=ZERO):
    if value is None:
        return default

    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return default


def to_float(value):
    if value is None:
        return None
    return float(value)


def validate_positive(value, field_name):
    number = to_decimal(value)

    if number <= 0:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} phải lớn hơn 0"
        )

    return number


def validate_non_negative(value, field_name):
    number = to_decimal(value)

    if number < 0:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} không được âm"
        )

    return number


def validate_transaction_type(value):
    transaction_type = str(value or "").upper()

    if transaction_type not in ("BUY", "SELL"):
        raise HTTPException(
            status_code=400,
            detail="Loại giao dịch phải là BUY hoặc SELL"
        )

    return transaction_type


def validate_symbol(value):
    symbol = str(value or "").strip().upper()

    if not symbol:
        raise HTTPException(
            status_code=400,
            detail="Mã cổ phiếu không được để trống"
        )

    return symbol


def validate_date(value):
    if not value:
        return datetime.now()

    if isinstance(value, datetime):
        return value

    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())

    try:
        return datetime.fromisoformat(str(value).replace("Z", ""))
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Ngày giao dịch không hợp lệ"
        )


def row_to_dict(row):
    return dict(row) if row else None


# =========================================================
# PORTFOLIO HELPERS
# =========================================================

def get_portfolio(db, portfolio_id, user_id):
    portfolio = db.execute(
        text("""
            SELECT *
            FROM investment_portfolios
            WHERE id = :portfolio_id
              AND user_id = :user_id
            LIMIT 1
        """),
        {
            "portfolio_id": portfolio_id,
            "user_id": user_id
        }
    ).mappings().first()

    if not portfolio:
        raise HTTPException(
            status_code=404,
            detail="Không tìm thấy danh mục đầu tư"
        )

    return dict(portfolio)


def get_fee_settings(db, portfolio_id):
    settings = db.execute(
        text("""
            SELECT *
            FROM investment_fee_settings
            WHERE portfolio_id = :portfolio_id
            LIMIT 1
        """),
        {"portfolio_id": portfolio_id}
    ).mappings().first()

    if not settings:
        return {
            "portfolio_id": portfolio_id,
            "buy_fee_rate": 0,
            "sell_fee_rate": 0,
            "sell_tax_rate": 0,
            "custody_fee": 0
        }

    return dict(settings)


def calculate_fee_tax(
    transaction_type,
    quantity,
    price,
    settings,
    fee=None,
    tax=None
):
    transaction_value = quantity * price

    if fee is not None:
        calculated_fee = validate_non_negative(fee, "Phí giao dịch")
    elif transaction_type == "BUY":
        calculated_fee = (
            transaction_value
            * to_decimal(settings.get("buy_fee_rate"))
            / Decimal("100")
        )
    else:
        calculated_fee = (
            transaction_value
            * to_decimal(settings.get("sell_fee_rate"))
            / Decimal("100")
        )

    if tax is not None:
        calculated_tax = validate_non_negative(tax, "Thuế")
    elif transaction_type == "SELL":
        calculated_tax = (
            transaction_value
            * to_decimal(settings.get("sell_tax_rate"))
            / Decimal("100")
        )
    else:
        calculated_tax = ZERO

    return calculated_fee, calculated_tax


# =========================================================
# CALCULATE PORTFOLIO
# =========================================================

def calculate_portfolio(db, portfolio_id):
    """
    Tính holdings theo phương pháp giá vốn bình quân.

    BUY:
        quantity tăng
        cost_value tăng theo giá trị mua + phí + thuế

    SELL:
        quantity giảm
        cost_value giảm theo giá vốn bình quân
        realized_profit được cập nhật

    Sau khi tính xong:
        JOIN stocks để lấy current_price.
    """

    transactions = db.execute(
        text("""
            SELECT
                id,
                portfolio_id,
                symbol,
                transaction_type,
                quantity,
                price,
                fee,
                tax,
                transaction_date,
                note
            FROM investment_transactions
            WHERE portfolio_id = :portfolio_id
            ORDER BY transaction_date ASC, id ASC
        """),
        {"portfolio_id": portfolio_id}
    ).mappings().all()

    holdings = {}

    for transaction in transactions:
        symbol = transaction["symbol"].upper()

        transaction_type = transaction["transaction_type"].upper()

        quantity = to_decimal(transaction["quantity"])
        price = to_decimal(transaction["price"])
        fee = to_decimal(transaction["fee"])
        tax = to_decimal(transaction["tax"])

        if symbol not in holdings:
            holdings[symbol] = {
                "symbol": symbol,
                "quantity": ZERO,
                "cost_value": ZERO,
                "realized_profit": ZERO
            }

        holding = holdings[symbol]

        transaction_value = quantity * price

        if transaction_type == "BUY":

            # Giá vốn mua bao gồm phí và thuế
            total_cost = transaction_value + fee + tax

            holding["quantity"] += quantity
            holding["cost_value"] += total_cost

        elif transaction_type == "SELL":

            if holding["quantity"] < quantity:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Giao dịch bán {symbol} tại ngày "
                        f"{transaction['transaction_date']} "
                        "vượt quá số lượng cổ phiếu đang nắm giữ"
                    )
                )

            # Giá vốn bình quân tại thời điểm bán
            average_cost = (
                holding["cost_value"] / holding["quantity"]
                if holding["quantity"] > 0
                else ZERO
            )

            sold_cost = average_cost * quantity

            # Tiền thu về sau khi trừ phí và thuế
            net_proceeds = transaction_value - fee - tax

            holding["realized_profit"] += (
                net_proceeds - sold_cost
            )

            holding["quantity"] -= quantity
            holding["cost_value"] -= sold_cost

            if holding["quantity"] == 0:
                holding["cost_value"] = ZERO

    # Chỉ giữ các mã còn số lượng
    holdings = {
        symbol: holding
        for symbol, holding in holdings.items()
        if holding["quantity"] > 0
    }

    symbols = list(holdings.keys())

    # =====================================================
    # JOIN STOCKS: LẤY GIÁ HIỆN TẠI
    # =====================================================

    current_prices = {}

    if symbols:
        price_rows = db.execute(
            text("""
                SELECT
                    h.symbol,
                    s.current_price
                FROM (
                    SELECT DISTINCT symbol
                    FROM investment_transactions
                    WHERE portfolio_id = :portfolio_id
                ) h
                LEFT JOIN stocks s
                    ON s.symbol = h.symbol
            """),
            {"portfolio_id": portfolio_id}
        ).mappings().all()

        current_prices = {
            row["symbol"]: to_decimal(row["current_price"])
            if row["current_price"] is not None
            else None
            for row in price_rows
        }

    # =====================================================
    # CALCULATE MARKET VALUE AND PROFIT
    # =====================================================

    result = []

    total_quantity = ZERO
    total_cost = ZERO
    total_market_value = ZERO
    total_unrealized_profit = ZERO
    total_realized_profit = ZERO

    has_missing_price = False

    for symbol, holding in holdings.items():

        quantity = holding["quantity"]
        cost_value = holding["cost_value"]

        average_cost = (
            cost_value / quantity
            if quantity > 0
            else ZERO
        )

        current_price = current_prices.get(symbol)

        if current_price is not None:
            market_value = quantity * current_price
            unrealized_profit = market_value - cost_value

            unrealized_profit_percent = (
                unrealized_profit / cost_value * Decimal("100")
                if cost_value > 0
                else ZERO
            )

            total_market_value += market_value
            total_unrealized_profit += unrealized_profit

        else:
            market_value = None
            unrealized_profit = None
            unrealized_profit_percent = None
            has_missing_price = True

        total_quantity += quantity
        total_cost += cost_value
        total_realized_profit += holding["realized_profit"]

        result.append({
            "symbol": symbol,
            "quantity": to_float(quantity),
            "average_cost": to_float(average_cost),
            "cost_value": to_float(cost_value),
            "current_price": to_float(current_price),
            "market_value": to_float(market_value),
            "unrealized_profit": to_float(unrealized_profit),
            "unrealized_profit_percent": to_float(
                unrealized_profit_percent
            ),
            "realized_profit": to_float(
                holding["realized_profit"]
            )
        })

    total_market_value_result = (
        None if has_missing_price else to_float(total_market_value)
    )

    total_unrealized_result = (
        None if has_missing_price else to_float(total_unrealized_profit)
    )

    return {
        "holdings": result,
        "summary": {
            "total_symbols": len(result),
            "total_quantity": to_float(total_quantity),
            "total_cost": to_float(total_cost),
            "total_market_value": total_market_value_result,
            "total_unrealized_profit": total_unrealized_result,
            "total_realized_profit": to_float(total_realized_profit)
        }
    }


# =========================================================
# PORTFOLIO CRUD
# =========================================================

@router.post("/")
def create_portfolio(
    data: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    name = str(data.get("name") or "").strip()

    if not name:
        raise HTTPException(
            status_code=400,
            detail="Tên danh mục không được để trống"
        )

    result = db.execute(
        text("""
            INSERT INTO investment_portfolios (
                user_id,
                name,
                description,
                created_at,
                updated_at
            )
            VALUES (
                :user_id,
                :name,
                :description,
                NOW(),
                NOW()
            )
        """),
        {
            "user_id": current_user.id,
            "name": name,
            "description": data.get("description")
        }
    )

    portfolio_id = result.lastrowid

    db.execute(
        text("""
            INSERT INTO investment_fee_settings (
                portfolio_id,
                buy_fee_rate,
                sell_fee_rate,
                sell_tax_rate,
                custody_fee
            )
            VALUES (
                :portfolio_id,
                0,
                0,
                0,
                0
            )
        """),
        {"portfolio_id": portfolio_id}
    )

    db.commit()

    return {
        "success": True,
        "message": "Tạo danh mục thành công",
        "id": portfolio_id
    }


@router.get("/")
def get_portfolios(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    rows = db.execute(
        text("""
            SELECT
                p.*,
                (
                    SELECT COUNT(*)
                    FROM investment_transactions t
                    WHERE t.portfolio_id = p.id
                ) AS transaction_count
            FROM investment_portfolios p
            WHERE p.user_id = :user_id
            ORDER BY p.id DESC
        """),
        {"user_id": current_user.id}
    ).mappings().all()

    return {
        "success": True,
        "data": [dict(row) for row in rows]
    }


@router.put("/{portfolio_id}")
def update_portfolio(
    portfolio_id: int,
    data: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    get_portfolio(db, portfolio_id, current_user.id)

    name = str(data.get("name") or "").strip()

    if not name:
        raise HTTPException(
            status_code=400,
            detail="Tên danh mục không được để trống"
        )

    db.execute(
        text("""
            UPDATE investment_portfolios
            SET
                name = :name,
                description = :description,
                updated_at = NOW()
            WHERE id = :portfolio_id
        """),
        {
            "portfolio_id": portfolio_id,
            "name": name,
            "description": data.get("description")
        }
    )

    db.commit()

    return {
        "success": True,
        "message": "Cập nhật danh mục thành công"
    }


@router.delete("/{portfolio_id}")
def delete_portfolio(
    portfolio_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    get_portfolio(db, portfolio_id, current_user.id)

    db.execute(
        text("""
            DELETE FROM investment_portfolios
            WHERE id = :portfolio_id
        """),
        {"portfolio_id": portfolio_id}
    )

    db.commit()

    return {
        "success": True,
        "message": "Xóa danh mục thành công"
    }


# =========================================================
# PORTFOLIO SUMMARY
# =========================================================

@router.get("/{portfolio_id}/summary")
def get_portfolio_summary(
    portfolio_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    portfolio = get_portfolio(
        db,
        portfolio_id,
        current_user.id
    )

    calculated = calculate_portfolio(db, portfolio_id)

    return {
        "success": True,
        "data": {
            "portfolio": portfolio,
            **calculated["summary"]
        }
    }


# =========================================================
# PORTFOLIO HOLDINGS
# =========================================================

@router.get("/{portfolio_id}/holdings")
def get_portfolio_holdings(
    portfolio_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    get_portfolio(db, portfolio_id, current_user.id)

    calculated = calculate_portfolio(db, portfolio_id)

    return {
        "success": True,
        "data": calculated["holdings"]
    }


# =========================================================
# TRANSACTIONS
# =========================================================

@router.post("/{portfolio_id}/transactions")
def create_transaction(
    portfolio_id: int,
    data: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    get_portfolio(db, portfolio_id, current_user.id)

    symbol = validate_symbol(data.get("symbol"))

    transaction_type = validate_transaction_type(
        data.get("transaction_type")
    )

    quantity = validate_positive(
        data.get("quantity"),
        "Số lượng"
    )

    price = validate_positive(
        data.get("price"),
        "Giá giao dịch"
    )

    settings = get_fee_settings(db, portfolio_id)

    fee, tax = calculate_fee_tax(
        transaction_type,
        quantity,
        price,
        settings,
        data.get("fee"),
        data.get("tax")
    )

    transaction_date = validate_date(
        data.get("transaction_date")
    )

    try:
        db.execute(
            text("""
                INSERT INTO investment_transactions (
                    portfolio_id,
                    symbol,
                    transaction_type,
                    quantity,
                    price,
                    fee,
                    tax,
                    transaction_date,
                    note,
                    created_at,
                    updated_at
                )
                VALUES (
                    :portfolio_id,
                    :symbol,
                    :transaction_type,
                    :quantity,
                    :price,
                    :fee,
                    :tax,
                    :transaction_date,
                    :note,
                    NOW(),
                    NOW()
                )
            """),
            {
                "portfolio_id": portfolio_id,
                "symbol": symbol,
                "transaction_type": transaction_type,
                "quantity": quantity,
                "price": price,
                "fee": fee,
                "tax": tax,
                "transaction_date": transaction_date,
                "note": data.get("note")
            }
        )

        # Kiểm tra lại lịch sử giao dịch.
        # Nếu bán vượt số lượng đang có thì rollback.
        calculate_portfolio(db, portfolio_id)

        db.commit()

    except Exception as exc:
        db.rollback()

        if isinstance(exc, HTTPException):
            raise exc

        raise HTTPException(
            status_code=500,
            detail=f"Không thể tạo giao dịch: {str(exc)}"
        )

    return {
        "success": True,
        "message": "Thêm giao dịch thành công"
    }


@router.get("/{portfolio_id}/transactions")
def get_transactions(
    portfolio_id: int,
    symbol: str = Query(None),
    transaction_type: str = Query(None, alias="type"),
    from_date: date = Query(None),
    to_date: date = Query(None),
    limit: int = Query(10, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    get_portfolio(db, portfolio_id, current_user.id)

    # Kiểm tra bộ lọc
    if from_date and to_date and from_date > to_date:
        raise HTTPException(
            status_code=400,
            detail="Ngày bắt đầu không được lớn hơn ngày kết thúc"
        )

    normalized_symbol = (
        symbol.strip().upper()
        if symbol and symbol.strip()
        else None
    )

    normalized_type = None

    if transaction_type:
        normalized_type = validate_transaction_type(transaction_type)

    # =====================================================
    # 1. LẤY TOÀN BỘ LỊCH SỬ ĐỂ TÍNH GIÁ VỐN
    # =====================================================

    transactions = db.execute(
        text("""
            SELECT *
            FROM investment_transactions
            WHERE portfolio_id = :portfolio_id
            ORDER BY transaction_date ASC, id ASC
        """),
        {"portfolio_id": portfolio_id}
    ).mappings().all()

    holdings = {}
    calculated_transactions = []

    for transaction in transactions:
        item = dict(transaction)

        transaction_symbol = item["symbol"].upper()
        transaction_type_value = item["transaction_type"].upper()

        quantity = to_decimal(item["quantity"])
        price = to_decimal(item["price"])
        fee = to_decimal(item["fee"])
        tax = to_decimal(item["tax"])

        if transaction_symbol not in holdings:
            holdings[transaction_symbol] = {
                "quantity": ZERO,
                "cost_value": ZERO
            }

        holding = holdings[transaction_symbol]

        transaction_value = quantity * price
        realized_profit = ZERO

        if transaction_type_value == "BUY":

            # Giá vốn mua bao gồm phí và thuế
            total_cost = transaction_value + fee + tax

            holding["quantity"] += quantity
            holding["cost_value"] += total_cost

        elif transaction_type_value == "SELL":

            if holding["quantity"] < quantity:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Giao dịch bán {transaction_symbol} tại ngày "
                        f"{item['transaction_date']} "
                        "vượt quá số lượng cổ phiếu đang nắm giữ"
                    )
                )

            # Giá vốn bình quân trước thời điểm bán
            average_cost = (
                holding["cost_value"] / holding["quantity"]
                if holding["quantity"] > 0
                else ZERO
            )

            sold_cost = average_cost * quantity

            # Tiền bán sau khi trừ phí và thuế
            net_proceeds = transaction_value - fee - tax

            realized_profit = net_proceeds - sold_cost

            holding["quantity"] -= quantity
            holding["cost_value"] -= sold_cost

            if holding["quantity"] == 0:
                holding["cost_value"] = ZERO

        # Lưu lời/lỗ của riêng giao dịch này
        item["realized_profit"] = to_float(realized_profit)

        calculated_transactions.append(item)

    # =====================================================
    # 2. ÁP DỤNG BỘ LỌC SAU KHI ĐÃ TÍNH GIÁ VỐN
    # =====================================================

    filtered_transactions = []

    for item in calculated_transactions:
        transaction_date = item["transaction_date"]

        # Chuẩn hóa về date để so sánh
        if isinstance(transaction_date, datetime):
            transaction_day = transaction_date.date()
        elif isinstance(transaction_date, date):
            transaction_day = transaction_date
        else:
            transaction_day = datetime.fromisoformat(
                str(transaction_date)
            ).date()

        if from_date and transaction_day < from_date:
            continue

        if to_date and transaction_day > to_date:
            continue

        if normalized_symbol:
            if item["symbol"].upper() != normalized_symbol:
                continue

        if normalized_type:
            if item["transaction_type"].upper() != normalized_type:
                continue

        filtered_transactions.append(item)

    # =====================================================
    # 3. TÍNH THỐNG KÊ TRÊN KẾT QUẢ ĐÃ LỌC
    # =====================================================

    total_realized_profit = ZERO
    total_fee = ZERO
    total_tax = ZERO

    for item in filtered_transactions:
        total_realized_profit += to_decimal(
            item["realized_profit"]
        )

        total_fee += to_decimal(item["fee"])
        total_tax += to_decimal(item["tax"])

    total = len(filtered_transactions)

    # =====================================================
    # 4. PHÂN TRANG
    # =====================================================

    # Sắp xếp mới nhất lên trước
    filtered_transactions.sort(
        key=lambda item: (
            item["transaction_date"],
            item["id"]
        ),
        reverse=True
    )

    paginated_transactions = filtered_transactions[
        offset:offset + limit
    ]

    return {
        "success": True,
        "data": paginated_transactions,
        "total": total,
        "limit": limit,
        "offset": offset,
        "statistics": {
            "realized_profit": to_float(total_realized_profit),
            "total_fee": to_float(total_fee),
            "total_tax": to_float(total_tax),
            "total_transactions": total
        }
    }


@router.put("/{portfolio_id}/transactions/{transaction_id}")
def update_transaction(
    portfolio_id: int,
    transaction_id: int,
    data: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    get_portfolio(db, portfolio_id, current_user.id)

    old = db.execute(
        text("""
            SELECT *
            FROM investment_transactions
            WHERE id = :transaction_id
              AND portfolio_id = :portfolio_id
            LIMIT 1
        """),
        {
            "transaction_id": transaction_id,
            "portfolio_id": portfolio_id
        }
    ).mappings().first()

    if not old:
        raise HTTPException(
            status_code=404,
            detail="Không tìm thấy giao dịch"
        )

    symbol = validate_symbol(
        data.get("symbol", old["symbol"])
    )

    transaction_type = validate_transaction_type(
        data.get("transaction_type", old["transaction_type"])
    )

    quantity = validate_positive(
        data.get("quantity", old["quantity"]),
        "Số lượng"
    )

    price = validate_positive(
        data.get("price", old["price"]),
        "Giá giao dịch"
    )

    settings = get_fee_settings(db, portfolio_id)

    fee, tax = calculate_fee_tax(
        transaction_type,
        quantity,
        price,
        settings,
        data.get("fee", old["fee"]),
        data.get("tax", old["tax"])
    )

    transaction_date = validate_date(
        data.get("transaction_date", old["transaction_date"])
    )

    try:
        db.execute(
            text("""
                UPDATE investment_transactions
                SET
                    symbol = :symbol,
                    transaction_type = :transaction_type,
                    quantity = :quantity,
                    price = :price,
                    fee = :fee,
                    tax = :tax,
                    transaction_date = :transaction_date,
                    note = :note,
                    updated_at = NOW()
                WHERE id = :transaction_id
                  AND portfolio_id = :portfolio_id
            """),
            {
                "transaction_id": transaction_id,
                "portfolio_id": portfolio_id,
                "symbol": symbol,
                "transaction_type": transaction_type,
                "quantity": quantity,
                "price": price,
                "fee": fee,
                "tax": tax,
                "transaction_date": transaction_date,
                "note": data.get("note", old["note"])
            }
        )

        calculate_portfolio(db, portfolio_id)

        db.commit()

    except Exception as exc:
        db.rollback()

        if isinstance(exc, HTTPException):
            raise exc

        raise HTTPException(
            status_code=500,
            detail=f"Không thể cập nhật giao dịch: {str(exc)}"
        )

    return {
        "success": True,
        "message": "Cập nhật giao dịch thành công"
    }


@router.delete("/{portfolio_id}/transactions/{transaction_id}")
def delete_transaction(
    portfolio_id: int,
    transaction_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    get_portfolio(db, portfolio_id, current_user.id)

    transaction = db.execute(
        text("""
            SELECT id
            FROM investment_transactions
            WHERE id = :transaction_id
              AND portfolio_id = :portfolio_id
            LIMIT 1
        """),
        {
            "transaction_id": transaction_id,
            "portfolio_id": portfolio_id
        }
    ).first()

    if not transaction:
        raise HTTPException(
            status_code=404,
            detail="Không tìm thấy giao dịch"
        )

    try:
        db.execute(
            text("""
                DELETE FROM investment_transactions
                WHERE id = :transaction_id
                  AND portfolio_id = :portfolio_id
            """),
            {
                "transaction_id": transaction_id,
                "portfolio_id": portfolio_id
            }
        )

        calculate_portfolio(db, portfolio_id)

        db.commit()

    except Exception as exc:
        db.rollback()

        if isinstance(exc, HTTPException):
            raise exc

        raise HTTPException(
            status_code=500,
            detail=f"Không thể xóa giao dịch: {str(exc)}"
        )

    return {
        "success": True,
        "message": "Xóa giao dịch thành công"
    }


# =========================================================
# FEE SETTINGS
# =========================================================

@router.get("/{portfolio_id}/fee-settings")
def get_portfolio_fee_settings(
    portfolio_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    get_portfolio(db, portfolio_id, current_user.id)

    settings = get_fee_settings(db, portfolio_id)

    return {
        "success": True,
        "data": settings
    }


@router.put("/{portfolio_id}/fee-settings")
def update_portfolio_fee_settings(
    portfolio_id: int,
    data: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    get_portfolio(db, portfolio_id, current_user.id)

    old = get_fee_settings(db, portfolio_id)

    buy_fee_rate = validate_non_negative(
        data.get("buy_fee_rate", old["buy_fee_rate"]),
        "Phí mua"
    )

    sell_fee_rate = validate_non_negative(
        data.get("sell_fee_rate", old["sell_fee_rate"]),
        "Phí bán"
    )

    sell_tax_rate = validate_non_negative(
        data.get("sell_tax_rate", old["sell_tax_rate"]),
        "Thuế bán"
    )

    custody_fee = validate_non_negative(
        data.get("custody_fee", old["custody_fee"]),
        "Phí lưu ký"
    )

    db.execute(
        text("""
            INSERT INTO investment_fee_settings (
                portfolio_id,
                buy_fee_rate,
                sell_fee_rate,
                sell_tax_rate,
                custody_fee
            )
            VALUES (
                :portfolio_id,
                :buy_fee_rate,
                :sell_fee_rate,
                :sell_tax_rate,
                :custody_fee
            )
            ON DUPLICATE KEY UPDATE
                buy_fee_rate = VALUES(buy_fee_rate),
                sell_fee_rate = VALUES(sell_fee_rate),
                sell_tax_rate = VALUES(sell_tax_rate),
                custody_fee = VALUES(custody_fee),
                updated_at = NOW()
        """),
        {
            "portfolio_id": portfolio_id,
            "buy_fee_rate": buy_fee_rate,
            "sell_fee_rate": sell_fee_rate,
            "sell_tax_rate": sell_tax_rate,
            "custody_fee": custody_fee
        }
    )

    db.commit()

    return {
        "success": True,
        "message": "Cập nhật phí giao dịch thành công"
    }

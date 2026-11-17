
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import User
from app.core.security import get_current_user


router = APIRouter()


# =========================================================
# HELPERS
# =========================================================

def to_decimal(value, field_name, default=None):
    if value is None:
        if default is not None:
            return Decimal(str(default))
        raise HTTPException(
            status_code=400,
            detail=f"Thiếu trường {field_name}"
        )

    try:
        result = Decimal(str(value))

        if not result.is_finite():
            raise InvalidOperation

        return result

    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail=f"Trường {field_name} không hợp lệ"
        )


def validate_positive(value, field_name):
    if value <= 0:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} phải lớn hơn 0"
        )


def validate_non_negative(value, field_name):
    if value < 0:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} không được âm"
        )


def validate_date(value):
    if not value:
        return datetime.now()

    try:
        if isinstance(value, datetime):
            return value

        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail="Ngày giao dịch không hợp lệ"
        )


def money(value):
    return value.quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP
    )


def get_portfolio(db, portfolio_id, user_id):
    portfolio = db.execute(
        text("""
            SELECT *
            FROM investment_portfolios
            WHERE id = :portfolio_id
              AND user_id = :user_id
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

    return portfolio


def get_fee_settings(db, portfolio_id):
    settings = db.execute(
        text("""
            SELECT *
            FROM investment_fee_settings
            WHERE portfolio_id = :portfolio_id
        """),
        {"portfolio_id": portfolio_id}
    ).mappings().first()

    if not settings:
        return {
            "buy_fee_rate": Decimal("0"),
            "sell_fee_rate": Decimal("0"),
            "sell_tax_rate": Decimal("0"),
            "custody_fee": Decimal("0")
        }

    return settings


def calculate_fee_tax(
    transaction_type,
    quantity,
    price,
    settings,
    fee=None,
    tax=None
):
    transaction_value = quantity * price

    if transaction_type == "BUY":
        calculated_fee = (
            transaction_value
            * Decimal(str(settings["buy_fee_rate"]))
            / Decimal("100")
        )
        calculated_tax = Decimal("0")

    else:
        calculated_fee = (
            transaction_value
            * Decimal(str(settings["sell_fee_rate"]))
            / Decimal("100")
        )
        calculated_tax = (
            transaction_value
            * Decimal(str(settings["sell_tax_rate"]))
            / Decimal("100")
        )

    actual_fee = (
        money(to_decimal(fee, "fee"))
        if fee is not None
        else money(calculated_fee)
    )

    actual_tax = (
        money(to_decimal(tax, "tax"))
        if tax is not None
        else money(calculated_tax)
    )

    validate_non_negative(actual_fee, "Phí giao dịch")
    validate_non_negative(actual_tax, "Thuế")

    return actual_fee, actual_tax


def calculate_portfolio(db, portfolio_id):
    transactions = db.execute(
        text("""
            SELECT
                id,
                symbol,
                transaction_type,
                quantity,
                price,
                fee,
                tax,
                transaction_date
            FROM investment_transactions
            WHERE portfolio_id = :portfolio_id
            ORDER BY transaction_date ASC, id ASC
        """),
        {"portfolio_id": portfolio_id}
    ).mappings().all()

    holdings = {}
    total_realized_profit = Decimal("0")

    for transaction in transactions:
        symbol = transaction["symbol"].upper()
        transaction_type = transaction["transaction_type"]

        quantity = Decimal(str(transaction["quantity"]))
        price = Decimal(str(transaction["price"]))
        fee = Decimal(str(transaction["fee"] or 0))
        tax = Decimal(str(transaction["tax"] or 0))

        if symbol not in holdings:
            holdings[symbol] = {
                "symbol": symbol,
                "quantity": Decimal("0"),
                "cost_value": Decimal("0"),
                "realized_profit": Decimal("0")
            }

        holding = holdings[symbol]

        if transaction_type == "BUY":
            holding["quantity"] += quantity
            holding["cost_value"] += (
                quantity * price + fee + tax
            )

        elif transaction_type == "SELL":
            if quantity > holding["quantity"]:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Giao dịch bán {symbol} không hợp lệ: "
                        "số lượng bán vượt quá số lượng đang nắm giữ"
                    )
                )

            if holding["quantity"] <= 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"Không có cổ phiếu {symbol} để bán"
                )

            average_cost = (
                holding["cost_value"] / holding["quantity"]
            )

            sell_value = quantity * price - fee - tax
            cost_of_sold = quantity * average_cost

            realized_profit = sell_value - cost_of_sold

            holding["quantity"] -= quantity
            holding["cost_value"] -= cost_of_sold
            holding["realized_profit"] += realized_profit

            total_realized_profit += realized_profit

    result = []

    for holding in holdings.values():
        if holding["quantity"] <= 0:
            continue

        quantity = holding["quantity"]
        cost_value = holding["cost_value"]

        average_cost = (
            cost_value / quantity
            if quantity > 0
            else Decimal("0")
        )

        result.append({
            "symbol": holding["symbol"],
            "quantity": float(quantity),
            "average_cost": float(money(average_cost)),
            "cost_value": float(money(cost_value)),
            "current_price": None,
            "market_value": None,
            "unrealized_profit": None,
            "realized_profit": float(money(
                holding["realized_profit"]
            ))
        })

    return {
        "holdings": result,
        "total_realized_profit": money(total_realized_profit)
    }


def validate_transaction_history(db, portfolio_id):
    # Kiểm tra toàn bộ lịch sử giao dịch sau khi thêm/sửa/xóa.
    calculate_portfolio(db, portfolio_id)


# =========================================================
# PORTFOLIOS
# =========================================================

@router.post("")
def create_portfolio(
    data: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    name = str(data.get("name", "")).strip()

    if not name:
        raise HTTPException(
            status_code=400,
            detail="Tên danh mục không được để trống"
        )

    description = data.get("description")

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
            "description": description
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


@router.get("")
def get_portfolios(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    portfolios = db.execute(
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
            ORDER BY p.created_at DESC
        """),
        {"user_id": current_user.id}
    ).mappings().all()

    return {
        "success": True,
        "data": [dict(item) for item in portfolios]
    }


@router.put("/{portfolio_id}")
def update_portfolio(
    portfolio_id: int,
    data: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    get_portfolio(db, portfolio_id, current_user.id)

    name = data.get("name")
    description = data.get("description")

    if name is not None:
        name = str(name).strip()

        if not name:
            raise HTTPException(
                status_code=400,
                detail="Tên danh mục không được để trống"
            )

    db.execute(
        text("""
            UPDATE investment_portfolios
            SET
                name = COALESCE(:name, name),
                description = CASE
                    WHEN :has_description = 1 THEN :description
                    ELSE description
                END,
                updated_at = NOW()
            WHERE id = :portfolio_id
        """),
        {
            "portfolio_id": portfolio_id,
            "name": name,
            "description": description,
            "has_description": 1 if "description" in data else 0
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
    portfolio = get_portfolio(db, portfolio_id, current_user.id)

    result = calculate_portfolio(db, portfolio_id)

    total_cost = sum(
        Decimal(str(item["cost_value"]))
        for item in result["holdings"]
    )

    return {
        "success": True,
        "data": {
            "portfolio": dict(portfolio),
            "total_symbols": len(result["holdings"]),
            "total_quantity": sum(
                item["quantity"]
                for item in result["holdings"]
            ),
            "total_cost": float(money(total_cost)),
            "total_market_value": None,
            "total_unrealized_profit": None,
            "total_realized_profit": float(
                result["total_realized_profit"]
            )
        }
    }


# =========================================================
# HOLDINGS
# =========================================================

@router.get("/{portfolio_id}/holdings")
def get_portfolio_holdings(
    portfolio_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    get_portfolio(db, portfolio_id, current_user.id)

    result = calculate_portfolio(db, portfolio_id)

    return {
        "success": True,
        "data": result["holdings"]
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

    symbol = str(data.get("symbol", "")).strip().upper()
    transaction_type = str(
        data.get("transaction_type", "")
    ).strip().upper()

    if not symbol:
        raise HTTPException(
            status_code=400,
            detail="Mã cổ phiếu không được để trống"
        )

    if transaction_type not in ("BUY", "SELL"):
        raise HTTPException(
            status_code=400,
            detail="Loại giao dịch chỉ được là BUY hoặc SELL"
        )

    quantity = to_decimal(data.get("quantity"), "quantity")
    price = to_decimal(data.get("price"), "price")

    validate_positive(quantity, "Số lượng")
    validate_positive(price, "Giá")

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

    result = db.execute(
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

    try:
        validate_transaction_history(db, portfolio_id)
        db.commit()

    except Exception:
        db.rollback()
        raise

    return {
        "success": True,
        "message": "Thêm giao dịch thành công",
        "id": result.lastrowid,
        "fee": float(fee),
        "tax": float(tax)
    }


@router.get("/{portfolio_id}/transactions")
def get_transactions(
    portfolio_id: int,
    symbol: str = Query(None),
    transaction_type: str = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    get_portfolio(db, portfolio_id, current_user.id)

    if transaction_type:
        transaction_type = transaction_type.upper()

        if transaction_type not in ("BUY", "SELL"):
            raise HTTPException(
                status_code=400,
                detail="Loại giao dịch không hợp lệ"
            )

    transactions = db.execute(
        text("""
            SELECT *
            FROM investment_transactions
            WHERE portfolio_id = :portfolio_id
              AND (
                  :symbol IS NULL
                  OR symbol = :symbol
              )
              AND (
                  :transaction_type IS NULL
                  OR transaction_type = :transaction_type
              )
            ORDER BY transaction_date DESC, id DESC
            LIMIT :limit OFFSET :offset
        """),
        {
            "portfolio_id": portfolio_id,
            "symbol": symbol.upper() if symbol else None,
            "transaction_type": transaction_type,
            "limit": limit,
            "offset": offset
        }
    ).mappings().all()

    return {
        "success": True,
        "data": [dict(item) for item in transactions]
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

    transaction = db.execute(
        text("""
            SELECT *
            FROM investment_transactions
            WHERE id = :transaction_id
              AND portfolio_id = :portfolio_id
        """),
        {
            "transaction_id": transaction_id,
            "portfolio_id": portfolio_id
        }
    ).mappings().first()

    if not transaction:
        raise HTTPException(
            status_code=404,
            detail="Không tìm thấy giao dịch"
        )

    symbol = str(
        data.get("symbol", transaction["symbol"])
    ).strip().upper()

    transaction_type = str(
        data.get(
            "transaction_type",
            transaction["transaction_type"]
        )
    ).strip().upper()

    if not symbol:
        raise HTTPException(
            status_code=400,
            detail="Mã cổ phiếu không được để trống"
        )

    if transaction_type not in ("BUY", "SELL"):
        raise HTTPException(
            status_code=400,
            detail="Loại giao dịch không hợp lệ"
        )

    quantity = to_decimal(
        data.get("quantity", transaction["quantity"]),
        "quantity"
    )

    price = to_decimal(
        data.get("price", transaction["price"]),
        "price"
    )

    validate_positive(quantity, "Số lượng")
    validate_positive(price, "Giá")

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
        data.get(
            "transaction_date",
            transaction["transaction_date"]
        )
    )

    note = data.get("note", transaction["note"])

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
            "note": note
        }
    )

    try:
        validate_transaction_history(db, portfolio_id)
        db.commit()

    except Exception:
        db.rollback()
        raise

    return {
        "success": True,
        "message": "Cập nhật giao dịch thành công",
        "fee": float(fee),
        "tax": float(tax)
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
        """),
        {
            "transaction_id": transaction_id,
            "portfolio_id": portfolio_id
        }
    ).mappings().first()

    if not transaction:
        raise HTTPException(
            status_code=404,
            detail="Không tìm thấy giao dịch"
        )

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

    try:
        validate_transaction_history(db, portfolio_id)
        db.commit()

    except Exception:
        db.rollback()
        raise

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
        "data": dict(settings)
    }


@router.put("/{portfolio_id}/fee-settings")
def update_portfolio_fee_settings(
    portfolio_id: int,
    data: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    get_portfolio(db, portfolio_id, current_user.id)

    current = get_fee_settings(db, portfolio_id)

    buy_fee_rate = to_decimal(
        data.get("buy_fee_rate", current["buy_fee_rate"]),
        "buy_fee_rate"
    )

    sell_fee_rate = to_decimal(
        data.get("sell_fee_rate", current["sell_fee_rate"]),
        "sell_fee_rate"
    )

    sell_tax_rate = to_decimal(
        data.get("sell_tax_rate", current["sell_tax_rate"]),
        "sell_tax_rate"
    )

    custody_fee = to_decimal(
        data.get("custody_fee", current["custody_fee"]),
        "custody_fee"
    )

    validate_non_negative(buy_fee_rate, "Phí mua")
    validate_non_negative(sell_fee_rate, "Phí bán")
    validate_non_negative(sell_tax_rate, "Thuế bán")
    validate_non_negative(custody_fee, "Phí lưu ký")

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
        "message": "Cập nhật phí giao dịch thành công",
        "data": {
            "buy_fee_rate": float(buy_fee_rate),
            "sell_fee_rate": float(sell_fee_rate),
            "sell_tax_rate": float(sell_tax_rate),
            "custody_fee": float(custody_fee)
        }
    }

import requests
import re
from datetime import date, datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import bindparam, select, text
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.models.models import (User, Stock)
from app.schemas.schemas import StockCreate
from app.core.security import (require_admin)
from html import unescape
from urllib.parse import urljoin
from app.utils.serializers import to_dict
from fastapi.responses import StreamingResponse
from io import BytesIO
import logging

router = APIRouter()

logger = logging.getLogger(__name__)

CAFEF_BASE_URL = "https://cafef.vn"

CAFEF_BASE_URL_FILE = "https://cafefnew.mediacdn.vn"

def parse_cafef_datetime(value: str | None):
    if not value:
        return None

    match = re.search(
        r"/Date\((\d+)\)/",
        value,
    )

    if not match:
        return None

    timestamp = int(match.group(1)) / 1000

    return datetime.fromtimestamp(
        timestamp,
        tz=timezone.utc,
    ).isoformat()

def parse_cafef_number(value):
    """
    Chuẩn hóa số theo định dạng CafeF Việt Nam.

    '191.032' -> 191032
    '0,01'    -> 0.01
    '4,9'     -> 4.9
    """
    if value is None or value == "":
        return None

    value = str(value).strip()

    # Có dấu phẩy: dấu phẩy là phần thập phân
    if "," in value:
        value = value.replace(".", "").replace(",", ".")

    # Không có dấu phẩy: dấu chấm là phân cách hàng nghìn
    else:
        value = value.replace(".", "")

    try:
        return float(value)
    except (ValueError, TypeError):
        return None

def parse_cafef_url(value):
    """
    CafeF có thể trả về URL dạng HTML anchor hoặc text thông thường.
    """
    if not value:
        return None

    value = unescape(str(value)).strip()

    match = re.search(
        r"""href=['"]([^'"]+)['"]""",
        value,
        re.IGNORECASE,
    )

    if match:
        return urljoin(CAFEF_BASE_URL, match.group(1))

    return None

def clean_cafef_name(value):
    """
    Lấy tên hiển thị từ nội dung HTML nếu cần.
    """
    if not value:
        return ""

    value = unescape(str(value))

    # Bóc phần text nằm trong thẻ HTML
    value = re.sub(r"<[^>]+>", "", value)

    return value.strip()


@router.get("/api/stocks")
def get_stocks(
    exchange: str | None = None,
    q: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    stmt = select(Stock)

    if exchange:
        stmt = stmt.where(Stock.exchange == exchange.upper())

    if q:
        keyword = q.strip()
        stmt = stmt.where(
            (Stock.symbol.like(f"%{keyword}%"))
            | (Stock.name.like(f"%{keyword}%"))
        )

    # total = db.scalar(
    #     select(func.count()).select_from(stmt.subquery())
    # ) or 0
    total = db.execute(
        text("""
            SELECT COUNT(*)
            FROM stocks
            WHERE (:exchange IS NULL OR exchange = :exchange)
        """),
        {"exchange": exchange.upper() if exchange else None}
    ).scalar() or 0

    items = db.scalars(
        stmt.order_by(Stock.symbol)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    return {
        "items": [
            to_dict(
                stock,
                ["id", "symbol", "name", "exchange",
                 "industry", "status", "reference_price", 
                 "current_price", "change_percent", "icb_code1", "icb_name1", "icb_code2", 
                 "icb_name2", "icb_code3", "icb_name3", "icb_code4", "icb_name4"]
            )
            for stock in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/api/stocks/{symbol}")
def get_stock(symbol: str, db: Session = Depends(get_db)):
    stock = db.scalar(
        select(Stock).where(Stock.symbol == symbol.upper())
    )

    if not stock:
        raise HTTPException(404, "Không tìm thấy cổ phiếu")
    return to_dict(
        stock,
        ["id", "symbol", "name", "exchange", "industry",
         "company_type", "status", "listed_date", "website", "url_logo",
         "description", "reference_price", "current_price", "change_percent", "icb_code1", "icb_name1", "icb_code2", 
                 "icb_name2", "icb_code3", "icb_name3", "icb_code4", "icb_name4"]
    )


@router.post("/api/stocks", status_code=201)
def create_stock(
    data: StockCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    symbol = data.symbol.strip().upper()

    if db.scalar(select(Stock.id).where(Stock.symbol == symbol)):
        raise HTTPException(409, "Mã cổ phiếu đã tồn tại")

    stock = Stock(
        symbol=symbol,
        name=data.name.strip(),
        exchange=data.exchange.strip().upper(),
        industry=data.industry,
    )
    db.add(stock)
    db.commit()
    db.refresh(stock)

    return to_dict(
        stock, ["id", "symbol", "name", "exchange", "industry"]
    )


@router.get("/api/stocks/{symbol}/financial_index")
def get_stock_financial_index(
    symbol: str,
    db: Session = Depends(get_db),
):
    symbol = symbol.upper()

    stock = db.scalar(
        select(Stock).where(
            Stock.symbol == symbol
        )
    )

    if not stock:
        raise HTTPException(
            status_code=404,
            detail="Không tìm thấy cổ phiếu",
        )

    url = f"{CAFEF_BASE_URL}/du-lieu/Ajax/PageNew/ChiSoTaiChinh.ashx"

    try:
        response = requests.get(
            url,
            params={"Symbol": symbol},
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": f"{CAFEF_BASE_URL}/",
            },
            timeout=10,
        )

        response.raise_for_status()

        result = response.json()

        if not result.get("Success"):
            return {
                "symbol": symbol,
                "financial_index": {},
            }

        financial_index = {}

        for item in result.get("Data", []):
            code = item.get("Code")
            value = item.get("Value")

            if code:
                financial_index[code] = value

        return {
            "symbol": symbol,
            "financial_index": financial_index,
        }

    except requests.RequestException:
        raise HTTPException(
            status_code=502,
            detail="Không thể lấy chỉ số tài chính từ CafeF",
        )

    except ValueError:
        raise HTTPException(
            status_code=502,
            detail="Dữ liệu chỉ số tài chính không hợp lệ",
        )


@router.get("/api/stocks/{symbol}/realtime")
def get_stock_realtime(
    symbol: str,
    db: Session = Depends(get_db),
):
    symbol = symbol.upper()

    url = f"{CAFEF_BASE_URL}/du-lieu/Ajax/PageNew/PriceRealTimeHeader.ashx"

    try:
        response = requests.get(
            url,
            params={"Symbol": symbol},
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": f"{CAFEF_BASE_URL}/",
            },
            timeout=10,
        )

        response.raise_for_status()

        result = response.json()

        if not result.get("Success"):
            raise HTTPException(
                status_code=502,
                detail="Không thể lấy giá realtime từ CafeF",
            )

        data = result.get("Data") or {}

        current_price = data.get("Gia")
        reference_price = data.get("GiaThamChieu")

        change = None
        change_percent = None

        if (
            current_price is not None
            and reference_price is not None
        ):
            change = current_price - reference_price

            if reference_price != 0:
                change_percent = (
                    change / reference_price
                ) * 100

        return {
            "symbol": symbol,
            "realtime_price": current_price,
            "realtime_reference_price": reference_price,
            "realtime_change": change,
            "realtime_change_percent": change_percent,
            "realtime_volume": data.get("KhoiLuong"),
            "realtime_time": data.get("ThoiGian"),
            "realtime_exchange": data.get("MaSan"),
            "realtime_status": (
                data.get("CompanySatus") or {}
            ),
        }

    except requests.RequestException:
        raise HTTPException(
            status_code=502,
            detail="Không thể kết nối CafeF",
        )

    except ValueError:
        raise HTTPException(
            status_code=502,
            detail="Dữ liệu giá realtime không hợp lệ",
        )

@router.get("/api/stocks/{symbol}/market_info")
def get_stock_market_info(
    symbol: str,
    db: Session = Depends(get_db),
):
    symbol = symbol.upper()

    stock = db.scalar(
        select(Stock).where(
            Stock.symbol == symbol
        )
    )

    if not stock:
        raise HTTPException(
            status_code=404,
            detail="Không tìm thấy cổ phiếu",
        )
    url = f"{CAFEF_BASE_URL}/du-lieu/Ajax/PageNew/RealtimePrice.ashx"

    try:
        response = requests.get(
            url,
            params={
                "Symbol": symbol,
            },
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": f"{CAFEF_BASE_URL}/",
            },
            timeout=10,
        )

        response.raise_for_status()

        result = response.json()

        if not result.get("Success"):
            raise HTTPException(
                status_code=502,
                detail="Không thể lấy thông tin thị trường từ CafeF",
            )

        data = result.get("Data") or {}

        return {
            "symbol": symbol,
            "reference_price": data.get("GiaThamChieu"),
            "ceiling_price": data.get("GiaTran"),
            "floor_price": data.get("GiaSan"),
            "open_price": data.get("GiaMoCua"),
            "highest_price": data.get("GiaCaoNhat"),
            "lowest_price": data.get("GiaThapNhat"),

            "foreign_buy_value": data.get("GiaTriNNMua"),
            "foreign_buy_volume": data.get("KhoiLuongNNMua"),

            "foreign_sell_value": data.get("GiaTriNNBan"),
            "foreign_sell_volume": data.get("KhoiLuongNNBan"),

            "foreign_room_remaining": data.get("RoomConLai"),
        }

    except requests.RequestException:
        raise HTTPException(
            status_code=502,
            detail="Không thể kết nối CafeF",
        )

    except ValueError:
        raise HTTPException(
            status_code=502,
            detail="Dữ liệu CafeF không hợp lệ",
        )


@router.get("/api/stocks/{symbol}/events")
def get_stock_events(
    symbol: str,
    db: Session = Depends(get_db),
):
    symbol = symbol.strip().upper()

    # 1. Kiểm tra cổ phiếu
    stock = db.scalar(
        select(Stock).where(
            Stock.symbol == symbol
        )
    )

    if not stock:
        raise HTTPException(
            status_code=404,
            detail="Không tìm thấy cổ phiếu",
        )

    # 2. Lấy sự kiện và nội dung từ database
    rows = db.execute(
        text("""
            SELECT
                e.id,
                e.event_datetime,
                e.event_type,
                d.content,
                d.sort_order
            FROM stock_events e
            LEFT JOIN stock_event_details d
                ON d.event_id = e.id
            WHERE e.symbol = :symbol
              AND e.source = 'CAFEF'
            ORDER BY
                e.event_datetime DESC,
                e.id DESC,
                d.sort_order ASC,
                d.id ASC
        """),
        {
            "symbol": symbol,
        },
    ).mappings().all()

    # 3. Gom nội dung theo từng sự kiện
    events_dict = {}

    for row in rows:
        event_id = row["id"]

        if event_id not in events_dict:
            events_dict[event_id] = {
                "time": row["event_datetime"],
                "type": row["event_type"],
                "texts": [],
            }

        if row["content"]:
            events_dict[event_id]["texts"].append(
                row["content"]
            )

    events = list(events_dict.values())

    # 4. Trả kết quả
    return {
        "symbol": symbol,
        "total": len(events),
        "events": events,
    }

@router.get("/api/events")
def get_market_events(
    page_index: int = Query(1, alias="pageIndex", ge=1),
    page_size: int = Query(20, alias="pageSize", ge=1, le=100),
    symbol: str | None = Query(None),
    db: Session = Depends(get_db),
):
    symbol = symbol.strip().upper() if symbol else None

    params = {
        "symbol": symbol,
        "limit": page_size,
        "offset": (page_index - 1) * page_size,
    }

    # Điều kiện lọc mã cổ phiếu
    where_sql = """
        WHERE e.source = 'CAFEF'
    """

    if symbol:
        where_sql += " AND e.symbol = :symbol"

    # 1. Tổng số sự kiện
    total = db.execute(
        text(f"""
            SELECT COUNT(*)
            FROM stock_events e
            {where_sql}
        """),
        params,
    ).scalar() or 0

    # 2. Lấy danh sách sự kiện theo trang
    events = db.execute(
        text(f"""
            SELECT
                e.id,
                e.symbol,
                e.event_datetime,
                e.event_type
            FROM stock_events e
            {where_sql}
            ORDER BY
                e.event_datetime DESC,
                e.id DESC
            LIMIT :limit OFFSET :offset
        """),
        params,
    ).mappings().all()

    if not events:
        return {
            "total": total,
            "pageIndex": page_index,
            "pageSize": page_size,
            "events": [],
        }

    event_ids = [item["id"] for item in events]

    # 3. Lấy nội dung của các sự kiện
    detail_rows = db.execute(
        text("""
            SELECT
                event_id,
                content,
                sort_order
            FROM stock_event_details
            WHERE event_id IN :event_ids
            ORDER BY sort_order ASC, id ASC
        """).bindparams(
            bindparam("event_ids", expanding=True)
        ),
        {
            "event_ids": event_ids,
        },
    ).mappings().all()

    details_map = {}

    for row in detail_rows:
        details_map.setdefault(row["event_id"], []).append(
            row["content"]
        )

    # 4. Ghép kết quả
    result = []

    for event in events:
        result.append({
            "id": event["id"],
            "symbol": event["symbol"],
            "time": event["event_datetime"],
            "type": event["event_type"],
            "texts": details_map.get(event["id"], []),
        })

    return {
        "total": total,
        "pageIndex": page_index,
        "pageSize": page_size,
        "events": result,
    }



@router.get("/api/stocks/{symbol}/ownership")
def get_stock_ownership(
    symbol: str,
    db: Session = Depends(get_db),
):
    symbol = symbol.upper().strip()

    stock = db.scalar(
        select(Stock).where(Stock.symbol == symbol)
    )

    if not stock:
        raise HTTPException(
            status_code=404,
            detail="Không tìm thấy cổ phiếu",
        )
    url = f"{CAFEF_BASE_URL}/du-lieu/Ajax/PageNew/CoCauSoHuu.ashx?Symbol={symbol.lower()}"
        

    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "(Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 "
                    "(KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            },
            timeout=15,
        )

        response.raise_for_status()
        result = response.json()

    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Không thể lấy dữ liệu CafeF: {str(exc)}",
        )

    except ValueError:
        raise HTTPException(
            status_code=502,
            detail="Dữ liệu CafeF trả về không hợp lệ",
        )

    if not result.get("Success"):
        raise HTTPException(
            status_code=502,
            detail="CafeF không trả về dữ liệu cơ cấu sở hữu",
        )

    data = result.get("Data") or {}

    shareholders = []

    for item in data.get("CoDongSoHuu") or []:
        shareholders.append({
            "code": item.get("Code"),
            "name": clean_cafef_name(item.get("Name")),
            "url": parse_cafef_url(item.get("Url")),
            "asset_volume": parse_cafef_number(
                item.get("AssetVolume")
            ),
            "asset_rate": parse_cafef_number(
                item.get("AssetRate")
            ),
            "updated_date": item.get("UpdatedDate"),
        })

    return {
        "symbol": symbol,
        "total": len(shareholders),
        "ownership_summary": {
            "foreign": data.get("NuocNgoai"),
            "state": data.get("NhaNuoc"),
            "other": data.get("Khac"),
        },
        "shareholders": shareholders,
    }


@router.get("/api/stocks/{symbol}/foreign-trading")
def get_foreign_trading(
    symbol: str,
    days: int = Query(
        365,
        ge=1,
        le=3650,
        description="Số ngày lịch cần lấy dữ liệu, mặc định 365 ngày",
    ),
    db: Session = Depends(get_db),
):
    symbol = symbol.upper().strip()

    # Kiểm tra mã cổ phiếu trong database
    stock = db.scalar(
        select(Stock).where(Stock.symbol == symbol)
    )

    if not stock:
        raise HTTPException(
            status_code=404,
            detail="Không tìm thấy cổ phiếu",
        )
    url = f"{CAFEF_BASE_URL}/du-lieu/Ajax/PageNew/GetDataNDTNN.ashx?Symbol={symbol.lower()}"

    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 "
                    "(KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            },
            timeout=20,
        )

        response.raise_for_status()
        result = response.json()

    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Không thể kết nối CafeF: {str(exc)}",
        )

    except ValueError:
        raise HTTPException(
            status_code=502,
            detail="Dữ liệu CafeF trả về không hợp lệ",
        )

    if not result.get("Success"):
        raise HTTPException(
            status_code=502,
            detail="CafeF không trả về dữ liệu khối ngoại",
        )

    data = result.get("Data") or {}

    # Tổng số bản ghi lịch sử CafeF báo
    total_count = data.get("Count", 0)

    # Danh sách dữ liệu giao dịch
    rows = data.get("Data") or []

    # Ngày bắt đầu lấy dữ liệu
    cutoff_date = (
        datetime.now(timezone.utc).date()
        - timedelta(days=days)
    )

    foreign_data = []

    for item in rows:
        trade_date_raw = item.get("TradeDate")

        if not trade_date_raw:
            continue

        # CafeF trả ngày dạng /Date(1792688400000)/
        match = re.search(
            r"/Date\((\d+)(?:[+-]\d+)?\)/",
            str(trade_date_raw),
        )

        if not match:
            continue

        timestamp_ms = int(match.group(1))

        trade_date = datetime.fromtimestamp(
            timestamp_ms / 1000,
            tz=timezone.utc,
        ).date()

        # Chỉ lấy dữ liệu trong khoảng days ngày gần nhất
        if trade_date < cutoff_date:
            continue

        foreign_data.append({
            "symbol": item.get("Symbol", symbol),
            "trade_date": trade_date.isoformat(),

            "buy_volume": item.get("BuyVolume", 0),
            "sell_volume": item.get("SellVolume", 0),
            "net_volume": item.get("NetVolume", 0),

            "buy_value": item.get("BuyValue", 0),
            "sell_value": item.get("SellValue", 0),
            "net_value": item.get("NetValue", 0),

            "remaining_room": item.get("Room", 0),
            "total_room": item.get("TotalRoom", 0),
            "foreign_ownership_percent": item.get("Percent", 0),

            "basic_price": item.get("BasicPrice", 0),
            "close_price": item.get("ClosePrice", 0),
            "average_price": item.get("AveragePrice", 0),
        })

    # Sắp xếp ngày mới nhất lên đầu
    foreign_data.sort(
        key=lambda x: x["trade_date"],
        reverse=True,
    )

    return {
        "symbol": symbol,
        "days": days,
        "from_date": cutoff_date.isoformat(),
        "to_date": datetime.now(timezone.utc).date().isoformat(),

        # Count tổng bản ghi CafeF báo, không phải số bản ghi đã lọc
        "total_count": total_count,

        # Số bản ghi thực tế trả về sau khi lọc
        "returned_count": len(foreign_data),

        "data": foreign_data,
    }


from typing import Optional
@router.get("/api/stocks/{symbol}/price-history")
def get_stock_price_history(
    symbol: str,
    days: Optional[int] = Query(
        None,
        ge=1,
        le=3650,
        description="Số ngày lịch cần lấy dữ liệu. Không truyền thì lấy toàn bộ lịch sử giá.",
    ),
    db: Session = Depends(get_db),
):
    symbol = symbol.upper().strip()

    # Kiểm tra mã cổ phiếu
    stock = db.scalar(
        select(Stock).where(Stock.symbol == symbol)
    )

    if not stock:
        raise HTTPException(
            status_code=404,
            detail="Không tìm thấy cổ phiếu",
        )

    url = (
        "https://cafefnew.mediacdn.vn/Images/Uploaded/"
        f"DuLieuDownload/Liveboard/{symbol}_PriceHistory.json"
    )

    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 "
                    "(KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            },
            timeout=20,
        )

        response.raise_for_status()
        rows = response.json()

    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Không thể lấy dữ liệu lịch sử giá CafeF: {str(exc)}",
        )

    except ValueError:
        raise HTTPException(
            status_code=502,
            detail="Dữ liệu lịch sử giá CafeF không hợp lệ",
        )

    if not isinstance(rows, list):
        raise HTTPException(
            status_code=502,
            detail="Định dạng dữ liệu lịch sử giá không hợp lệ",
        )

    today = date.today()

    # Chỉ tính ngày bắt đầu khi có truyền days
    from_date = today - timedelta(days=days) if days is not None else None

    price_history = []

    for item in rows:
        trade_date_raw = item.get("TradeDate")

        if not trade_date_raw:
            continue

        try:
            trade_date = datetime.fromisoformat(
                trade_date_raw
            ).date()

        except (ValueError, TypeError):
            continue

        # Có truyền days thì mới lọc ngày
        if from_date is not None:
            if trade_date < from_date or trade_date > today:
                continue

        price_history.append({
            "symbol": item.get("Symbol", symbol),
            "trade_date": trade_date.isoformat(),

            "basic_price": item.get("BasicPrice", 0),
            "open_price": item.get("OpenPrice", 0),
            "high_price": item.get("HighPrice", 0),
            "low_price": item.get("LowPrice", 0),
            "close_price": item.get("ClosePrice", 0),

            "volume": item.get("Volume", 0),
            "total_value": item.get("TotalValue", 0),

            "ceiling": item.get("Ceiling", 0),
            "floor": item.get("Floor", 0),

            "average_price": item.get("AveragePrice", 0),
            "adjust_price": item.get("AdjustPrice", 0),
            "average_adjust_price": item.get(
                "AverageAdjustPrice", 0
            ),

            "agreed_volume": item.get("AgreedVolume", 0),
            "agreed_value": item.get("AgreedValue", 0),
        })

    # Sắp xếp theo ngày tăng dần
    price_history.sort(
        key=lambda x: x["trade_date"]
    )

    return {
        "symbol": symbol,
        "days": days,
        "from_date": (
            from_date.isoformat() if from_date else None
        ),
        "to_date": today.isoformat(),
        "total": len(price_history),
        "data": price_history,
    }


@router.get("/api/stocks/{symbol}/documents")
def get_stock_documents(
    symbol: str,
    report_type: int = Query(1, alias="Type"),
    year: int = Query(0, alias="Year"),
):
    url = f"{CAFEF_BASE_URL}/du-lieu/Ajax/PageNew/FileBCTC.ashx"
   

    params = {
        "Symbol": symbol.upper(),
        "Type": report_type,
        "Year": year,
    }

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": f"{CAFEF_BASE_URL}/du-lieu/{symbol.upper()}.chn",
    }

    try:
        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=15,
        )
        response.raise_for_status()

        result = response.json()

        return {
            "symbol": symbol.upper(),
            "data": result.get("Data", []),
        }

    except requests.RequestException as e:
        return {
            "symbol": symbol.upper(),
            "data": [],
            "error": str(e),
        }

@router.get("/api/stocks/{symbol}/documents/pdf")
def get_stock_document_pdf(
    symbol: str,
    url: str = Query(..., description="URL PDF từ CafeF"),
):


    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": f"{CAFEF_BASE_URL}/",
            },
            timeout=30,
        )

        response.raise_for_status()

        content_type = response.headers.get(
            "Content-Type",
            "application/pdf",
        )

        if "pdf" not in content_type.lower():
            raise HTTPException(
                status_code=502,
                detail="CafeF không trả về file PDF",
            )

        return StreamingResponse(
            BytesIO(response.content),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="{symbol.upper()}_report.pdf"',
                "Cache-Control": "public, max-age=3600",
            },
        )

    except requests.RequestException as e:
        raise HTTPException(
            status_code=502,
            detail=f"Không thể tải tài liệu từ CafeF: {str(e)}",
        )


@router.get("/api/stocks/{symbol}/report_sumary")
def get_Report_Summary(
    symbol: str,
    page_index: int = Query(1, alias="pageIndex", ge=1),
    page_size: int = Query(4, alias="pageSize", ge=1, le=100),
    report_type: str = Query("ALL", alias="reportType"),
    type_time: str = Query("QUY", alias="TypeTime"),
):

    url = "https://apiweb.cafef.vn/api/v1/BCTC/GetReportSummary"

    params = {
        "symbol": symbol.upper(),
        "pageIndex": page_index,
        "pageSize": page_size,
        "reportType": report_type,
        "TypeTime": type_time,
    }

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
        "Referer": f"{CAFEF_BASE_URL}/du-lieu/{symbol.upper()}.chn",
        "Origin": f"{CAFEF_BASE_URL}",
    }

    try:
        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=20,
        )
        response.raise_for_status()

        result = response.json()

        if not result.get("isSuccess"):
            return {
                "symbol": symbol,
                "data": [],
                "error": "CafeF không trả về dữ liệu thành công",
            }

        value = result.get("value") or {}

        return {
            "symbol": symbol,
            "count": value.get("count", 0),
            "unit": value.get("unit"),
            "listType": value.get("listType", []),
            "templace": value.get("templace", []),
            "data": value.get("data", []),
        }

    except requests.RequestException as e:
        return {
            "symbol": symbol,
            "data": [],
            "error": str(e),
        }

    except ValueError as e:
        return {
            "symbol": symbol,
            "data": [],
            "error": f"Không thể đọc JSON từ CafeF: {e}",
        }



@router.get("/api/stocks/{symbol}/financial_indicators")
def get_FinancialIndicators(
    symbol: str,
    page_index: int = Query(1, alias="pageIndex", ge=1),
    page_size: int = Query(10, alias="pageSize", ge=1, le=100),
):
    symbol = symbol.upper().strip()

    db = next(get_db())

    try:
        # 1. Lấy danh mục chỉ tiêu
        definitions = db.execute(
            text("""
                SELECT
                    code,
                    name,
                    metric_number AS number
                FROM stockmate.financial_metric_definitions
                WHERE is_active = 1
                ORDER BY metric_number
            """)
        ).mappings().all()

        # 2. Đếm số kỳ dữ liệu
        count = db.execute(
            text("""
                SELECT COUNT(DISTINCT CONCAT(
                    fiscal_year, '-', fiscal_quarter
                ))
                FROM stockmate.stock_financial_metrics
                WHERE symbol = :symbol
                  AND source = 'CAFEF'
            """),
            {"symbol": symbol},
        ).scalar() or 0

        # 3. Lấy danh sách kỳ báo cáo
        periods = db.execute(
            text("""
                SELECT DISTINCT
                    fiscal_year,
                    fiscal_quarter,
                    period_type,
                    data_time
                FROM stockmate.stock_financial_metrics
                WHERE symbol = :symbol
                  AND source = 'CAFEF'
                ORDER BY fiscal_year DESC, fiscal_quarter DESC
                LIMIT :limit OFFSET :offset
            """),
            {
                "symbol": symbol,
                "limit": page_size,
                "offset": (page_index - 1) * page_size,
            },
        ).mappings().all()

        # 4. Lấy dữ liệu chỉ tiêu theo từng kỳ
        data = []

        for period in periods:

            metrics = db.execute(
                text("""
                    SELECT
                        m.metric_value AS value,
                        d.name,
                        m.metric_code AS code,
                        d.metric_number AS number
                    FROM stockmate.stock_financial_metrics m
                    LEFT JOIN stockmate.financial_metric_definitions d
                        ON d.code = m.metric_code
                    WHERE m.symbol = :symbol
                      AND m.fiscal_year = :year
                      AND m.fiscal_quarter = :quarter
                      AND m.source = 'CAFEF'
                    ORDER BY d.metric_number
                """),
                {
                    "symbol": symbol,
                    "year": period["fiscal_year"],
                    "quarter": period["fiscal_quarter"],
                },
            ).mappings().all()

            data.append({
                "symbol": symbol,
                "year": period["fiscal_year"],
                "quater": period["fiscal_quarter"],
                "type": period["period_type"],
                "content": None,
                "time": (
                    period["data_time"].isoformat()
                    if period["data_time"]
                    else None
                ),
                "data": [
                    {
                        "value": float(metric["value"])
                            if metric["value"] is not None else None,
                        "name": metric["name"],
                        "code": metric["code"],
                        "number": metric["number"],
                    }
                    for metric in metrics
                ],
            })

        # 5. Trả response tương tự CafeF
        return {
            "isSuccess": True,
            "value": {
                "count": count,
                "name": symbol,
                "templace": [
                    {
                        "name": item["name"],
                        "code": item["code"],
                        "number": item["number"],
                    }
                    for item in definitions
                ],
                "data": data,
            },
        }

    except Exception as e:
        logger.exception(
            "Lỗi lấy Financial Indicators của %s",
            symbol,
        )

        raise HTTPException(
            status_code=500,
            detail="Không thể lấy dữ liệu chỉ tiêu tài chính",
        )

    finally:
        db.close()

@router.get("/api/stock-filters/metrics")
def get_stock_filter_metrics(
    db: Session = Depends(get_db),
):
    rows = db.execute(
        text("""
            SELECT
                code,
                name,
                metric_number AS number,
                unit
            FROM stockmate.financial_metric_definitions
            WHERE is_active = 1
            ORDER BY metric_number
        """)
    ).mappings().all()

    return {
        "items": [
            {
                "code": row["code"],
                "name": row["name"],
                "number": row["number"],
                "unit": row["unit"],
            }
            for row in rows
        ]
    }

from typing import Literal
from pydantic import BaseModel, Field
class StockFilterCondition(BaseModel):
    code: str
    operator: Literal[
        "gt", "gte", "lt", "lte", "eq", "between"
    ]
    value: float
    value_to: float | None = None


class StockFilterRequest(BaseModel):
    conditions: list[StockFilterCondition] = Field(
        min_length=1,
        max_length=20,
    )
    pageIndex: int = Field(default=1, ge=1)
    pageSize: int = Field(default=20, ge=1, le=100)


@router.post("/api/stock-filters/search")
def search_stocks_by_financial_metrics(
    request: StockFilterRequest,
    db: Session = Depends(get_db),
):
    try:
        # 1. Kiểm tra các mã chỉ tiêu có tồn tại và đang hoạt động
        codes = list({
            condition.code.strip()
            for condition in request.conditions
        })

        definitions = db.execute(
            text("""
                SELECT code, name, unit
                FROM stockmate.financial_metric_definitions
                WHERE is_active = 1
                  AND code IN :codes
            """).bindparams(
                bindparam("codes", expanding=True)
            ),
            {"codes": codes},
        ).mappings().all()

        definition_map = {
            row["code"]: row
            for row in definitions
        }

        missing_codes = [
            code for code in codes
            if code not in definition_map
        ]

        if missing_codes:
            raise HTTPException(
                status_code=400,
                detail=f"Chỉ tiêu không hợp lệ: {', '.join(missing_codes)}",
            )

        # 2. Xây dựng điều kiện lọc
        operator_map = {
            "gt": ">",
            "gte": ">=",
            "lt": "<",
            "lte": "<=",
            "eq": "=",
        }

        conditions_sql = []
        params = {}

        for index, condition in enumerate(request.conditions):
            code = condition.code.strip()

            if condition.operator == "between":
                if condition.value_to is None:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Chỉ tiêu {code} cần có value_to",
                    )

                if condition.value > condition.value_to:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Khoảng giá trị của {code} không hợp lệ",
                    )

                conditions_sql.append(f"""
                    EXISTS (
                        SELECT 1
                        FROM stockmate.stock_financial_metrics m
                        WHERE m.symbol = s.symbol
                          AND m.source = 'CAFEF'
                          AND m.metric_code = :code_{index}
                          AND m.fiscal_year = lp.fiscal_year
                          AND m.fiscal_quarter = lp.fiscal_quarter
                          AND m.metric_value BETWEEN
                              :value_{index}
                              AND :value_to_{index}
                    )
                """)

                params[f"code_{index}"] = code
                params[f"value_{index}"] = condition.value
                params[f"value_to_{index}"] = condition.value_to

            else:
                operator = operator_map[condition.operator]

                conditions_sql.append(f"""
                    EXISTS (
                        SELECT 1
                        FROM stockmate.stock_financial_metrics m
                        WHERE m.symbol = s.symbol
                          AND m.source = 'CAFEF'
                          AND m.metric_code = :code_{index}
                          AND m.fiscal_year = lp.fiscal_year
                          AND m.fiscal_quarter = lp.fiscal_quarter
                          AND m.metric_value {operator} :value_{index}
                    )
                """)

                params[f"code_{index}"] = code
                params[f"value_{index}"] = condition.value

        where_conditions = " AND ".join(conditions_sql)

        # 3. Xác định kỳ báo cáo mới nhất của từng cổ phiếu
        latest_period_cte = """
            (
                SELECT
                    m.symbol,
                    m.fiscal_year,
                    MAX(m.fiscal_quarter) AS fiscal_quarter
                FROM stockmate.stock_financial_metrics m
                INNER JOIN (
                    SELECT
                        symbol,
                        MAX(fiscal_year) AS fiscal_year
                    FROM stockmate.stock_financial_metrics
                    WHERE source = 'CAFEF'
                    GROUP BY symbol
                ) y
                    ON y.symbol = m.symbol
                AND y.fiscal_year = m.fiscal_year
                WHERE m.source = 'CAFEF'
                GROUP BY m.symbol, m.fiscal_year
            )
        """

        # 4. Đếm tổng số cổ phiếu phù hợp
        count_sql = text(f"""
            SELECT COUNT(*)
            FROM stockmate.stocks s
            INNER JOIN {latest_period_cte} lp
                ON lp.symbol = s.symbol
            WHERE s.symbol IS NOT NULL
            AND TRIM(s.symbol) <> ''
            AND {where_conditions}
        """)

        total = db.execute(
            count_sql,
            params,
        ).scalar() or 0

        # 5. Lấy danh sách cổ phiếu
        result_params = {
            **params,
            "limit": request.pageSize,
            "offset": (request.pageIndex - 1) * request.pageSize,
        }

        stocks = db.execute(
            text(f"""
                SELECT
                    s.symbol,
                    lp.fiscal_year,
                    lp.fiscal_quarter
                FROM stockmate.stocks s
                INNER JOIN {latest_period_cte} lp
                    ON lp.symbol = s.symbol
                WHERE s.symbol IS NOT NULL
                AND TRIM(s.symbol) <> ''
                AND {where_conditions}
                ORDER BY s.symbol
                LIMIT :limit OFFSET :offset
            """),
            result_params,
        ).mappings().all()

        if not stocks:
            return {
                "total": total,
                "pageIndex": request.pageIndex,
                "pageSize": request.pageSize,
                "items": [],
            }

        # 6. Lấy giá trị các chỉ tiêu đã chọn để hiển thị kết quả
        symbols = [row["symbol"] for row in stocks]

        metric_rows = db.execute(
            text(f"""
                SELECT
                    m.symbol,
                    m.metric_code,
                    m.metric_value
                FROM stockmate.stock_financial_metrics m
                INNER JOIN {latest_period_cte} lp
                    ON lp.symbol = m.symbol
                AND lp.fiscal_year = m.fiscal_year
                AND lp.fiscal_quarter = m.fiscal_quarter
                WHERE m.source = 'CAFEF'
                AND m.symbol IN :symbols
                AND m.metric_code IN :codes
            """).bindparams(
                bindparam("symbols", expanding=True),
                bindparam("codes", expanding=True),
            ),
            {
                "symbols": symbols,
                "codes": codes,
            },
        ).mappings().all()

        metrics_map = {}

        for row in metric_rows:
            metrics_map.setdefault(row["symbol"], {})[
                row["metric_code"]
            ] = (
                float(row["metric_value"])
                if row["metric_value"] is not None
                else None
            )

        # 7. Trả dữ liệu
        items = []

        for stock in stocks:
            metric_values = metrics_map.get(
                stock["symbol"],
                {},
            )

            items.append({
                "symbol": stock["symbol"],
                "year": stock["fiscal_year"],
                "quarter": stock["fiscal_quarter"],
                "metrics": [
                    {
                        "code": code,
                        "name": definition_map[code]["name"],
                        "unit": definition_map[code]["unit"],
                        "value": metric_values.get(code),
                    }
                    for code in codes
                ],
            })

        return {
            "total": total,
            "pageIndex": request.pageIndex,
            "pageSize": request.pageSize,
            "items": items,
        }

    except HTTPException:
        raise

    except Exception:
        logger.exception("Lỗi lọc cổ phiếu theo chỉ tiêu tài chính")

        raise HTTPException(
            status_code=500,
            detail="Không thể lọc cổ phiếu",
        )




@router.get("/api/stocks/{symbol}/list_ceo")
def get_List_Ceo(symbol: str):
    url = f"{CAFEF_BASE_URL}/du-lieu/Ajax/PageNew/ListCeo.ashx"

    symbol = symbol.strip().upper()

    params = {
        "Symbol": symbol,
        "PositionGroup": 0,
    }

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
        "Referer": f"{CAFEF_BASE_URL}/",
    }

    try:
        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=20,
        )
        response.raise_for_status()
        result = response.json()

        if not isinstance(result, dict):
            return {
                "symbol": symbol,
                "success": False,
                "message": "Dữ liệu CafeF không đúng định dạng.",
                "data": [],
            }

        groups = result.get("Data") or []

        normalized_groups = []

        for group in groups:
            if not isinstance(group, dict):
                continue

            members = []

            for person in group.get("values") or []:
                if not isinstance(person, dict):
                    continue

                image = person.get("Image") or ""
                detail_url = person.get("LinkCeoDetail") or ""

                members.append({
                    "code": person.get("CeoCode"),
                    "name": person.get("Name"),
                    "position": person.get("Position"),
                    "position_order": person.get("PositionOrder"),
                    "group_id": person.get("GroupID"),
                    "age": person.get("old"),
                    "image_url": (
                        f"{CAFEF_BASE_URL_FILE}{image}"
                        if image.startswith("/")
                        else image or None
                    ),
                    "detail_url": (
                        f"{CAFEF_BASE_URL}{detail_url}"
                        if detail_url.startswith("/")
                        else detail_url or None
                    ),
                    "schools": person.get("CeoSchools") or [],
                    "positions": person.get("ceoPositions") or [],
                })

            normalized_groups.append({
                "type": group.get("Type"),
                "group_name": group.get("GroupName"),
                "count": len(members),
                "members": members,
            })

        return {
            "symbol": symbol,
            "success": bool(result.get("Success", False)),
            "message": result.get("Message"),
            "total": sum(
                group["count"] for group in normalized_groups
            ),
            "groups": normalized_groups,
        }

    except requests.RequestException as e:
        return {
            "symbol": symbol,
            "success": False,
            "message": f"Lỗi gọi CafeF: {e}",
            "total": 0,
            "groups": [],
        }

    except ValueError as e:
        return {
            "symbol": symbol,
            "success": False,
            "message": f"Không thể đọc JSON từ CafeF: {e}",
            "total": 0,
            "groups": [],
        }

@router.get("/api/stocks/{symbol}/info_ceo")
def get_info_ceo(
    symbol: str,
    ceo_code: str = Query(..., min_length=1),
):
    symbol = symbol.strip().upper()
    ceo_code = ceo_code.strip()

    url = f"{CAFEF_BASE_URL}/du-lieu/Ajax/pagenew/ceov2/ajaxceov2.ashx"

    params = {
        "ceocode": ceo_code,
    }

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
        "Referer": f"{CAFEF_BASE_URL}/",
    }

    try:
        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=20,
        )
        response.raise_for_status()

        result = response.json()

        if not isinstance(result, dict):
            raise ValueError("Dữ liệu CafeF không đúng định dạng.")

        data = result.get("Data") or {}

        if not isinstance(data, dict):
            data = {}

        return {
            "symbol": symbol,
            "Success": result.get("Success", False),
            "Message": result.get("Message"),
            "Data": data,
        }

    except requests.RequestException as e:
        return {
            "symbol": symbol,
            "Success": False,
            "Message": f"Lỗi gọi CafeF: {e}",
            "Data": None,
        }

    except ValueError as e:
        return {
            "symbol": symbol,
            "Success": False,
            "Message": f"Không thể đọc JSON từ CafeF: {e}",
            "Data": None,
        }


@router.get("/api/stocks/{symbol}/ceo_transactions")
def get_ceo_transactions(
    symbol: str,
    ceo_code: str = Query(..., min_length=1),
    typetime: int = Query(0),
):
    symbol = symbol.strip().upper()
    ceo_code = ceo_code.strip()

    url = (
        f"{CAFEF_BASE_URL}/du-lieu/Ajax/PageNew/DataHistory/"
        "GDCoDongByCeoCode.ashx"
    )

    params = {
        "ceocode": ceo_code,
        "typetime": typetime,
    }

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
        "Referer": f"{CAFEF_BASE_URL}/",
    }

    try:
        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=20,
        )
        response.raise_for_status()

        result = response.json()

        if not isinstance(result, dict):
            raise ValueError("Dữ liệu CafeF không đúng định dạng.")

        data = result.get("Data") or []

        if not isinstance(data, list):
            data = []

        return {
            "symbol": symbol,
            "ceo_code": ceo_code,
            "typetime": typetime,
            "Success": result.get("Success", False),
            "Message": result.get("Message"),
            "Total": len(data),
            "Data": data,
        }

    except requests.RequestException as e:
        return {
            "symbol": symbol,
            "ceo_code": ceo_code,
            "typetime": typetime,
            "Success": False,
            "Message": f"Lỗi gọi CafeF: {e}",
            "Total": 0,
            "Data": [],
        }

    except ValueError as e:
        return {
            "symbol": symbol,
            "ceo_code": ceo_code,
            "typetime": typetime,
            "Success": False,
            "Message": f"Không thể đọc JSON từ CafeF: {e}",
            "Total": 0,
            "Data": [],
        }

    
    
@router.get("/api/StockMarket")
def get_StockMarket():
    url = "https://msh-appdata.cafef.vn/rest-api/api/v1/StockMarket"

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://liveboard.cafef.vn/",
        "Origin": "https://liveboard.cafef.vn",
    }

    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=20,
        )
        response.raise_for_status()

        result = response.json()

        # CafeF trả về trực tiếp object chứa data
        data = result.get("data", [])

        if not isinstance(data, list):
            return {
                "data": [],
                "error": "Cấu trúc dữ liệu CafeF không hợp lệ",
            }

        return {
            "data": data,
        }

    except requests.RequestException as e:
        return {
            "data": [],
            "error": f"Lỗi kết nối CafeF: {str(e)}",
        }

    except ValueError as e:
        return {
            "data": [],
            "error": f"Không thể đọc JSON từ CafeF: {str(e)}",
        }

@router.get("/api/StockMarketBoard")
def get_StockMarketBoard(floorcode: int = 11):
    url = "https://cafef.vn/du-lieu/ajax/ajaxliveboard.ashx"

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://liveboard.cafef.vn/",
        "Origin": "https://liveboard.cafef.vn",
    }

    params = {
        "floorcode": floorcode
    }

    try:
        response = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=20,
        )

        response.raise_for_status()

        result = response.json()

        # CafeF trả về object chứa Data, Message, Success
        data = result.get("Data", [])

        if not isinstance(data, list):
            return {
                "data": [],
                "success": False,
                "error": "Cấu trúc dữ liệu CafeF không hợp lệ",
            }

        return {
            "data": data,
            "success": result.get("Success", True),
            "message": result.get("Message"),
        }

    except requests.RequestException as e:
        return {
            "data": [],
            "success": False,
            "error": f"Lỗi kết nối CafeF: {str(e)}",
        }

    except ValueError as e:
        return {
            "data": [],
            "success": False,
            "error": f"Không thể đọc JSON từ CafeF: {str(e)}",
        }


@router.get("/api/stocks/top10")
def get_top10_stocks(
    center_id: str = Query("HOSE"),
    type: str = Query("UP"),
):
    center_id = center_id.strip().upper()
    type = type.strip().upper()

    if center_id not in ["HOSE", "HNX", "UPCOM"]:
        raise HTTPException(
            status_code=400,
            detail="Sàn giao dịch không hợp lệ."
        )

    if type not in ["UP", "DOWN"]:
        raise HTTPException(
            status_code=400,
            detail="Loại dữ liệu không hợp lệ. Chỉ chấp nhận UP hoặc DOWN."
        )

    url = (
        f"{CAFEF_BASE_URL}/du-lieu/Ajax/Mobile/Smart/"
        "AjaxTop10CP.ashx"
    )

    params = {
        "centerID": center_id,
        "type": type,
    }

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
        "Referer": f"{CAFEF_BASE_URL}/",
    }

    try:
        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=20,
        )
        response.raise_for_status()

        result = response.json()

        if not isinstance(result, dict):
            raise ValueError("Dữ liệu CafeF không đúng định dạng.")

        data = result.get("Data") or []

        if not isinstance(data, list):
            data = []

        return {
            "centerID": center_id,
            "type": type,
            "Success": result.get("Success", False),
            "Message": result.get("Message"),
            "Total": len(data),
            "Data": data,
        }

    except requests.RequestException as e:
        return {
            "centerID": center_id,
            "type": type,
            "Success": False,
            "Message": f"Lỗi gọi CafeF: {e}",
            "Total": 0,
            "Data": [],
        }

    except ValueError as e:
        return {
            "centerID": center_id,
            "type": type,
            "Success": False,
            "Message": f"Không thể đọc JSON từ CafeF: {e}",
            "Total": 0,
            "Data": [],
        }


@router.get("/api/stocks/market_map")
def get_market_map(
    type: int = Query(1),
    category: int = Query(0),
    center_id: int = Query(1),
):
    url = (
        f"{CAFEF_BASE_URL}/du-lieu/ajax/mobile/smart/"
        "ajaxbandothitruong.ashx"
    )

    params = {
        "type": type,
        "category": category,
        "centerId": center_id,
    }

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
        "Referer": f"{CAFEF_BASE_URL}/",
    }

    try:
        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=20,
        )
        response.raise_for_status()

        result = response.json()

        if not isinstance(result, dict):
            raise ValueError("Dữ liệu CafeF không đúng định dạng.")

        return {
            "type": type,
            "category": category,
            "centerId": center_id,
            "Success": result.get("Success", False),
            "Message": result.get("Message"),
            "Data": result.get("Data"),
        }

    except requests.RequestException as e:
        return {
            "type": type,
            "category": category,
            "centerId": center_id,
            "Success": False,
            "Message": f"Lỗi gọi CafeF: {e}",
            "Data": None,
        }

    except ValueError as e:
        return {
            "type": type,
            "category": category,
            "centerId": center_id,
            "Success": False,
            "Message": f"Không thể đọc JSON từ CafeF: {e}",
            "Data": None,
        }
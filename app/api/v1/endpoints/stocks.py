import requests
import re
from datetime import date, datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, text
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

router = APIRouter()

CAFEF_BASE_URL = "https://cafef.vn"

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
    url = f"{CAFEF_BASE_URL}/du-lieu/Ajax/PageNew/LichSuKien.ashx"
    

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
                detail="Không thể lấy lịch sử sự kiện từ CafeF",
            )

        events = []

        for item in result.get("Data", []):
            events.append({
                "time": parse_cafef_datetime(
                    item.get("Time")
                ),
                "type": item.get("type"),
                "texts": item.get("Text") or [],
            })

        return {
            "symbol": symbol,
            "total": len(events),
            "events": events,
        }

    except requests.RequestException:
        raise HTTPException(
            status_code=502,
            detail="Không thể kết nối CafeF",
        )

    except ValueError:
        raise HTTPException(
            status_code=502,
            detail="Dữ liệu sự kiện CafeF không hợp lệ",
        )


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


@router.get("/api/stocks/{symbol}/price-history")
def get_stock_price_history(
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

    # Ngày bắt đầu lấy dữ liệu
    today = date.today()
    from_date = today - timedelta(days=days)

    price_history = []

    for item in rows:
        trade_date_raw = item.get("TradeDate")

        if not trade_date_raw:
            continue

        try:
            # Ví dụ: 2026-10-23T00:00:00
            trade_date = datetime.fromisoformat(
                trade_date_raw
            ).date()

        except (ValueError, TypeError):
            continue

        # Chỉ lấy dữ liệu trong khoảng ngày yêu cầu
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

    # Sắp xếp theo ngày tăng dần để biểu đồ dễ sử dụng
    price_history.sort(
        key=lambda x: x["trade_date"]
    )

    return {
        "symbol": symbol,
        "days": days,
        "from_date": from_date.isoformat(),
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
    page_size: int = Query(4, alias="pageSize", ge=1, le=100),
):

    url = "https://apiweb.cafef.vn/api/v1/BCTC/FinancialIndicators"

    params = {
        "symbol": symbol.upper(),
        "pageIndex": page_index,
        "pageSize": page_size
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
                        f"{CAFEF_BASE_URL}{image}"
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


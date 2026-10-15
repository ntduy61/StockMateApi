#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
StockMate - Đồng bộ danh mục cổ phiếu HOSE, HNX, UPCOM
Nguồn dữ liệu: Vnstock VCI
Database: MariaDB
"""

from pathlib import Path

import pandas as pd
from vnstock import Listing

from sqlalchemy import func


import time
import logging
from datetime import date, timedelta

from sqlalchemy import select, text
from sqlalchemy.dialects.mysql import insert as mysql_insert

from vnstock import Quote, Fundamental

from app.database import SessionLocal
from app.models import Stock, StockPrice, StockFinancial, StockValuation

EXCHANGES = ["HOSE", "HNX", "UPCOM"]

# Chuẩn hóa tên sàn từ nguồn dữ liệu
EXCHANGE_MAPPING = {
    "HSX": "HOSE",
    "HOSE": "HOSE",
    "HNX": "HNX",
    "UPCOM": "UPCOM",
}


def fetch_stocks():
    """Lấy danh sách cổ phiếu cơ sở từ Vnstock."""

    listing = Listing(source="VCI")
    df = listing.symbols_by_exchange()

    if df is None or df.empty:
        raise RuntimeError("Không lấy được danh mục cổ phiếu từ Vnstock.")

    # Kiểm tra định dạng dữ liệu nguồn
    required = {"symbol", "exchange", "type", "organ_name"}
    missing = required - set(df.columns)

    if missing:
        raise ValueError(
            f"Vnstock thiếu các cột bắt buộc: {missing}. "
            f"Các cột hiện có: {df.columns.tolist()}"
        )

    df = df.copy()

    df["symbol"] = (
        df["symbol"].astype(str).str.strip().str.upper()
    )
    df["exchange"] = (
        df["exchange"].astype(str).str.strip().str.upper()
    )
    df["type"] = (
        df["type"].astype(str).str.strip().str.upper()
    )
    df["organ_name"] = (
        df["organ_name"].astype(str).str.strip()
    )

    df["exchange"] = df["exchange"].map(EXCHANGE_MAPPING)

    # Chỉ lấy cổ phiếu cơ sở, không lấy trái phiếu,
    # chứng quyền, ETF hoặc hợp đồng phái sinh.
    df = df[
        df["type"].eq("STOCK")
        & df["exchange"].isin(EXCHANGES)
        & df["symbol"].str.fullmatch(r"[A-Z]{3}", na=False)
        & df["organ_name"].ne("")
        & df["organ_name"].ne("NAN")
    ].copy()

    # Mỗi mã chỉ giữ một bản ghi
    df = (
        df.drop_duplicates(subset=["symbol"], keep="first")
        .sort_values(["exchange", "symbol"])
        .reset_index(drop=True)
    )

    if df.empty:
        raise RuntimeError(
            "Không có mã cổ phiếu hợp lệ sau khi lọc. "
            "Hãy kiểm tra dữ liệu nguồn và cột type."
        )

    return df


def sync_stocks():
    """Đồng bộ danh mục cổ phiếu vào MariaDB."""

    df = fetch_stocks()

    #output_dir = Path("data")
    #output_dir.mkdir(parents=True, exist_ok=True)

    # Lưu bản CSV để kiểm tra danh mục nguồn
    #csv_path = output_dir / "stocks_all.csv"
    #df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    # Chuẩn bị dữ liệu theo model Stock
    rows = []

    for _, row in df.iterrows():
        rows.append({
            "symbol": row["symbol"],
            "name": row["organ_name"],
            "exchange": row["exchange"],
            "company_type": "STOCK",
            "status": "ACTIVE",
        })

    db = SessionLocal()

    try:
        # MariaDB: INSERT hoặc UPDATE khi trùng khóa UNIQUE symbol
        stmt = mysql_insert(Stock.__table__).values(rows)

        upsert_stmt = stmt.on_duplicate_key_update(
            name=stmt.inserted.name,
            exchange=stmt.inserted.exchange,
            company_type=stmt.inserted.company_type,
            status=stmt.inserted.status,
            updated_at=text("CURRENT_TIMESTAMP"),
        )

        result = db.execute(upsert_stmt)
        db.commit()

        print("=" * 55)
        print("ĐỒNG BỘ DANH MỤC CỔ PHIẾU THÀNH CÔNG")
        print("=" * 55)

        for exchange in EXCHANGES:
            count = int((df["exchange"] == exchange).sum())
            print(f"{exchange}: {count} mã")

        print(f"Tổng mã nguồn hợp lệ: {len(rows)}")
        #print(f"CSV: {csv_path}")
        print(f"Số dòng MariaDB báo ảnh hưởng: {result.rowcount}")
        print("Đã hoàn tất đồng bộ.")

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()

def sync_latest_price(symbol: str):
    """Đồng bộ giá phiên gần nhất của một mã cổ phiếu."""

    symbol = symbol.strip().upper()
    end_date = date.today()
    start_date = end_date - timedelta(days=10)

    db = SessionLocal()

    try:
        stock = db.scalar(
            select(Stock).where(Stock.symbol == symbol)
        )

        if stock is None:
            print(f"[PRICE] {symbol}: chưa có trong bảng stocks")
            return False

        quote = Quote(symbol=symbol, source="VCI")

        df = quote.history(
            start=str(start_date),
            end=str(end_date),
            interval="1D",
        )

        if df is None or df.empty:
            print(f"[PRICE] {symbol}: không có dữ liệu")
            return False

        df = df.sort_values("time")
        latest = df.iloc[-1]

        trading_date = pd.to_datetime(latest["time"]).date()

        def number(value):
            if pd.isna(value):
                return None
            return float(value)

        row = {
            "stock_id": stock.id,
            "trading_date": trading_date,
            "open_price": number(latest.get("open")),
            "high_price": number(latest.get("high")),
            "low_price": number(latest.get("low")),
            "close_price": number(latest.get("close")),
            "adjusted_close": number(latest.get("close")),
            "volume": (
                int(latest["volume"])
                if pd.notna(latest.get("volume"))
                else None
            ),
            "value_traded": number(latest.get("value")),
        }

        stmt = mysql_insert(StockPrice.__table__).values([row])

        stmt = stmt.on_duplicate_key_update(
            open_price=stmt.inserted.open_price,
            high_price=stmt.inserted.high_price,
            low_price=stmt.inserted.low_price,
            close_price=stmt.inserted.close_price,
            adjusted_close=stmt.inserted.adjusted_close,
            volume=stmt.inserted.volume,
            value_traded=stmt.inserted.value_traded,
        )

        db.execute(stmt)
        db.commit()

        print(
            f"[PRICE] {symbol}: {trading_date}, "
            f"close={row['close_price']}"
        )

        return True

    except Exception:
        db.rollback()
        logging.exception("[PRICE] Lỗi đồng bộ %s", symbol)
        return False

    finally:
        db.close()



def sync_financial_ratios(symbol: str):
    """Đồng bộ EPS, P/E, ROE, ROA, ROIC theo năm."""

    symbol = symbol.strip().upper()
    current_year = date.today().year

    target_years = [
        str(year)
        for year in range(current_year - 8, current_year)
    ]

    db = SessionLocal()

    try:
        stock = db.scalar(
            select(Stock).where(Stock.symbol == symbol)
        )

        if stock is None:
            print(f"[RATIO] {symbol}: chưa có trong bảng stocks")
            return False

        financial = Fundamental().equity(symbol)

        df = financial.ratio(
            period="year",
            lang="vi",
            format="long",
        )

        if df is None or df.empty:
            print(f"[RATIO] {symbol}: không có dữ liệu")
            return False

        required = {"item", "item_id"}

        if not required.issubset(df.columns):
            print(f"[RATIO] Cột không hợp lệ: {df.columns.tolist()}")
            return False

        # Chuyển các chỉ tiêu thành từ điển:
        # item_id -> {năm: giá trị}
        df = df.copy()
        df["item_id"] = df["item_id"].astype(str).str.strip()

        metrics = df.set_index("item_id")

        financial_rows = []
        valuation_rows = []

        for year in target_years:
            if year not in df.columns:
                continue

            def get_value(item_id):
                if item_id not in metrics.index:
                    return None

                value = metrics.loc[item_id, year]

                # Nếu có nhiều dòng trùng item_id, bỏ qua để tránh
                # vô tình chọn sai chỉ tiêu.
                if isinstance(value, pd.Series):
                    return None

                if pd.isna(value):
                    return None

                return float(value)

            eps = get_value("trailing_eps")
            roe = get_value("roe")
            roa = get_value("roa")
            pe = get_value("pe_ratio")
            pb = get_value("pb_ratio")
            ps = get_value("ps_ratio")

            # Dữ liệu mẫu không có ROIC.
            roic = get_value("roic")

            period_end = date(int(year), 12, 31)

            if any(v is not None for v in [eps, roe, roa, roic]):
                financial_rows.append({
                    "stock_id": stock.id,
                    "period_end": period_end,
                    "period_type": "year",
                    "eps": eps,
                    "roe": roe,
                    "roa": roa,
                    "roic": roic,
                    "source": "VCI",
                })

            if any(v is not None for v in [pe, pb, ps]):
                valuation_rows.append({
                    "stock_id": stock.id,
                    "valuation_date": period_end,
                    "period_type": "year",
                    "pe": pe,
                    "pb": pb,
                    "ps": ps,
                    "source": "VCI",
                })

            print(
                f"[RATIO] {symbol} {year}: "
                f"EPS={eps}, P/E={pe}, ROE={roe}, "
                f"ROA={roa}, ROIC={roic}, P/B={pb}, P/S={ps}"
            )

        if financial_rows:
            stmt = mysql_insert(
                StockFinancial.__table__
            ).values(financial_rows)

            stmt = stmt.on_duplicate_key_update(
                eps=stmt.inserted.eps,
                roe=stmt.inserted.roe,
                roa=stmt.inserted.roa,
                roic=stmt.inserted.roic,
                source=stmt.inserted.source,
                updated_at=text("CURRENT_TIMESTAMP"),
            )

            db.execute(stmt)

        if valuation_rows:
            stmt = mysql_insert(
                StockValuation.__table__
            ).values(valuation_rows)

            stmt = stmt.on_duplicate_key_update(
                pe=stmt.inserted.pe,
                pb=stmt.inserted.pb,
                ps=stmt.inserted.ps,
                source=stmt.inserted.source,
                updated_at=text("CURRENT_TIMESTAMP"),
            )

            db.execute(stmt)

        db.commit()

        print(
            f"[RATIO] {symbol}: đã lưu "
            f"{len(financial_rows)} bản ghi tài chính, "
            f"{len(valuation_rows)} bản ghi định giá"
        )

        return True

    except Exception:
        db.rollback()
        logging.exception("[RATIO] Lỗi đồng bộ %s", symbol)
        return False

    finally:
        db.close()


def sync_all_market_data(limit=None, delay=1.5):
    """Đồng bộ giá và tỷ số tài chính của các mã đã lưu trong stocks."""

    db = SessionLocal()

    try:
        stmt = select(Stock.symbol).order_by(Stock.symbol)

        if limit is not None:
            stmt = stmt.limit(limit)

        symbols = db.scalars(stmt).all()
    finally:
        db.close()

    total = len(symbols)
    print(f"Bắt đầu đồng bộ dữ liệu cho {total} mã")

    for index, symbol in enumerate(symbols, start=1):
        print(f"\n[{index}/{total}] {symbol}")

        sync_latest_price(symbol)
        time.sleep(delay)

        sync_financial_ratios(symbol)
        time.sleep(delay)

    print("\nĐã hoàn tất quá trình đồng bộ.")



def sync_quarterly_financial_ratios(symbol: str, year: int):
    """Đồng bộ EPS, P/E, ROE, ROA, ROIC theo quý của một năm."""

    import re

    symbol = symbol.strip().upper()
    db = SessionLocal()

    try:
        stock = db.scalar(
            select(Stock).where(Stock.symbol == symbol)
        )

        if stock is None:
            print(f"[RATIO] {symbol}: chưa có trong bảng stocks")
            return False

        financial = Fundamental().equity(symbol)

        df = financial.ratio(
            period="quarter",
            lang="vi",
            format="long",
        )

        if df is None or df.empty:
            print(f"[RATIO] {symbol}: không có dữ liệu")
            return False

        required = {"item", "item_id"}
        if not required.issubset(df.columns):
            print(f"[RATIO] Cột không hợp lệ: {df.columns.tolist()}")
            return False

        # Chỉ lấy cột đúng định dạng YYYY-Q1 đến YYYY-Q4.
        # Loại bỏ các cột trùng được Pandas thêm hậu tố _1, _2...
        quarter_cols = [
            col for col in df.columns
            if re.fullmatch(
                rf"{year}-Q[1-4]",
                str(col),
            )
        ]

        if not quarter_cols:
            print(
                f"[RATIO] {symbol}: không tìm thấy dữ liệu "
                f"quý năm {year}. Các cột: {df.columns.tolist()}"
            )
            return False

        df = df.copy()
        df["item_id"] = df["item_id"].astype(str).str.strip()

        metrics = df.set_index("item_id")

        def get_value(item_id, quarter_col):
            if item_id not in metrics.index:
                return None

            value = metrics.loc[item_id, quarter_col]

            # Không chọn tùy tiện nếu có nhiều dòng cùng item_id.
            if isinstance(value, pd.Series):
                return None

            if pd.isna(value):
                return None

            return float(value)

        financial_rows = []
        valuation_rows = []

        for quarter_col in sorted(quarter_cols):
            quarter = int(quarter_col[-1])

            # Ngày cuối quý.
            period_end = date(
                year,
                quarter * 3 + 1 if quarter < 4 else 12,
                1,
            )

            if quarter < 4:
                from calendar import monthrange
                month = quarter * 3
                period_end = date(
                    year,
                    month,
                    monthrange(year, month)[1],
                )
            else:
                period_end = date(year, 12, 31)

            eps = get_value("trailing_eps", quarter_col)
            roe = get_value("roe", quarter_col)
            roa = get_value("roa", quarter_col)
            roic = get_value("roic", quarter_col)

            pe = get_value("pe_ratio", quarter_col)
            pb = get_value("pb_ratio", quarter_col)
            ps = get_value("ps_ratio", quarter_col)

            if any(v is not None for v in [eps, roe, roa, roic]):
                financial_rows.append({
                    "stock_id": stock.id,
                    "period_end": period_end,
                    "period_type": "quarter",
                    "fiscal_quarter": quarter,
                    "fiscal_quarter_txt": quarter_col,
                    "eps": eps,
                    "roe": roe,
                    "roa": roa,
                    "roic": roic,
                    "source": "VCI",
                })

            if any(v is not None for v in [pe, pb, ps]):
                valuation_rows.append({
                    "stock_id": stock.id,
                    "valuation_date": period_end,
                    "period_type": "quarter",
                    "fiscal_quarter": quarter,
                    "fiscal_quarter_txt": quarter_col,
                    "pe": pe,
                    "pb": pb,
                    "ps": ps,
                    "source": "VCI",
                })

            print(
                f"[RATIO] {symbol} {quarter_col}: "
                f"EPS={eps}, P/E={pe}, ROE={roe}, "
                f"ROA={roa}, ROIC={roic}, P/B={pb}, P/S={ps}"
            )

        if financial_rows:
            stmt = mysql_insert(
                StockFinancial.__table__
            ).values(financial_rows)

            stmt = stmt.on_duplicate_key_update(
                eps=stmt.inserted.eps,
                roe=stmt.inserted.roe,
                roa=stmt.inserted.roa,
                roic=stmt.inserted.roic,
                source=stmt.inserted.source,
                updated_at=text("CURRENT_TIMESTAMP"),
            )

            db.execute(stmt)

        if valuation_rows:
            stmt = mysql_insert(
                StockValuation.__table__
            ).values(valuation_rows)

            stmt = stmt.on_duplicate_key_update(
                pe=stmt.inserted.pe,
                pb=stmt.inserted.pb,
                ps=stmt.inserted.ps,
                source=stmt.inserted.source,
                updated_at=text("CURRENT_TIMESTAMP"),
            )

            db.execute(stmt)

        db.commit()

        print(
            f"[RATIO] {symbol} năm {year}: đã lưu "
            f"{len(financial_rows)} bản ghi tài chính, "
            f"{len(valuation_rows)} bản ghi định giá"
        )

        return True

    except Exception:
        db.rollback()
        logging.exception(
            "[RATIO] Lỗi đồng bộ quý %s năm %s",
            symbol,
            year,
        )
        return False

    finally:
        db.close()



def sync_quarterly_all_market_data(year: int, limit=None, delay=1.5):
    """Đồng bộ giá mới nhất và ratio theo quý của một năm."""

    db = SessionLocal()

    try:
        stmt = select(Stock.symbol).order_by(Stock.symbol)

        if limit is not None:
            stmt = stmt.limit(limit)

        symbols = db.scalars(stmt).all()

    finally:
        db.close()

    total = len(symbols)
    print(f"Bắt đầu đồng bộ {total} mã cho năm {year}")

    for index, symbol in enumerate(symbols, start=1):
        print(f"\n[{index}/{total}] {symbol}")

        try:
            sync_latest_price(symbol)
            time.sleep(delay)

            sync_quarterly_financial_ratios(symbol, year)
            time.sleep(delay)

        except Exception:
            logging.exception("[RATIO] Lỗi đồng bộ %s", symbol)

    print(f"\nĐã hoàn tất đồng bộ năm {year}.")


from sqlalchemy import delete
import argparse

def delete_table_data(table_names: list[str]):
    """
    Xóa toàn bộ dữ liệu trong các bảng được chỉ định.
    Chỉ cho phép xóa những bảng nằm trong danh sách an toàn.
    """

    table_models = {
        Stock.__tablename__: Stock,
        StockPrice.__tablename__: StockPrice,
        StockFinancialReport.__tablename__: StockFinancialReport,
        StockFinancial.__tablename__: StockFinancial,
        StockValuation.__tablename__: StockValuation,
    }

    if not table_names:
        raise ValueError("Bạn chưa chỉ định bảng cần xóa.")

    # Kiểm tra tất cả bảng trước khi thực hiện xóa.
    invalid_tables = set(table_names) - set(table_models.keys())

    if invalid_tables:
        raise ValueError(
            f"Bảng không được phép xóa: {sorted(invalid_tables)}. "
            f"Các bảng hợp lệ: {sorted(table_models.keys())}"
        )

    db = SessionLocal()

    try:
        for table_name in table_names:
            model = table_models[table_name]

            result = db.execute(delete(model))
            print(
                f"[DELETE] {table_name}: "
                f"{result.rowcount} dòng"
            )

        db.commit()
        print("[DELETE] Xóa dữ liệu thành công.")

    except Exception:
        db.rollback()
        logging.exception("[DELETE] Lỗi xóa dữ liệu")
        raise

    finally:
        db.close()



def main():
    parser = argparse.ArgumentParser(
        description="StockMate - Đồng bộ dữ liệu chứng khoán"
    )

    parser.add_argument(
        "--type",
        type=int,
        required=True,
        choices=[1, 2, 3, 4],
        help="1: Danh mục, 2: Dữ liệu năm, "
             "3: Dữ liệu quý, 4: Xóa dữ liệu bảng",
    )

    parser.add_argument("--start-year", type=int, default=2018)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--delay", type=float, default=5)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--tables",
        type=str,
        default="",
        help="Tên các bảng cần xóa, phân cách bằng dấu phẩy",
    )

    args = parser.parse_args()

    if args.type == 1:
        sync_stocks()

    elif args.type == 2:
        sync_all_market_data(
            limit=args.limit,
            delay=args.delay,
        )

    elif args.type == 3:
        if args.start_year > args.end_year:
            parser.error("--start-year phải <= --end-year")

        for year in range(args.start_year, args.end_year + 1):
            print(f"\n{'=' * 50}")
            print(f"ĐỒNG BỘ DỮ LIỆU QUÝ NĂM {year}")
            print(f"{'=' * 50}")

            sync_quarterly_all_market_data(
                year=year,
                limit=args.limit,
                delay=args.delay,
            )

    elif args.type == 4:
        if not args.tables.strip():
            parser.error(
                "Type 4 yêu cầu --tables để chỉ định bảng cần xóa."
            )

        table_names = [
            name.strip()
            for name in args.tables.split(",")
            if name.strip()
        ]

        # Xác nhận trước khi xóa dữ liệu.
        confirm = input(
            f"Xác nhận xóa toàn bộ dữ liệu trong "
            f"{table_names}? Nhập YES để tiếp tục: "
        )

        if confirm == "YES":
            delete_table_data(table_names)
        else:
            print("Đã hủy thao tác xóa.")


if __name__ == "__main__":
    main()

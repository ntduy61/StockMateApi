import json

import pandas as pd
from vnstock import Fundamental, Market, Reference


def dataframe_to_records(df):
    """Chuyển DataFrame thành danh sách JSON-compatible."""
    if df is None or df.empty:
        return []

    clean_df = df.astype(object).where(pd.notna(df), None)
    return json.loads(clean_df.to_json(
        orient="records",
        date_format="iso",
        force_ascii=False,
    ))


def get_stock_catalog():
    """Danh sách cổ phiếu kèm sàn giao dịch."""
    df = Reference().equity.list_by_exchange()
    return dataframe_to_records(df)


def get_company_overview(symbol: str):
    """Thông tin tổng quan doanh nghiệp."""
    symbol = symbol.strip().upper()

    df = Reference().company(symbol).info()

    return dataframe_to_records(df)


def get_stock_quote(symbol: str):
    """Bảng giá mới nhất của một mã."""
    df = Market().equity(symbol.upper()).quote()
    return dataframe_to_records(df)


def get_stock_history(symbol: str, start: str, end: str):
    """Lịch sử giá theo ngày."""
    df = Market().equity(symbol.upper()).ohlcv(
        start=start,
        end=end,
        interval="1D",
    )
    return dataframe_to_records(df)



def get_financial_report(
    symbol: str,
    report_type: str,
    period_type: str,
):
    """Lấy báo cáo tài chính trong 1 năm gần nhất."""

    import logging
    import pandas as pd
    from datetime import date

    from vnstock import Fundamental

    logger = logging.getLogger("stockmate.sync")

    report_functions = {
        "income_statement": "income_statement",
        "balance_sheet": "balance_sheet",
        "cash_flow": "cash_flow",
        "ratio": "ratio",
    }

    if report_type not in report_functions:
        raise ValueError(
            f"Loại báo cáo không hợp lệ: {report_type}"
        )

    if period_type not in ("year", "quarter"):
        raise ValueError(
            f"Kỳ báo cáo không hợp lệ: {period_type}"
        )

    financial = Fundamental().equity(symbol.upper())

    report_func = getattr(
        financial,
        report_functions[report_type],
    )

    df = report_func(
        period=period_type,
        lang="vi",
        format="long",
    )

    if df is None or df.empty:
        return []

    # Kiểm tra metadata giới hạn dữ liệu trước khi xử lý.
    attrs = getattr(df, "attrs", {}) or {}

    cap_info = {
        key: value
        for key, value in attrs.items()
        if key.startswith("tier_cap_")
    }

    if cap_info:
        logger.warning(
            "%s %s/%s bị giới hạn dữ liệu VNStock: %s",
            symbol,
            report_type,
            period_type,
            cap_info,
        )

    # Chỉ lấy dữ liệu trong 1 năm gần nhất.
    if "period" in df.columns:
        current_year = date.today().year
        start_year = current_year - 1

        period_year = pd.to_numeric(
            df["period"].astype(str).str[:4],
            errors="coerce",
        )

        df = df.loc[
            period_year.between(start_year, current_year)
        ].copy()

    logger.info(
        "%s %s/%s: lấy được %s dòng",
        symbol,
        report_type,
        period_type,
        len(df),
    )

    if not df.empty and "period" in df.columns:
        logger.info(
            "Các kỳ báo cáo: %s",
            df["period"].unique().tolist(),
        )

    return dataframe_to_records(df)

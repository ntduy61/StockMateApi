
# app/tasks/sync_news.py

import argparse
import hashlib
import logging
import re
import time

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import (
    urljoin,
    urlsplit,
    urlunsplit,
    parse_qsl,
    urlencode,
)

import requests
from bs4 import BeautifulSoup

from sqlalchemy import text
from app.db.database import get_db


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

CAFEF_API_URL = (
    "https://cafef.vn/du-lieu/Ajax/PageNew/News.ashx"
)
CAFEF_BASE_URL = "https://cafef.vn"

MAX_WORKERS = 8
DEFAULT_PAGE_SIZE = 10
DEFAULT_PAGES = 1

REQUEST_TIMEOUT = 20
REQUEST_DELAY = 0.1

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "application/json, text/html, "
        "application/xhtml+xml, */*"
    ),
    "Referer": "https://cafef.vn/",
}

# 0	Tất cả	tat-ca
# 1	Tình hình SXKD & Phân tích khác	sxkd-phan-tich
# 2	Trả cổ tức & Chốt quyền	tra-co-tuc
# 3	Thay đổi nhân sự	thay-doi-nhan-su
# 4	Tăng vốn - Cổ phiếu quỹ	tang-von-co-phieu-quy
# 5	GD cổ đông lớn & Cổ đông nội bộ	gd-co-dong-lon-co-dong-noi-bo
CAFEF_NEWS_TYPES = {
    0: "tat-ca",
    1: "sxkd-phan-tich",
    2: "tra-co-tuc",
    3: "thay-doi-nhan-su",
    4: "tang-von-co-phieu-quy",
    5: "gd-co-dong-lon-co-dong-noi-bo",
}

# Danh mục mặc định.
# Các slug phải tồn tại trong news_categories.

CATEGORY_RULES = [
    (
        "tat-ca",
        [
            "tất cả"
        ],
    ),
    (
        "sxkd-phan-tich",
        [
            "tình hình SXKD & Phân tích khác"
        ],
    ),
    (
        "tra-co-tuc",
        [
            "trả cổ tức & Chốt quyền"
        ],
    ),
    (
        "thay-doi-nhan-su",
        [
            "thay đổi nhân sự"
        ],
    ),
    (
        "tang-von-co-phieu-quy",
        [
            "tăng vốn - Cổ phiếu quỹ"
        ],
    ),
    (
        "gd-co-dong-lon-co-dong-noi-bo",
        [
            "GD cổ đông lớn & Cổ đông nội bộ"
        ],
    ),
]


def normalize_url(url: str) -> str:
    """Chuẩn hóa URL, loại bỏ tham số tracking."""
    absolute = urljoin(CAFEF_BASE_URL, url.strip())
    parts = urlsplit(absolute)

    query = [
        (key, value)
        for key, value in parse_qsl(
            parts.query,
            keep_blank_values=True,
        )
        if not key.lower().startswith("utm_")
    ]

    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query),
            "",
        )
    )


def make_url_hash(url: str) -> str:
    return hashlib.sha256(
        url.encode("utf-8")
    ).hexdigest()


def parse_cafef_date(value):
    """
    Chuyển /Date(1792577625000)/ thành datetime.
    CafeF trả timestamp theo mili giây.
    """
    if not value:
        return None

    if isinstance(value, datetime):
        return value.replace(tzinfo=None)

    match = re.search(r"/Date\((-?\d+)", str(value))

    if match:
        timestamp = int(match.group(1)) / 1000
        return datetime.fromtimestamp(
            timestamp,
            tz=timezone.utc,
        ).replace(tzinfo=None)

    try:
        parsed = parsedate_to_datetime(str(value))
        return parsed.replace(tzinfo=None)
    except (TypeError, ValueError, OverflowError):
        pass

    try:
        return datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        ).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def get_json(url, params=None):
    """Gọi API CafeF."""
    response = requests.get(
        url,
        params=params,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()



def get_news_list(
    symbol: str,
    news_type: int = 0,
    page_index: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
):
    """Lấy danh sách tin theo mã cổ phiếu và loại tin."""

    try:
        result = get_json(
            CAFEF_API_URL,
            params={
                "symbol": symbol.lower(),
                "NewsType": news_type,
                "pageIndex": page_index,
                "pageSize": page_size,
            },
        )

        if not result.get("Success"):
            logging.warning(
                "CafeF Success=false: %s, type=%s, page=%s",
                symbol,
                news_type,
                page_index,
            )
            return []

        data = result.get("Data") or []

        if not isinstance(data, list):
            return []

        news_list = []

        for item in data:
            title = (item.get("Title") or "").strip()
            link = item.get("LinkDetail")

            if not title or not link:
                continue

            source_url = normalize_url(link)

            news_list.append({
                "stock_symbol": symbol.upper(),
                "title": title,
                "summary": (
                    item.get("SubTitle") or ""
                ).strip() or None,
                "source": "CafeF",
                "source_url": source_url,
                "source_url_hash": make_url_hash(
                    source_url
                ),
                "published_at": parse_cafef_date(
                    item.get("DeployDate")
                ),
                "thumbnail_url": item.get("Image") or None,
                "content_type": "article",

                # Lưu loại tin API CafeF trả về.
                "cafef_news_type": news_type,
            })

        return news_list

    except (
        requests.RequestException,
        ValueError,
        TypeError,
    ):
        logging.exception(
            "Lỗi lấy tin %s, NewsType=%s, page=%s",
            symbol,
            news_type,
            page_index,
        )
        return []



def fetch_news_detail(source_url: str) -> dict:
    """
    Lấy nội dung chi tiết tin CafeF.

    Hỗ trợ:
    - Tin công bố thông tin: #newscontent
    - Tin CafeF thông thường: .detail-content.afcbc-body[data-role='content']

    Return:
    {
        "content": str,
        "source_name": str | None,
        "attachments": list[dict]
    }
    """

    result = {
        "content": None,
        "source_name": None,
        "attachments": [],
    }

    if not source_url:
        return result

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    try:
        response = requests.get(
            source_url,
            headers=headers,
            timeout=20,
        )
        response.raise_for_status()
        response.encoding = response.apparent_encoding

        soup = BeautifulSoup(response.text, "html.parser")

        # --------------------------------------------------
        # 1. Tìm vùng nội dung bài viết
        # --------------------------------------------------

        news_content = soup.select_one("#newscontent")

        # CafeF article thông thường
        if not news_content:
            news_content = soup.select_one(
                ".detail-content.afcbc-body[data-role='content']"
            )

        # Fallback cho một số cấu trúc CafeF khác
        if not news_content:
            news_content = soup.select_one(
                ".detail-content"
            )

        if not news_content:
            logging.warning(
                "Không tìm thấy vùng nội dung bài viết: %s",
                source_url,
            )
            return result

        # --------------------------------------------------
        # 2. Lấy nội dung tin công bố thông tin
        # --------------------------------------------------

        disclosure_content = news_content.select_one(".pa-hose")

        if not disclosure_content:
            disclosure_content = news_content.select_one(
                ".desc.unreset"
            )

        if disclosure_content:
            content_container = disclosure_content
        else:
            content_container = news_content

        # --------------------------------------------------
        # 3. Xóa các thành phần không phải nội dung bài viết
        # --------------------------------------------------

        for element in content_container.select(
            "script, style, iframe, noscript"
        ):
            element.decompose()

        # Xóa các thành phần quảng cáo / bài liên quan
        for element in content_container.select(
            ".pushed, .related-news, .box-related, "
            ".ads, .advertisement, .banner"
        ):
            element.decompose()

        # --------------------------------------------------
        # 4. Lấy nội dung text
        # --------------------------------------------------

        # paragraphs = []

        # for element in content_container.select("p"):
        #     text = element.get_text(" ", strip=True)

        #     if not text:
        #         continue

        #     # Bỏ các đoạn chỉ có nội dung quảng cáo hoặc điều hướng
        #     lower_text = text.lower()

        #     if (
        #         "xem thêm" in lower_text
        #         or "đọc thêm" in lower_text
        #         or "tin liên quan" in lower_text
        #     ):
        #         continue

        #     paragraphs.append(text)

        # # Nếu bài không dùng thẻ p, lấy toàn bộ text
        # if paragraphs:
        #     content = "\n\n".join(paragraphs)
        # else:
        #     content = content_container.get_text(
        #         "\n",
        #         strip=True,
        #     )

        # result["content"] = content.strip() if content else None

        # Lấy toàn bộ HTML bên trong vùng nội dung bài viết
        result["content"] = content_container.decode_contents().strip()
        
        # --------------------------------------------------
        # 5. Lấy tên nguồn tin
        # --------------------------------------------------

        source_element = soup.select_one("#originSource")

        if source_element:
            source_name = source_element.get_text(
                " ",
                strip=True,
            )

            if source_name:
                result["source_name"] = source_name

        # --------------------------------------------------
        # 6. Lấy file đính kèm
        # --------------------------------------------------

        attachment_links = []

        # File đính kèm thường nằm trong FileWrapper
        attachment_links.extend(
            soup.select(".FileWrapper a[href]")
        )

        # Một số tin có link PDF trực tiếp trong nội dung
        attachment_links.extend(
            content_container.select("a[href]")
        )

        # Loại link trùng
        seen_urls = set()

        for link in attachment_links:
            href = link.get("href")

            if not href:
                continue

            file_url = urljoin(source_url, href.strip())

            if file_url in seen_urls:
                continue

            seen_urls.add(file_url)

            file_name = (
                link.get_text(" ", strip=True)
                or file_url.rsplit("/", 1)[-1]
            )

            lower_url = file_url.lower().split("?")[0]

            if lower_url.endswith(".pdf"):
                file_type = "pdf"
            elif lower_url.endswith((".doc", ".docx")):
                file_type = "doc"
            elif lower_url.endswith((".xls", ".xlsx")):
                file_type = "xls"
            elif lower_url.endswith((".jpg", ".jpeg", ".png")):
                file_type = "image"
            else:
                continue

            result["attachments"].append({
                "file_name": file_name,
                "file_url": file_url,
                "file_type": file_type,
            })

        # --------------------------------------------------
        # 7. Fallback: lấy link PDF từ iframe
        # --------------------------------------------------

        for iframe in soup.select("iframe[src]"):
            iframe_url = urljoin(
                source_url,
                iframe.get("src", "").strip(),
            )

            lower_url = iframe_url.lower().split("?")[0]

            if not lower_url.endswith(".pdf"):
                continue

            if iframe_url in seen_urls:
                continue

            seen_urls.add(iframe_url)

            result["attachments"].append({
                "file_name": iframe_url.rsplit("/", 1)[-1],
                "file_url": iframe_url,
                "file_type": "pdf",
            })

        logging.info(
            "Đã lấy nội dung tin: %s | content_length=%s | attachments=%s",
            source_url,
            len(result["content"] or ""),
            len(result["attachments"]),
        )

        return result

    except requests.RequestException as exc:
        logging.exception(
            "Lỗi request khi lấy tin %s: %s",
            source_url,
            exc,
        )

    except Exception as exc:
        logging.exception(
            "Lỗi parse nội dung tin %s: %s",
            source_url,
            exc,
        )

    return result


def get_category_slug(title: str, summary: str):
    """Phân loại sơ bộ theo tiêu đề và mô tả."""
    content = f"{title or ''} {summary or ''}".lower()

    for slug, keywords in CATEGORY_RULES:
        if any(keyword in content for keyword in keywords):
            return slug

    return "tin-doanh-nghiep"


def fetch_stock_news(
    symbol: str,
    pages: int,
    page_size: int,
):
    """Lấy tin theo cả 6 NewsType cho một mã."""
    all_news = []

    for news_type in CAFEF_NEWS_TYPES:
        for page_index in range(1, pages + 1):
            news_list = get_news_list(
                symbol=symbol,
                news_type=news_type,
                page_index=page_index,
                page_size=page_size,
            )

            all_news.extend(news_list)

            logging.info(
                "%s | NewsType=%s (%s) | page=%s | "
                "nhận=%s bài",
                symbol,
                news_type,
                CAFEF_NEWS_TYPES[news_type],
                page_index,
                len(news_list),
            )

            if len(news_list) < page_size:
                break

            if REQUEST_DELAY:
                time.sleep(REQUEST_DELAY)

    return all_news


def sync_news(
    pages: int = DEFAULT_PAGES,
    page_size: int = DEFAULT_PAGE_SIZE,
    workers: int = MAX_WORKERS,
):
    """Đồng bộ tin CafeF vào MariaDB."""
    db = next(get_db())

    try:
        # Kiểm tra các bảng cần thiết.
        tables = {
            row[0]
            for row in db.execute(
                text("SHOW TABLES")
            ).all()
        }

        required = {
            "stocks",
            "news",
            "news_categories",
        }

        missing = required - tables

        if missing:
            raise RuntimeError(
                "Thiếu bảng database: "
                + ", ".join(sorted(missing))
            )

        has_attachments = (
            "news_attachments" in tables
        )

        news_columns = {
            row[0]
            for row in db.execute(
                text("SHOW COLUMNS FROM news")
            ).all()
        }

        has_hash = "source_url_hash" in news_columns

        required_columns = {
            "category_id",
            "stock_symbol",
            "title",
            "summary",
            "content",
            "source",
            "source_url",
            "source_name",
            "published_at",
            "thumbnail_url",
            "content_type",
        }

        missing_columns = (
            required_columns - news_columns
        )

        if missing_columns:
            raise RuntimeError(
                "Bảng news thiếu cột: "
                + ", ".join(sorted(missing_columns))
            )

        categories = db.execute(
            text("""
                SELECT id, slug
                FROM news_categories
                WHERE is_active = 1
            """)
        ).mappings().all()

        category_map = {
            row["slug"]: row["id"]
            for row in categories
        }

       

        # Kiểm tra các danh mục cần thiết đã tồn tại trong DB chưa
        missing_categories = [
            slug
            for slug in CAFEF_NEWS_TYPES.values()
            if slug not in category_map
        ]

        if missing_categories:
            raise RuntimeError(
                "Chưa có các danh mục trong news_categories: "
                + ", ".join(missing_categories)
            )

        stocks = db.execute(
            text("""
                SELECT symbol
                FROM stocks
                WHERE symbol IS NOT NULL
                ORDER BY symbol
            """)
        ).mappings().all()

        symbols = [
            row["symbol"].strip().upper()
            for row in stocks
            if row["symbol"] and row["symbol"].strip()
        ]

        logging.info(
            "Bắt đầu đồng bộ tin: %s mã, pages=%s, "
            "page_size=%s, workers=%s",
            len(symbols),
            pages,
            page_size,
            workers,
        )

        # Lấy dữ liệu bằng worker; chỉ luồng chính
        # thao tác với database.
        all_news = []

        with ThreadPoolExecutor(
            max_workers=workers
        ) as executor:
            futures = {
                executor.submit(
                    fetch_stock_news,
                    symbol,
                    pages,
                    page_size,
                ): symbol
                for symbol in symbols
            }

            for future in as_completed(futures):
                symbol = futures[future]

                try:
                    items = future.result()
                    all_news.extend(items)

                    logging.info(
                        "%s: nhận được %s bài",
                        symbol,
                        len(items),
                    )

                except Exception:
                    logging.exception(
                        "Lỗi lấy tin mã %s",
                        symbol,
                    )

        # Một URL có thể xuất hiện trong nhiều mã.
        # Gộp lại để không tạo bài trùng.
        deduplicated = {}

        for item in all_news:
            key = item["source_url_hash"]
            existing = deduplicated.get(key)

            if existing:
                symbols_found = set(
                    existing["stock_symbol"].split(",")
                )
                symbols_found.add(item["stock_symbol"])

                existing["stock_symbol"] = ",".join(
                    sorted(symbols_found)
                )

                if (
                    not existing.get("summary")
                    and item.get("summary")
                ):
                    existing["summary"] = item["summary"]
            else:
                deduplicated[key] = item.copy()

        all_news = list(deduplicated.values())

        logging.info(
            "Tổng bài sau khi loại trùng URL: %s",
            len(all_news),
        )

        
        insert_sql = text("""
                INSERT INTO news (
                    category_id,
                    stock_symbol,
                    title,
                    summary,
                    content,
                    source,
                    source_url,
                    source_name,
                    published_at,
                    thumbnail_url,
                    content_type
                )
                VALUES (
                    :category_id,
                    :stock_symbol,
                    :title,
                    :summary,
                    :content,
                    :source,
                    :source_url,
                    :source_name,
                    :published_at,
                    :thumbnail_url,
                    :content_type
                )
                ON DUPLICATE KEY UPDATE
                    stock_symbol = VALUES(stock_symbol),
                    title = VALUES(title),
                    summary = COALESCE(
                        VALUES(summary), summary
                    ),
                    content = COALESCE(
                        VALUES(content), content
                    ),
                    source_name = COALESCE(
                        VALUES(source_name), source_name
                    ),
                    published_at = COALESCE(
                        VALUES(published_at), published_at
                    ),
                    thumbnail_url = COALESCE(
                        VALUES(thumbnail_url), thumbnail_url
                    ),
                    category_id = COALESCE(
                        VALUES(category_id), category_id
                    )
            """)
        

        attachment_insert_sql = text("""
            INSERT INTO news_attachments (
                news_id,
                file_name,
                file_url,
                file_type
            )
            VALUES (
                :news_id,
                :file_name,
                :file_url,
                :file_type
            )
        """)

        # Lấy danh sách bài hiện tại để chỉ tải
        # chi tiết bài mới hoặc bài chưa có nội dung.
        existing_rows = db.execute(
            text("""
                SELECT id, source_url, content
                FROM news
                WHERE source = 'CafeF'
            """)
        ).mappings().all()

        existing_by_url = {
            normalize_url(row["source_url"]): row
            for row in existing_rows
        }

        inserted_or_updated = 0
        detail_success = 0
        errors = 0

        for index, item in enumerate(all_news, start=1):
            try:
                source_url = item["source_url"]
                existing = existing_by_url.get(source_url)

                # Nếu bài đã có nội dung, không cần tải lại
                # HTML mỗi lần cron chạy.
                detail = {
                    "content": (
                        existing["content"]
                        if existing
                        else None
                    ),
                    "source_name": None,
                    "attachments": [],
                }

                if not existing or not existing["content"]:
                    detail = fetch_news_detail(source_url)

                    if detail.get("content"):
                        detail_success += 1

                    if REQUEST_DELAY:
                        time.sleep(REQUEST_DELAY)

                news_type = item.get("cafef_news_type", 0)
                category_slug = CAFEF_NEWS_TYPES.get(news_type, "tat-ca")

                category_id = category_map.get(category_slug)

                if category_id is None:
                    raise RuntimeError(
                        f"Không tìm thấy danh mục '{category_slug}' "
                        f"cho NewsType={news_type}"
                    )

                params = {
                    **item,
                    "category_id": category_id,
                    "content": detail.get("content"),
                    "source_name": detail.get("source_name"),
                }

                db.execute(insert_sql, params)
                db.flush()

                # Tìm ID bài viết sau INSERT/UPDATE.
                if has_hash:
                    saved = db.execute(
                        text("""
                            SELECT id
                            FROM news
                            WHERE source_url_hash = :url_hash
                        """),
                        {
                            "url_hash": item["source_url_hash"]
                        },
                    ).first()
                else:
                    saved = db.execute(
                        text("""
                            SELECT id
                            FROM news
                            WHERE source_url = :source_url
                            LIMIT 1
                        """),
                        {"source_url": source_url},
                    ).first()

                if saved and has_attachments:
                    # Tránh lặp file mỗi lần đồng bộ.
                    db.execute(
                        text("""
                            DELETE FROM news_attachments
                            WHERE news_id = :news_id
                        """),
                        {"news_id": saved.id},
                    )

                    attachments = detail.get("attachments", [])

                    if attachments:
                        db.execute(
                            attachment_insert_sql,
                            [
                                {
                                    "news_id": saved.id,
                                    **attachment,
                                }
                                for attachment in attachments
                            ],
                        )

                inserted_or_updated += 1

                # Commit định kỳ để hạn chế transaction dài.
                if index % 50 == 0:
                    db.commit()

                    logging.info(
                        "Tiến độ %s/%s | xử lý=%s | "
                        "chi tiết tải được=%s",
                        index,
                        len(all_news),
                        inserted_or_updated,
                        detail_success,
                    )

            except Exception:
                db.rollback()
                errors += 1

                logging.exception(
                    "Lỗi lưu tin: %s",
                    item.get("source_url"),
                )

        db.commit()

        logging.info(
            "Hoàn tất đồng bộ CafeF | mã=%s | "
            "bài=%s | chi tiết=%s | lỗi=%s",
            len(symbols),
            inserted_or_updated,
            detail_success,
            errors,
        )

    except Exception:
        db.rollback()
        logging.exception("Lỗi đồng bộ tin CafeF")
        raise

    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Đồng bộ tin tức cổ phiếu từ CafeF"
    )

    parser.add_argument(
        "--pages",
        type=int,
        default=DEFAULT_PAGES,
        help="Số trang lấy cho mỗi mã",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help="Số tin trên mỗi trang",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=MAX_WORKERS,
        help="Số luồng lấy danh sách tin",
    )

    args = parser.parse_args()

    sync_news(
        pages=max(1, args.pages),
        page_size=max(1, args.page_size),
        workers=max(1, args.workers),
    )

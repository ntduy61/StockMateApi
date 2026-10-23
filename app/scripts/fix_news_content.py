
import logging
import time

from sqlalchemy import text

from app.db.database import SessionLocal

# Import hàm fetch_news_detail hiện tại của bạn
from app.scripts.sync_news import fetch_news_detail


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


def fix_news_content():
    db = SessionLocal()

    try:
        # ==========================================
        # 1. TÌM TIN CHƯA CÓ CONTENT
        # ==========================================

        news_list = db.execute(
            text("""
                SELECT
                    id,
                    source_url
                FROM news
                WHERE
                    content IS NULL
                    OR TRIM(content) = ''
                ORDER BY id ASC
            """)
        ).mappings().all()

        total = len(news_list)

        logging.info(
            "Tìm thấy %s tin tức cần cập nhật",
            total,
        )

        updated_count = 0
        attachment_count = 0
        failed_count = 0

        # ==========================================
        # 2. DUYỆT TỪNG TIN
        # ==========================================

        for index, news in enumerate(
            news_list,
            start=1,
        ):
            news_id = news["id"]
            source_url = news["source_url"]

            if not source_url:
                logging.warning(
                    "[%s/%s] News ID %s không có source_url",
                    index,
                    total,
                    news_id,
                )
                continue

            logging.info(
                "[%s/%s] Đang xử lý News ID: %s",
                index,
                total,
                news_id,
            )

            try:
                # Lấy nội dung và file đính kèm
                detail = fetch_news_detail(
                    source_url
                )

                content = detail.get("content")
                attachments = detail.get(
                    "attachments",
                    [],
                )

                # ==================================
                # 3. CẬP NHẬT CONTENT
                # ==================================

                if content and content.strip():
                    db.execute(
                        text("""
                            UPDATE news
                            SET content = :content
                            WHERE id = :news_id
                              AND (
                                  content IS NULL
                                  OR TRIM(content) = ''
                              )
                        """),
                        {
                            "content": content.strip(),
                            "news_id": news_id,
                        },
                    )

                    updated_count += 1

                    logging.info(
                        "News ID %s: cập nhật content thành công",
                        news_id,
                    )

                else:
                    logging.warning(
                        "News ID %s: không lấy được content",
                        news_id,
                    )

                # ==================================
                # 4. THÊM FILE ĐÍNH KÈM NẾU CHƯA CÓ
                # ==================================

                for attachment in attachments:
                    file_name = attachment.get(
                        "file_name"
                    )

                    file_url = attachment.get(
                        "file_url"
                    )

                    file_type = attachment.get(
                        "file_type",
                        "file",
                    )

                    if not file_url:
                        continue

                    # Kiểm tra file đã tồn tại
                    exists = db.execute(
                        text("""
                            SELECT 1
                            FROM news_attachments
                            WHERE news_id = :news_id
                              AND file_url = :file_url
                            LIMIT 1
                        """),
                        {
                            "news_id": news_id,
                            "file_url": file_url,
                        },
                    ).first()

                    if exists:
                        logging.info(
                            "File đã tồn tại, bỏ qua: %s",
                            file_url,
                        )
                        continue

                    # Thêm file mới
                    db.execute(
                        text("""
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
                        """),
                        {
                            "news_id": news_id,
                            "file_name": file_name,
                            "file_url": file_url,
                            "file_type": file_type,
                        },
                    )

                    attachment_count += 1

                    logging.info(
                        "Đã thêm file: %s",
                        file_name,
                    )

                # Commit từng bài viết
                db.commit()

            except Exception:
                db.rollback()

                failed_count += 1

                logging.exception(
                    "Lỗi xử lý News ID %s",
                    news_id,
                )

            # Tránh gửi request liên tục
            time.sleep(1)

        # ==========================================
        # 5. TỔNG KẾT
        # ==========================================

        logging.info(
            """
            ========== KẾT QUẢ ==========
            Tổng tin cần xử lý: %s
            Content đã cập nhật: %s
            File đính kèm đã thêm: %s
            Tin xử lý lỗi: %s
            =============================
            """,
            total,
            updated_count,
            attachment_count,
            failed_count,
        )

    finally:
        db.close()


if __name__ == "__main__":
    fix_news_content()

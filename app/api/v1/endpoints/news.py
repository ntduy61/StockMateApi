
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import User
from app.core.security import get_current_user

router = APIRouter()


def category_dict(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "slug": row["slug"],
        "description": row["description"],
        "is_active": row["is_active"],
        "created_at": str(row["created_at"])
        if row["created_at"] else None,
    }
def new_dict(row):
    return {
        "id": row["id"],
        "category_id": row["category_id"],
        "stock_symbol": row["stock_symbol"],
        "title": row["title"],
        "summary": row["summary"],
        "created_at": str(row["created_at"])
        if row["created_at"] else None,
    }

def attachment_dict(row):
    return {
        "id": row["id"],
        "news_id": row["news_id"],
        "file_name": row["file_name"],
        "file_url": row["file_url"],
        "file_type": row["file_type"],
        "created_at": str(row["created_at"])
        if row["created_at"] else None,
    }


@router.get("/api/news")
def get_notifications(
    category_id: int = Query(0, ge=0),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    conditions = ["category_id = :category_id"]

    params = {
        "category_id": category_id,
        "offset": (page - 1) * page_size,
        "page_size": page_size,
    }

    # Chuyển list thành chuỗi SQL
    where_clause = " AND ".join(conditions)

    total = db.execute(
        text(f"""
            SELECT COUNT(*)
            FROM stockmate.news
            WHERE {where_clause}
        """),
        params,
    ).scalar() or 0

    rows = db.execute(
        text(f"""
            SELECT
                id,
                category_id,
                stock_symbol,
                title,
                summary,
                created_at
            FROM stockmate.news
            WHERE {where_clause}
            ORDER BY created_at DESC, id DESC
            LIMIT :page_size OFFSET :offset
        """),
        params,
    ).mappings().all()

    return {
        "items": [new_dict(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/api/news/{news_id}")
def get_news_detail(
    news_id: int,
    db: Session = Depends(get_db),
):
    news = db.execute(
        text("""
        SELECT
            id,
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
            content_type,
            created_at
        FROM stockmate.news
        WHERE id = :news_id
        LIMIT 1
        """),
        {"news_id": news_id},
    ).mappings().first()

    if not news:
        raise HTTPException(
            status_code=404,
            detail="Không tìm thấy tin tức",
        )

    attachments = db.execute(
        text("""
            SELECT
                id,
                news_id,
                file_name,
                file_url,
                file_type,
                created_at
            FROM stockmate.news_attachments
            WHERE news_id = :news_id
            ORDER BY created_at DESC, id DESC
        """),
        {"news_id": news_id},
    ).mappings().all()

    return {
        "id": news["id"],
        "category_id": news["category_id"],
        "stock_symbol": news["stock_symbol"],
        "title": news["title"],
        "summary": news["summary"],
        "content": news["content"],
        "source": news["source"],
        "source_url": news["source_url"],
        "source_name": news["source_name"],
        "published_at": str(news["published_at"])
        if news["published_at"] else None,
        "thumbnail_url": news["thumbnail_url"],
        "content_type": news["content_type"],
        "created_at": str(news["created_at"])
        if news["created_at"] else None,
        "attachments": [
            attachment_dict(row) for row in attachments
        ],
    }

@router.get("/api/news-categories")
def get_news_categories(
    active_only: bool = Query(True),
    db: Session = Depends(get_db),
):
    where_clause = "WHERE is_active = 1" if active_only else ""
    rows = db.execute(
        text(f"""
            SELECT
                id,
                name,
                slug,
                description,
                is_active,
                created_at
            FROM stockmate.news_categories
            {where_clause}
            ORDER BY id ASC
        """),
    ).mappings().all()

    return {
        "items": [category_dict(row) for row in rows],
        "total": len(rows),
    }
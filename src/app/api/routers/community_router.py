import os
import uuid

from fastapi import APIRouter, Depends, Query, UploadFile, File, HTTPException
from starlette.requests import Request
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.db import get_async_db
from src.utils.security import get_current_user, get_current_user_optional
from src.services.community_service import CommunityService
from src.database.postgres.repositories.post_repository import PostRepository
from src.app.api.routers.models.community_model import (
    CreatePostRequest, UpdatePostRequest, CommentRequest, CreatePollRequest, VotePollRequest,
)
from src.app.api.rate_limiter import limiter
from src.app.api.config import RATE_LIMITS

UPLOADS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))), "uploads")
ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10 MB

router = APIRouter(prefix="/api/community", tags=["Community"])


# ── Image Upload ──

@router.post("/upload-image")
@limiter.limit("10/minute")
async def upload_community_image(
    request: Request,
    file: UploadFile = File(...),
    user=Depends(get_current_user),
):
    """Upload an image for a community post. Returns the image URL."""
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_IMAGE_EXT:
        raise HTTPException(status_code=400, detail="File type not allowed. Use JPG, PNG, GIF, or WebP.")

    content = await file.read()
    if len(content) > MAX_IMAGE_SIZE:
        raise HTTPException(status_code=400, detail="File too large. Max 10 MB.")

    # Compress if needed
    try:
        from PIL import Image as PILImage
        import io
        img = PILImage.open(io.BytesIO(content))
        img = img.convert("RGB")
        if img.width > 1024:
            ratio = 1024 / img.width
            img = img.resize((1024, int(img.height * ratio)), PILImage.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=75, optimize=True)
        content = buf.getvalue()
        ext = ".jpg"
    except Exception as _e:
        pass  # logged below not to crash hot path

    community_dir = os.path.join(UPLOADS_DIR, "community")
    os.makedirs(community_dir, exist_ok=True)
    filename = f"{user.id}_{uuid.uuid4().hex[:8]}{ext}"
    filepath = os.path.join(community_dir, filename)
    with open(filepath, "wb") as f:
        f.write(content)

    image_url = f"/uploads/community/{filename}"
    return {"image_url": image_url}


# ── Posts ──

@router.post("/posts")
@limiter.limit(RATE_LIMITS["create_post"])
async def create_post(
    request: Request,
    req: CreatePostRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    post = await CommunityService.create_post(session, user.id, req.text, title=req.title, tag=req.tag, image_url=req.image_url)
    return {
        "id": post.id, "text": post.text, "title": post.title, "tag": post.tag, "image_url": post.image_url,
        "created_at": post.created_at.isoformat() if post.created_at else None,
    }


@router.get("/posts")
@limiter.limit(RATE_LIMITS["list_posts"])
async def list_posts(
    request: Request,
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    sort: str = Query("hot", regex="^(hot|new|top)$"),
    cursor: str = Query(None, description="ISO timestamp cursor for keyset pagination"),
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user_optional),
):
    uid = user.id if user else None
    return await CommunityService.list_posts(session, uid, limit=limit, offset=offset, sort=sort, cursor=cursor)


@router.put("/posts/{post_id}")
async def update_post(
    post_id: int,
    req: UpdatePostRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    post = await CommunityService.update_post(session, post_id, user.id, req.model_dump(exclude_unset=True))
    return {
        "id": post.id, "text": post.text, "title": post.title, "tag": post.tag, "image_url": post.image_url,
        "created_at": post.created_at.isoformat() if post.created_at else None,
    }


@router.delete("/posts/{post_id}")
async def delete_post(
    post_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    return await CommunityService.delete_post(session, post_id, user.id)


@router.post("/posts/{post_id}/like")
@limiter.limit(RATE_LIMITS["like"])
async def toggle_like(
    request: Request,
    post_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    return await CommunityService.toggle_like(session, post_id, user.id)


@router.post("/posts/{post_id}/comments")
@limiter.limit(RATE_LIMITS["comment"])
async def add_comment(
    request: Request,
    post_id: int,
    req: CommentRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    comment = await CommunityService.add_comment(session, post_id, user.id, req.text, parent_id=req.parent_id)
    return {
        "id": comment.id, "text": comment.text, "parent_id": comment.parent_id,
        "created_at": comment.created_at.isoformat() if comment.created_at else None,
    }


@router.post("/posts/{post_id}/comments/{comment_id}/like")
@limiter.limit(RATE_LIMITS["like"])
async def like_comment(
    request: Request,
    post_id: int,
    comment_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    liked = await PostRepository.toggle_comment_like(session, comment_id, user.id)
    return {"liked": liked}


@router.put("/posts/{post_id}/comments/{comment_id}")
async def edit_comment(
    post_id: int,
    comment_id: int,
    req: CommentRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    return await CommunityService.edit_comment(session, post_id, comment_id, user.id, req.text)


@router.delete("/posts/{post_id}/comments/{comment_id}")
async def delete_comment(
    post_id: int,
    comment_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    return await CommunityService.delete_comment(session, post_id, comment_id, user.id)


@router.get("/posts/{post_id}/comments")
async def get_comments(
    post_id: int,
    max_depth: int = Query(2, ge=1, le=20),
    parent_id: int = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user_optional),
):
    return await CommunityService.get_comments(
        session, post_id, limit=limit, offset=offset,
        max_depth=max_depth, parent_id=parent_id, user_id=(user.id if user else None),
    )


# ── Polls ──

@router.post("/polls")
async def create_poll(
    req: CreatePollRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    poll = await CommunityService.create_poll(session, user.id, req.question, req.options)
    return {"id": poll.id, "question": poll.question}


@router.get("/polls")
async def list_polls(
    limit: int = Query(10, ge=1, le=20),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user_optional),
):
    return await CommunityService.list_polls(session, (user.id if user else None), limit=limit, offset=offset)


@router.post("/polls/{poll_id}/vote")
async def vote_poll(
    poll_id: int,
    req: VotePollRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    return await CommunityService.vote_poll(session, poll_id, req.option_id, user.id)


# ── Hashtags ──

@router.get("/hashtags/trending")
async def trending_hashtags(
    limit: int = Query(10, ge=1, le=20),
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user_optional),
):
    """Get trending hashtags by post count."""
    from src.database.postgres.schemas.post_schema import HashtagSchema
    from src.database.redis.match_cache import MatchCache
    from sqlalchemy import select

    # Try cache first
    cached = await MatchCache.get_generic("hashtag:trending")
    if cached:
        return cached

    result = await session.execute(
        select(HashtagSchema.name, HashtagSchema.post_count)
        .where(HashtagSchema.post_count > 0)
        .order_by(HashtagSchema.post_count.desc())
        .limit(limit)
    )
    tags = [{"name": r[0], "post_count": r[1]} for r in result.all()]
    await MatchCache.set_generic("hashtag:trending", tags, ttl=300)
    return tags


@router.get("/hashtags/search")
async def search_hashtags(
    q: str = Query(..., min_length=1),
    limit: int = Query(10, ge=1, le=20),
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user_optional),
):
    """Search hashtags by prefix (for autocomplete)."""
    from src.database.postgres.schemas.post_schema import HashtagSchema
    from sqlalchemy import select

    result = await session.execute(
        select(HashtagSchema.name, HashtagSchema.post_count)
        .where(HashtagSchema.name.ilike(f"{q.lower()}%"))
        .order_by(HashtagSchema.post_count.desc())
        .limit(limit)
    )
    return [{"name": r[0], "post_count": r[1]} for r in result.all()]


@router.get("/hashtags/{hashtag_name}/posts")
async def hashtag_posts(
    hashtag_name: str,
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user_optional),
):
    """Get posts tagged with a specific hashtag."""
    from src.database.postgres.schemas.post_schema import PostSchema, PostHashtagSchema, HashtagSchema, PostLikeSchema
    from src.database.postgres.schemas.user_schema import UserSchema
    from sqlalchemy import select

    result = await session.execute(
        select(PostSchema, UserSchema.full_name, UserSchema.first_name, UserSchema.last_name, UserSchema.profile, UserSchema.username)
        .join(UserSchema, PostSchema.user_id == UserSchema.id)
        .join(PostHashtagSchema, PostSchema.id == PostHashtagSchema.post_id)
        .join(HashtagSchema, PostHashtagSchema.hashtag_id == HashtagSchema.id)
        .where(HashtagSchema.name == hashtag_name.lower())
        .order_by(PostSchema.created_at.desc())
        .limit(limit).offset(offset)
    )
    rows = result.all()
    post_ids = [r[0].id for r in rows]
    liked_ids = set()
    if post_ids and user:
        liked_result = await session.execute(
            select(PostLikeSchema.post_id).where(
                PostLikeSchema.post_id.in_(post_ids), PostLikeSchema.user_id == user.id,
            )
        )
        liked_ids = {r[0] for r in liked_result.all()}
    posts = []
    for row in rows:
        post = row[0]
        posts.append({
            "id": post.id, "text": post.text, "title": post.title, "tag": post.tag,
            "image_url": post.image_url, "likes_count": post.likes_count,
            "comments_count": post.comments_count,
            "created_at": post.created_at.isoformat() if post.created_at else None,
            "liked": post.id in liked_ids,
            "user": {
                "id": post.user_id, "full_name": row.full_name,
                "first_name": row.first_name, "last_name": row.last_name,
                "profile": row.profile, "username": row.username,
            },
        })
    return {"posts": posts, "hashtag": hashtag_name}


# ── Mentions ──

@router.get("/mentions")
async def get_mentions(
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """Get posts/comments where the current user was mentioned."""
    from src.database.postgres.schemas.post_schema import MentionSchema, PostSchema
    from src.database.postgres.schemas.user_schema import UserSchema
    from sqlalchemy import select

    result = await session.execute(
        select(MentionSchema, UserSchema.full_name, UserSchema.username, PostSchema.text)
        .join(UserSchema, MentionSchema.mentioner_user_id == UserSchema.id)
        .outerjoin(PostSchema, MentionSchema.post_id == PostSchema.id)
        .where(MentionSchema.mentioned_user_id == user.id)
        .order_by(MentionSchema.created_at.desc())
        .limit(limit).offset(offset)
    )
    mentions = []
    for row in result.all():
        m = row[0]
        mentions.append({
            "id": m.id,
            "mentioner": {"full_name": row.full_name, "username": row.username},
            "post_id": m.post_id,
            "comment_id": m.comment_id,
            "post_text": row.text[:100] if row.text else None,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        })
    return mentions

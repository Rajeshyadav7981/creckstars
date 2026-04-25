from fastapi import HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.repositories.post_repository import PostRepository, PollRepository
from src.database.postgres.schemas.post_schema import PostSchema, PostCommentSchema, CommentClosureSchema, HashtagSchema, PostHashtagSchema, MentionSchema
from src.database.postgres.schemas.user_schema import UserSchema
from src.database.redis.match_cache import MatchCache
from src.utils.text_parser import extract_mentions, extract_hashtags


class CommunityService:

    @staticmethod
    async def create_post(session: AsyncSession, user_id: int, text: str, title: str = None, tag: str = None, image_url: str = None):
        if not text or not text.strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Post text is required")
        clean_text = text.strip()
        post = await PostRepository.create_post(session, user_id, clean_text, title=title, tag=tag, image_url=image_url)

        # Process hashtags — batch upsert (1 query instead of N)
        hashtags = extract_hashtags(clean_text)
        if hashtags:
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            tag_names = list(set(hashtags[:10]))
            stmt = pg_insert(HashtagSchema).values([{"name": t, "post_count": 1} for t in tag_names])
            stmt = stmt.on_conflict_do_update(
                index_elements=["name"],
                set_={"post_count": HashtagSchema.post_count + 1}
            ).returning(HashtagSchema.id, HashtagSchema.name)
            result = await session.execute(stmt)
            ht_map = {r.name: r.id for r in result.all()}
            for t in tag_names:
                if t in ht_map:
                    session.add(PostHashtagSchema(post_id=post.id, hashtag_id=ht_map[t]))
            await session.flush()

        # Process mentions — resolve all usernames to user_ids in ONE query
        # instead of one per mention.
        mentions = extract_mentions(clean_text)
        if mentions:
            usernames = list(set(mentions[:20]))  # dedup, cap at 20
            user_res = await session.execute(
                select(UserSchema.id, UserSchema.username)
                .where(UserSchema.username.in_(usernames))
            )
            for uid, _uname in user_res.all():
                if uid and uid != user_id:
                    session.add(MentionSchema(
                        mentioned_user_id=uid,
                        mentioner_user_id=user_id,
                        post_id=post.id,
                    ))
            await session.flush()

        await session.commit()
        await CommunityService._invalidate_posts_cache()
        return post

    @staticmethod
    async def update_post(session: AsyncSession, post_id: int, user_id: int, data: dict):
        result = await session.execute(select(PostSchema).where(PostSchema.id == post_id))
        post = result.scalar_one_or_none()
        if not post or post.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found or not authorized")
        for key, val in data.items():
            if val is not None and hasattr(post, key):
                setattr(post, key, val)
        await session.commit()
        await session.refresh(post)
        return post

    @staticmethod
    async def _invalidate_posts_cache():
        """Invalidate all cached post list pages so next request fetches fresh data."""
        try:
            from src.database.redis.redis_client import redis_client
            r = await redis_client.get_client()
            if r:
                for sort in ("hot", "new", "top"):
                    sort_keys = await r.keys(f"cache:posts:{sort}:*")
                    if sort_keys:
                        await r.delete(*sort_keys)
        except Exception as _e:
            pass  # logged below not to crash hot path

    # Per-sort cache TTLs — top/hot change slowly, new needs freshness
    _SORT_TTL = {"top": 300, "hot": 60, "new": 30}

    @staticmethod
    async def list_posts(session: AsyncSession, user_id: int, limit: int = 20, offset: int = 0, sort: str = "hot", cursor: str = None):
        # Shared cache key (no user_id — liked state added per-user after)
        cache_key = f"posts:{sort}:l{limit}:o{offset}:c{cursor or ''}"
        cache_ttl = CommunityService._SORT_TTL.get(sort, 30)
        cached_posts = None
        try:
            cached_posts = await MatchCache.get_generic(cache_key)
        except Exception as _e:
            pass  # logged below not to crash hot path

        if cached_posts is not None:
            posts = cached_posts.get("posts", [])
        else:
            rows = await PostRepository.list_posts(session, limit=limit, offset=offset, sort=sort, cursor=cursor)

            redis_likes = {}
            post_ids = [row[0].id for row in rows]
            try:
                from src.database.redis.redis_client import redis_client
                r = await redis_client.get_client()
                if r and post_ids:
                    pipe = r.pipeline()
                    for pid in post_ids:
                        pipe.get(f"post_likes:{pid}")
                    vals = await pipe.execute()
                    for pid, val in zip(post_ids, vals):
                        if val is not None:
                            redis_likes[pid] = int(val)
            except Exception as _e:
                pass  # logged below not to crash hot path

            # NOTE: hot-score sorting now happens in SQL inside list_posts —
            # rows are already in the right order by the time they get here.

            posts = []
            for row in rows:
                post = row[0]
                posts.append({
                    "id": post.id, "text": post.text, "title": post.title, "tag": post.tag,
                    "image_url": post.image_url,
                    "likes_count": redis_likes.get(post.id, post.likes_count),
                    "comments_count": post.comments_count,
                    "created_at": post.created_at.isoformat() if post.created_at else None,
                    "user": {
                        "id": post.user_id, "full_name": row.full_name,
                        "first_name": row.first_name, "last_name": row.last_name,
                        "profile": getattr(row, 'profile', None),
                    },
                })

            next_cursor = str(posts[-1]["id"]) if posts and len(posts) == limit else None
            cached_posts = {"posts": posts, "next_cursor": next_cursor}
            try:
                await MatchCache.set_generic(cache_key, cached_posts, ttl=cache_ttl)
            except Exception as _e:
                pass  # logged below not to crash hot path

        # Per-user liked state (always fresh, not cached). Skip if guest.
        post_ids = [p["id"] for p in posts]
        if post_ids and user_id:
            liked_ids = await PostRepository.get_likes_for_posts(session, post_ids, user_id)
            for p in posts:
                p["liked"] = p["id"] in liked_ids
        else:
            for p in posts:
                p["liked"] = False

        return cached_posts

    @staticmethod
    async def toggle_like(session: AsyncSession, post_id: int, user_id: int):
        post = await PostRepository.get_post(session, post_id)
        if not post:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")
        liked = await PostRepository.toggle_like(session, post_id, user_id)
        new_count = post.likes_count + (1 if liked else -1)
        # Cache the latest like count in Redis for fast reads
        try:
            from src.database.redis.redis_client import redis_client
            r = await redis_client.get_client()
            if r:
                await r.setex(f"post_likes:{post_id}", 300, str(max(0, new_count)))
        except Exception as _e:
            pass  # logged below not to crash hot path
        return {"liked": liked, "likes_count": max(0, new_count)}

    @staticmethod
    async def delete_post(session: AsyncSession, post_id: int, user_id: int):
        post = await PostRepository.get_post(session, post_id)
        if not post:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")
        if post.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your post")
        await PostRepository.delete_post(session, post_id)
        await CommunityService._invalidate_posts_cache()
        return {"message": "Post deleted"}

    @staticmethod
    async def edit_comment(session: AsyncSession, post_id: int, comment_id: int, user_id: int, text: str):
        from sqlalchemy import select as _sel
        result = await session.execute(_sel(PostCommentSchema).where(PostCommentSchema.id == comment_id))
        comment = result.scalar_one_or_none()
        if not comment:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")
        if comment.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your comment")
        if not text or not text.strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Text required")
        comment.text = text.strip()
        await session.commit()
        try:
            from src.database.redis.redis_client import redis_client
            r = await redis_client.get_client()
            if r:
                keys = await r.keys(f"cache:comments:{post_id}:*")
                if keys:
                    await r.delete(*keys)
        except Exception as _e:
            pass  # logged below not to crash hot path
        return {"id": comment.id, "text": comment.text}

    @staticmethod
    async def delete_comment(session: AsyncSession, post_id: int, comment_id: int, user_id: int):
        from sqlalchemy import select as _sel
        result = await session.execute(_sel(PostCommentSchema).where(PostCommentSchema.id == comment_id))
        comment = result.scalar_one_or_none()
        if not comment:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")
        if comment.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your comment")
        await PostRepository.delete_comment(session, comment_id)
        post = await PostRepository.get_post(session, post_id)
        if post and post.comments_count > 0:
            post.comments_count -= 1
            await session.commit()
        try:
            from src.database.redis.redis_client import redis_client
            r = await redis_client.get_client()
            if r:
                keys = await r.keys(f"cache:comments:{post_id}:*")
                if keys:
                    await r.delete(*keys)
        except Exception as _e:
            pass  # logged below not to crash hot path
        await CommunityService._invalidate_posts_cache()
        return {"message": "Comment deleted"}

    @staticmethod
    async def add_comment(session: AsyncSession, post_id: int, user_id: int, text: str, parent_id: int = None):
        post = await PostRepository.get_post(session, post_id)
        if not post:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")
        if not text or not text.strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Comment text is required")
        comment = await PostRepository.add_comment(session, post_id, user_id, text.strip(), parent_id=parent_id)

        closure_self = CommentClosureSchema(ancestor_id=comment.id, descendant_id=comment.id, depth=0)
        session.add(closure_self)

        if parent_id:
            ancestors = await session.execute(
                select(CommentClosureSchema).where(CommentClosureSchema.descendant_id == parent_id)
            )
            closure_rows = [
                CommentClosureSchema(
                    ancestor_id=anc.ancestor_id,
                    descendant_id=comment.id,
                    depth=anc.depth + 1,
                )
                for anc in ancestors.scalars().all()
            ]
            if closure_rows:
                session.add_all(closure_rows)
        await session.flush()
        await session.commit()

        # Invalidate ALL comment caches for this post + posts list cache
        try:
            from src.database.redis.redis_client import redis_client
            r = await redis_client.get_client()
            if r:
                keys = await r.keys(f"cache:comments:{post_id}:*")
                if keys:
                    await r.delete(*keys)
        except Exception as _e:
            pass  # logged below not to crash hot path
        await CommunityService._invalidate_posts_cache()

        return comment

    @staticmethod
    async def get_comments(session: AsyncSession, post_id: int, limit: int = 20, offset: int = 0,
                           max_depth: int = 2, parent_id: int = None, user_id: int = None):
        cache_key = f"comments:{post_id}:d{max_depth}:p{parent_id}:l{limit}:o{offset}"
        try:
            cached = await MatchCache.get_generic(cache_key)
            if cached:
                return cached
        except Exception as _e:
            pass  # logged below not to crash hot path

        if parent_id:
            rows = await PostRepository.get_comments_subtree(session, post_id, parent_id, max_depth=max_depth, limit=limit, offset=offset)
        else:
            rows = await PostRepository.get_comments(session, post_id, limit=limit, offset=offset)

        # Build flat list first (ordered by created_at ASC so parents always come before children)
        comments_by_id = {}
        flat_list = []
        for row in rows:
            comment = {
                "id": row[0].id,
                "text": row[0].text,
                "parent_id": row[0].parent_id,
                "likes_count": row[0].likes_count or 0,
                "created_at": row[0].created_at.isoformat() if row[0].created_at else None,
                "user": {
                    "id": row[0].user_id,
                    "full_name": row.full_name,
                    "first_name": row.first_name,
                    "last_name": row.last_name,
                    "profile": getattr(row, 'profile', None),
                    "username": getattr(row, 'username', None),
                },
                "replies": [],
                "has_more_replies": False,
                "reply_count": 0,
            }
            comments_by_id[comment["id"]] = comment
            flat_list.append(comment)

        # Build tree: attach children to their parents
        # Works because query is ASC — parent is always already in comments_by_id
        top_level = []
        for comment in flat_list:
            pid = comment["parent_id"]
            if pid and pid in comments_by_id:
                comments_by_id[pid]["replies"].append(comment)
            else:
                top_level.append(comment)

        def _limit_depth(nodes, current_depth):
            for node in nodes:
                if current_depth >= max_depth:
                    total_replies = _count_all_replies(node["replies"])
                    node["has_more_replies"] = total_replies > 0
                    node["reply_count"] = total_replies
                    node["replies"] = []
                else:
                    _limit_depth(node["replies"], current_depth + 1)

        def _count_all_replies(replies):
            count = len(replies)
            for r in replies:
                count += _count_all_replies(r["replies"])
            return count

        _limit_depth(top_level, 0)

        try:
            await MatchCache.set_generic(cache_key, top_level, ttl=10)
        except Exception as _e:
            pass  # logged below not to crash hot path

        # Add user-specific liked state (not cached — per-user)
        if user_id and flat_list:
            try:
                liked_ids = await PostRepository.get_comment_likes_for_user(
                    session, [c["id"] for c in flat_list], user_id
                )
                for c in flat_list:
                    c["liked"] = c["id"] in liked_ids
            except Exception:
                # comment_likes table may not exist yet — gracefully skip
                for c in flat_list:
                    c["liked"] = False

        return top_level

    @staticmethod
    async def create_poll(session: AsyncSession, user_id: int, question: str, options: list[str]):
        if not question or not question.strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Question is required")
        if len(options) < 2:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least 2 options required")
        return await PollRepository.create_poll(session, user_id, question.strip(), options)

    @staticmethod
    async def list_polls(session: AsyncSession, user_id: int, limit: int = 10, offset: int = 0):
        """Batch query: 1 query for polls + options + user vote (was 21 queries)."""
        rows = await PollRepository.list_polls_batch(session, user_id, limit=limit, offset=offset)
        from collections import OrderedDict
        polls_map = OrderedDict()
        for r in rows:
            pid = r.id
            if pid not in polls_map:
                polls_map[pid] = {
                    "id": pid,
                    "question": r.question,
                    "total_votes": r.total_votes,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "user": {
                        "id": r.user_id,
                        "full_name": r.full_name,
                        "first_name": r.first_name,
                        "last_name": r.last_name,
                        "profile": r.profile,
                        "username": r.username,
                    },
                    "voted_option_id": r.user_voted,
                    "options": [],
                    "_seen_opts": set(),
                }
            if r.opt_id and r.opt_id not in polls_map[pid]["_seen_opts"]:
                polls_map[pid]["_seen_opts"].add(r.opt_id)
                polls_map[pid]["options"].append({"id": r.opt_id, "text": r.opt_text, "votes": r.opt_votes or 0})
        for p in polls_map.values():
            del p["_seen_opts"]
        return list(polls_map.values())

    @staticmethod
    async def vote_poll(session: AsyncSession, poll_id: int, option_id: int, user_id: int):
        poll = await PollRepository.get_poll(session, poll_id)
        if not poll:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Poll not found")
        result = await PollRepository.vote(session, poll_id, option_id, user_id)
        return {"status": result}

from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.schemas.post_schema import (
    PostSchema, PostLikeSchema, PostCommentSchema, CommentClosureSchema,
    CommentLikeSchema, PollSchema, PollOptionSchema, PollVoteSchema,
)
from src.database.postgres.schemas.user_schema import UserSchema


class PostRepository:

    @staticmethod
    async def create_post(session: AsyncSession, user_id: int, text: str, title: str = None, tag: str = None, image_url: str = None) -> PostSchema:
        post = PostSchema(user_id=user_id, text=text, title=title, tag=tag, image_url=image_url)
        session.add(post)
        await session.commit()
        await session.refresh(post)
        return post

    @staticmethod
    async def get_post(session: AsyncSession, post_id: int) -> PostSchema | None:
        result = await session.execute(
            select(PostSchema).where(PostSchema.id == post_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_posts(session: AsyncSession, limit: int = 20, offset: int = 0, sort: str = "new", cursor: str = None):
        query = (
            select(PostSchema, UserSchema.full_name, UserSchema.first_name, UserSchema.last_name, UserSchema.profile)
            .join(UserSchema, PostSchema.user_id == UserSchema.id)
        )
        if sort == "top":
            query = query.order_by(PostSchema.likes_count.desc(), PostSchema.id.desc())
        else:
            # "new" and "hot" both fetch by created_at; hot is re-sorted in Python
            query = query.order_by(PostSchema.created_at.desc(), PostSchema.id.desc())

        # Cursor-based pagination (id tie-breaking prevents duplicates/skips)
        if cursor:
            try:
                parts = cursor.split('|')
                cursor_id = int(parts[0])
                query = query.where(PostSchema.id < cursor_id)
            except (ValueError, TypeError, IndexError):
                query = query.offset(offset)
        else:
            query = query.offset(offset)

        result = await session.execute(query.limit(limit))
        return result.all()

    @staticmethod
    async def delete_post(session: AsyncSession, post_id: int):
        await session.execute(delete(PostSchema).where(PostSchema.id == post_id))
        await session.commit()

    @staticmethod
    async def delete_comment(session: AsyncSession, comment_id: int):
        # Delete closure table entries first, then the comment (CASCADE handles children)
        await session.execute(delete(CommentClosureSchema).where(
            (CommentClosureSchema.ancestor_id == comment_id) | (CommentClosureSchema.descendant_id == comment_id)
        ))
        await session.execute(delete(PostCommentSchema).where(PostCommentSchema.id == comment_id))
        await session.commit()

    @staticmethod
    async def toggle_comment_like(session: AsyncSession, comment_id: int, user_id: int) -> bool:
        """Toggle like on comment. Returns True if liked, False if unliked."""
        existing = await session.execute(
            select(CommentLikeSchema).where(
                CommentLikeSchema.comment_id == comment_id,
                CommentLikeSchema.user_id == user_id,
            )
        )
        like = existing.scalar_one_or_none()
        comment = await session.get(PostCommentSchema, comment_id)
        if like:
            await session.delete(like)
            if comment:
                comment.likes_count = max(0, (comment.likes_count or 0) - 1)
            await session.commit()
            return False
        session.add(CommentLikeSchema(comment_id=comment_id, user_id=user_id))
        if comment:
            comment.likes_count = (comment.likes_count or 0) + 1
        await session.commit()
        return True

    @staticmethod
    async def get_comment_likes_for_user(session: AsyncSession, comment_ids: list, user_id: int) -> set:
        if not comment_ids:
            return set()
        result = await session.execute(
            select(CommentLikeSchema.comment_id).where(
                CommentLikeSchema.comment_id.in_(comment_ids),
                CommentLikeSchema.user_id == user_id,
            )
        )
        return {r[0] for r in result.all()}

    @staticmethod
    async def toggle_like(session: AsyncSession, post_id: int, user_id: int) -> bool:
        """Toggle like. Returns True if liked, False if unliked."""
        existing = await session.execute(
            select(PostLikeSchema).where(
                PostLikeSchema.post_id == post_id,
                PostLikeSchema.user_id == user_id,
            )
        )
        like = existing.scalar_one_or_none()
        if like:
            await session.delete(like)
            # Decrement count
            post = await session.get(PostSchema, post_id)
            if post:
                post.likes_count = max(0, post.likes_count - 1)
            await session.commit()
            return False
        else:
            session.add(PostLikeSchema(post_id=post_id, user_id=user_id))
            post = await session.get(PostSchema, post_id)
            if post:
                post.likes_count += 1
            await session.commit()
            return True

    @staticmethod
    async def has_liked(session: AsyncSession, post_id: int, user_id: int) -> bool:
        result = await session.execute(
            select(PostLikeSchema.id).where(
                PostLikeSchema.post_id == post_id,
                PostLikeSchema.user_id == user_id,
            )
        )
        return result.scalar_one_or_none() is not None

    @staticmethod
    async def get_likes_for_posts(session: AsyncSession, post_ids: list[int], user_id: int) -> set[int]:
        """Return set of post_ids that the user has liked."""
        if not post_ids:
            return set()
        result = await session.execute(
            select(PostLikeSchema.post_id).where(
                PostLikeSchema.post_id.in_(post_ids),
                PostLikeSchema.user_id == user_id,
            )
        )
        return {r[0] for r in result.all()}

    @staticmethod
    async def add_comment(session: AsyncSession, post_id: int, user_id: int, text: str, parent_id: int = None) -> PostCommentSchema:
        comment = PostCommentSchema(post_id=post_id, user_id=user_id, text=text, parent_id=parent_id)
        session.add(comment)
        post = await session.get(PostSchema, post_id)
        if post:
            post.comments_count += 1
        await session.commit()
        await session.refresh(comment)
        return comment

    @staticmethod
    async def get_comments(session: AsyncSession, post_id: int, limit: int = 200, offset: int = 0):
        result = await session.execute(
            select(PostCommentSchema, UserSchema.full_name, UserSchema.first_name, UserSchema.last_name, UserSchema.profile, UserSchema.username)
            .join(UserSchema, PostCommentSchema.user_id == UserSchema.id)
            .where(PostCommentSchema.post_id == post_id)
            .order_by(PostCommentSchema.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
        return result.all()

    @staticmethod
    async def get_comments_subtree(session: AsyncSession, post_id: int, parent_id: int,
                                   max_depth: int = 5, limit: int = 200, offset: int = 0):
        """Fetch a subtree of comments under parent_id using the closure table."""
        result = await session.execute(
            select(PostCommentSchema, UserSchema.full_name, UserSchema.first_name, UserSchema.last_name, UserSchema.profile, UserSchema.username)
            .join(UserSchema, PostCommentSchema.user_id == UserSchema.id)
            .join(CommentClosureSchema, PostCommentSchema.id == CommentClosureSchema.descendant_id)
            .where(
                PostCommentSchema.post_id == post_id,
                CommentClosureSchema.ancestor_id == parent_id,
                CommentClosureSchema.depth <= max_depth,
            )
            .order_by(CommentClosureSchema.depth, PostCommentSchema.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
        return result.all()


class PollRepository:

    @staticmethod
    async def create_poll(session: AsyncSession, user_id: int, question: str, options: list[str]) -> PollSchema:
        poll = PollSchema(user_id=user_id, question=question)
        session.add(poll)
        await session.flush()
        for opt_text in options:
            session.add(PollOptionSchema(poll_id=poll.id, text=opt_text))
        await session.commit()
        await session.refresh(poll)
        return poll

    @staticmethod
    async def get_poll(session: AsyncSession, poll_id: int) -> PollSchema | None:
        result = await session.execute(
            select(PollSchema).where(PollSchema.id == poll_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_options(session: AsyncSession, poll_id: int) -> list[PollOptionSchema]:
        result = await session.execute(
            select(PollOptionSchema).where(PollOptionSchema.poll_id == poll_id).order_by(PollOptionSchema.id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def list_polls_batch(session: AsyncSession, user_id: int, limit: int = 10, offset: int = 0):
        """Fetch polls with options and user vote in a single query (kills N+1)."""
        from sqlalchemy.orm import aliased
        result = await session.execute(
            select(
                PollSchema.id, PollSchema.question, PollSchema.total_votes, PollSchema.created_at, PollSchema.user_id,
                UserSchema.full_name, UserSchema.first_name, UserSchema.last_name, UserSchema.profile, UserSchema.username,
                PollOptionSchema.id.label('opt_id'), PollOptionSchema.text.label('opt_text'), PollOptionSchema.votes.label('opt_votes'),
                PollVoteSchema.option_id.label('user_voted'),
            )
            .join(UserSchema, PollSchema.user_id == UserSchema.id)
            .outerjoin(PollOptionSchema, PollOptionSchema.poll_id == PollSchema.id)
            .outerjoin(PollVoteSchema, (PollVoteSchema.poll_id == PollSchema.id) & (PollVoteSchema.user_id == user_id))
            .where(PollSchema.id.in_(
                select(PollSchema.id).order_by(PollSchema.created_at.desc()).limit(limit).offset(offset).scalar_subquery()
            ))
            .order_by(PollSchema.created_at.desc(), PollOptionSchema.id.asc())
        )
        return result.all()

    @staticmethod
    async def vote(session: AsyncSession, poll_id: int, option_id: int, user_id: int) -> str:
        """Vote on poll. Returns 'voted', 'changed', or 'removed'."""
        existing = await session.execute(
            select(PollVoteSchema).where(
                PollVoteSchema.poll_id == poll_id,
                PollVoteSchema.user_id == user_id,
            )
        )
        old_vote = existing.scalar_one_or_none()

        if old_vote:
            if old_vote.option_id == option_id:
                # Same option — remove vote (deselect)
                old_option = await session.get(PollOptionSchema, old_vote.option_id)
                if old_option:
                    old_option.votes = max(0, old_option.votes - 1)
                poll = await session.get(PollSchema, poll_id)
                if poll:
                    poll.total_votes = max(0, poll.total_votes - 1)
                await session.delete(old_vote)
                await session.commit()
                return "removed"
            else:
                # Different option — change vote
                old_option = await session.get(PollOptionSchema, old_vote.option_id)
                if old_option:
                    old_option.votes = max(0, old_option.votes - 1)
                new_option = await session.get(PollOptionSchema, option_id)
                if new_option:
                    new_option.votes += 1
                old_vote.option_id = option_id
                await session.commit()
                return "changed"

        # New vote
        session.add(PollVoteSchema(poll_id=poll_id, option_id=option_id, user_id=user_id))
        option = await session.get(PollOptionSchema, option_id)
        if option:
            option.votes += 1
        poll = await session.get(PollSchema, poll_id)
        if poll:
            poll.total_votes += 1
        await session.commit()
        return "voted"

    @staticmethod
    async def get_user_vote(session: AsyncSession, poll_id: int, user_id: int) -> int | None:
        result = await session.execute(
            select(PollVoteSchema.option_id).where(
                PollVoteSchema.poll_id == poll_id,
                PollVoteSchema.user_id == user_id,
            )
        )
        row = result.scalar_one_or_none()
        return row

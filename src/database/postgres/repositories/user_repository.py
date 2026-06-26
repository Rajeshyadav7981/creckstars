from sqlalchemy import select, or_, case, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only
from src.database.postgres.schemas.user_schema import UserSchema


# Statements that purge a user's private engagement data. Authored content
# (posts/comments/polls/matches/tournaments) is intentionally left in place and
# de-identified via the anonymized user row — deleting it would cascade into
# other users' replies and corrupt match scorecards.
_DELETE_PERSONAL_DATA = (
    "DELETE FROM push_tokens WHERE user_id = :uid",
    "DELETE FROM match_subscriptions WHERE user_id = :uid",
    "DELETE FROM user_favorite_matches WHERE user_id = :uid",
    "DELETE FROM user_favorite_tournaments WHERE user_id = :uid",
    "DELETE FROM post_likes WHERE user_id = :uid",
    "DELETE FROM comment_likes WHERE user_id = :uid",
    "DELETE FROM poll_votes WHERE user_id = :uid",
    "DELETE FROM mentions WHERE mentioned_user_id = :uid OR mentioner_user_id = :uid",
)


class UserRepository:

    @staticmethod
    async def create_user(session: AsyncSession, data: dict) -> UserSchema:
        user = UserSchema(**data)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user

    @staticmethod
    async def get_by_email(session: AsyncSession, email: str) -> UserSchema | None:
        result = await session.execute(
            select(UserSchema).where(UserSchema.email == email)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_mobile(session: AsyncSession, mobile: str) -> UserSchema | None:
        result = await session.execute(
            select(UserSchema).where(UserSchema.mobile == mobile)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_id(session: AsyncSession, user_id: int) -> UserSchema | None:
        result = await session.execute(
            select(UserSchema).where(UserSchema.id == user_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def update_password(session: AsyncSession, user_id: int, new_hash: str):
        result = await session.execute(
            select(UserSchema).where(UserSchema.id == user_id)
        )
        user = result.scalar_one_or_none()
        if user:
            user.password = new_hash
            await session.commit()

    @staticmethod
    async def update_user(session: AsyncSession, user_id: int, data: dict) -> UserSchema | None:
        result = await session.execute(
            select(UserSchema).where(UserSchema.id == user_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            return None
        for key, value in data.items():
            setattr(user, key, value)
        await session.commit()
        await session.refresh(user)
        return user

    @staticmethod
    async def search(session: AsyncSession, query: str, limit: int = 20) -> list[tuple]:
        """Search users with priority (username prefix > username contains > name contains) using pg_trgm GIN indexes; returns (user, player_id) rows to avoid N+1 on cricket-profile link."""
        from src.database.postgres.schemas.player_schema import PlayerSchema
        # Subquery: cheapest linked player per user (MIN picks lowest id, which
        # is the first stub/user row ever created). LATERAL would be tidier but
        # GROUP BY is simpler and the player→user mapping is 1:1 in practice.
        player_link = (
            select(
                PlayerSchema.user_id.label("uid"),
                func.min(PlayerSchema.id).label("pid"),
            )
            .where(PlayerSchema.user_id.isnot(None))
            .group_by(PlayerSchema.user_id)
            .subquery()
        )
        q = (
            select(UserSchema, player_link.c.pid)
            .outerjoin(player_link, player_link.c.uid == UserSchema.id)
            .options(load_only(
                UserSchema.id, UserSchema.username, UserSchema.full_name,
                UserSchema.first_name, UserSchema.last_name, UserSchema.profile,
            ))
        )
        if query and query.strip():
            ql = query.strip().lower()
            q = q.where(or_(
                UserSchema.username.ilike(f"{ql}%"),       # prefix (uses varchar_pattern_ops)
                UserSchema.username.ilike(f"%{ql}%"),      # contains (uses GIN trgm)
                UserSchema.full_name.ilike(f"%{ql}%"),     # name (uses GIN trgm)
            )).order_by(
                case(
                    (UserSchema.username.ilike(f"{ql}%"), 0),   # exact prefix first
                    (UserSchema.username.ilike(f"%{ql}%"), 1),  # username contains
                    else_=2,                                     # name match last
                ),
                UserSchema.full_name,
            )
        else:
            q = q.order_by(UserSchema.full_name)
        result = await session.execute(q.limit(limit))
        return list(result.all())

    @staticmethod
    async def delete_account(session: AsyncSession, user_id: int) -> bool:
        """Permanently delete a user's account for Play Store compliance.

        Purges private engagement data, decrements follower/following counts on
        the counterparties, unlinks any cricket player profile, then anonymizes
        the user row in place (NOT NULL created_by references elsewhere prevent a
        hard row delete). The original mobile/email/username are freed so the
        number can be re-registered, and the password is scrubbed so login is
        impossible. Runs as a single transaction.
        """
        user = await UserRepository.get_by_id(session, user_id)
        if not user:
            return False

        params = {"uid": user_id}

        # Keep denormalized follow counts correct on the people this user was
        # connected to, before the follow rows are removed.
        await session.execute(text(
            "UPDATE users SET following_count = GREATEST(following_count - 1, 0) "
            "WHERE id IN (SELECT follower_id FROM user_follows WHERE following_id = :uid)"
        ), params)
        await session.execute(text(
            "UPDATE users SET followers_count = GREATEST(followers_count - 1, 0) "
            "WHERE id IN (SELECT following_id FROM user_follows WHERE follower_id = :uid)"
        ), params)
        await session.execute(text(
            "DELETE FROM user_follows WHERE follower_id = :uid OR following_id = :uid"
        ), params)

        for stmt in _DELETE_PERSONAL_DATA:
            await session.execute(text(stmt), params)

        # Unlink the cricket profile but keep the player row so historical
        # scorecards stay intact.
        await session.execute(text(
            "UPDATE players SET user_id = NULL WHERE user_id = :uid"
        ), params)

        # Anonymize the account in place and free the unique identifiers.
        await session.execute(text(
            "UPDATE users SET "
            "first_name = 'Deleted', last_name = 'User', full_name = 'Deleted User', "
            "mobile = 'del_' || id, email = NULL, username = NULL, "
            "password = 'account_deleted', profile = NULL, bio = NULL, "
            "city = NULL, state_province = NULL, country = NULL, "
            "date_of_birth = NULL, batting_style = NULL, bowling_style = NULL, "
            "player_role = NULL, followers_count = 0, following_count = 0 "
            "WHERE id = :uid"
        ), params)

        await session.commit()
        return True

from sqlalchemy import select, update as sa_update, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.schemas.tournament_stage_schema import TournamentStageSchema
from src.database.postgres.schemas.tournament_group_schema import TournamentGroupSchema
from src.database.postgres.schemas.tournament_group_team_schema import TournamentGroupTeamSchema
from src.database.postgres.schemas.team_schema import TeamSchema

class TournamentStageRepository:
    @staticmethod
    async def create_stage(session, data):
        stage = TournamentStageSchema(**data)
        session.add(stage)
        await session.flush()
        return stage

    @staticmethod
    async def get_stages(session, tournament_id):
        result = await session.execute(
            select(TournamentStageSchema)
            .where(TournamentStageSchema.tournament_id == tournament_id)
            .order_by(TournamentStageSchema.stage_order)
        )
        return result.scalars().all()

    @staticmethod
    async def get_stage_by_id(session, stage_id):
        result = await session.execute(select(TournamentStageSchema).where(TournamentStageSchema.id == stage_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def update_stage(session, stage_id, data):
        result = await session.execute(select(TournamentStageSchema).where(TournamentStageSchema.id == stage_id))
        stage = result.scalar_one_or_none()
        if stage:
            for k, v in data.items():
                setattr(stage, k, v)
            await session.flush()
        return stage

    @staticmethod
    async def create_group(session, data):
        group = TournamentGroupSchema(**data)
        session.add(group)
        await session.flush()
        return group

    @staticmethod
    async def get_groups(session, stage_id):
        result = await session.execute(
            select(TournamentGroupSchema)
            .where(TournamentGroupSchema.stage_id == stage_id)
            .order_by(TournamentGroupSchema.group_order)
        )
        return result.scalars().all()

    @staticmethod
    async def add_team_to_group(session, group_id, team_id):
        gteam = TournamentGroupTeamSchema(group_id=group_id, team_id=team_id)
        session.add(gteam)
        await session.flush()
        return gteam

    @staticmethod
    async def get_group_teams(session, group_id):
        result = await session.execute(
            select(TeamSchema, TournamentGroupTeamSchema)
            .join(TournamentGroupTeamSchema, TeamSchema.id == TournamentGroupTeamSchema.team_id)
            .where(TournamentGroupTeamSchema.group_id == group_id)
        )
        return result.all()

    @staticmethod
    async def update_team_status(session, group_id, team_id, status):
        result = await session.execute(
            select(TournamentGroupTeamSchema)
            .where(TournamentGroupTeamSchema.group_id == group_id, TournamentGroupTeamSchema.team_id == team_id)
        )
        gt = result.scalar_one_or_none()
        if gt:
            gt.qualification_status = status
            await session.flush()
        return gt

    @staticmethod
    async def bulk_update_team_status(session, pairs_by_status):
        """Apply qualification status to many (group_id, team_id) pairs at once.

        `pairs_by_status` is `{status: [(group_id, team_id), ...]}`. Each
        status value gets ONE UPDATE statement using a (group_id, team_id) IN
        tuple — replaces N×2 SELECT+UPDATE round-trips with at most one query
        per distinct status value (typically 2: "qualified" and "eliminated").
        """
        for status, pairs in pairs_by_status.items():
            if not pairs:
                continue
            await session.execute(
                sa_update(TournamentGroupTeamSchema)
                .where(
                    tuple_(
                        TournamentGroupTeamSchema.group_id,
                        TournamentGroupTeamSchema.team_id,
                    ).in_(pairs)
                )
                .values(qualification_status=status)
            )

    @staticmethod
    async def remove_team_from_group(session, group_id, team_id):
        result = await session.execute(
            select(TournamentGroupTeamSchema)
            .where(TournamentGroupTeamSchema.group_id == group_id, TournamentGroupTeamSchema.team_id == team_id)
        )
        gt = result.scalar_one_or_none()
        if gt:
            await session.delete(gt)
            await session.flush()
            return True
        return False

    @staticmethod
    async def get_all_group_teams_for_stage(session, stage_id):
        result = await session.execute(
            select(TournamentGroupTeamSchema, TournamentGroupSchema)
            .join(TournamentGroupSchema, TournamentGroupTeamSchema.group_id == TournamentGroupSchema.id)
            .where(TournamentGroupSchema.stage_id == stage_id)
        )
        return result.all()

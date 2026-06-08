import asyncio
from uuid import UUID

from puppyrun_agent.workflow import run_phase1_workflow, run_phase2_workflow
from puppyrun_api.db import SessionLocal
from puppyrun_api.repositories import sessions as session_repo


async def run_phase1_agent_job(ctx: dict, run_id: str) -> str:
    parsed_run_id = UUID(run_id)
    async with SessionLocal() as db:
        return await run_phase1_workflow(db, parsed_run_id)


async def run_phase2_agent_job(ctx: dict, run_id: str) -> str:
    parsed_run_id = UUID(run_id)
    async with SessionLocal() as db:
        return await run_phase2_workflow(db, parsed_run_id)


async def run_dummy_agent_job(ctx: dict, run_id: str) -> str:
    parsed_run_id = UUID(run_id)
    async with SessionLocal() as db:
        await session_repo.mark_run_started(db, parsed_run_id)
    await asyncio.sleep(0.1)
    summary = "Phase 0 dummy Agent completed. Real research workflow is not enabled yet."
    async with SessionLocal() as db:
        await session_repo.mark_run_completed(db, parsed_run_id, summary)
    return summary

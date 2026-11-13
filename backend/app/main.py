import os

import asyncpg
from fastapi import FastAPI, HTTPException

from app.api import router

app = FastAPI(title="Industrial Safety Workflow API")
app.include_router(router)


@app.get("/health")
async def health() -> dict[str, str]:
    try:
        connection = await asyncpg.connect(os.environ["DATABASE_URL"], timeout=3)
        try:
            await connection.fetchval("SELECT 1")
        finally:
            await connection.close()
    except (asyncpg.PostgresError, OSError, TimeoutError) as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc

    return {"status": "ok", "database": "ok"}

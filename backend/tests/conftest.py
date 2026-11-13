import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def database_url() -> str:
    url = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://safety:safety_local_only@db:5432/industrial_safety",
    )
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


@pytest.fixture(scope="session")
def engine():
    engine = create_engine(database_url())
    with engine.connect() as connection:
        connection.exec_driver_sql("SELECT 1")
    yield engine
    engine.dispose()


@pytest.fixture
def connection(engine):
    connection = engine.connect()
    transaction = connection.begin()
    yield connection
    transaction.rollback()
    connection.close()


@pytest.fixture
def api_client(connection):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app

    def override_get_db():
        with Session(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        ) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()

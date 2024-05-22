import pytest
from typing import Generator
from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine

@pytest.fixture(scope="session")
def db_connect_string() -> str:
    return "sqlite:///test.db"

@pytest.fixture(scope="session")
def engine(*, db_connect_string: str) -> Engine:
    return create_engine(db_connect_string)

def _init_db(engine: Engine) -> None:
    # Database model registration.
    from .player import DbPlayer  # noqa: F401
    # Table creation.
    SQLModel.metadata.create_all(engine)

def _ping_database(engine: Engine) -> bool:
    try:
        engine.connect()
        return True
    except Exception:
        return False

@pytest.fixture(scope="session")
def database(*, engine: Engine) -> Engine:
    _init_db(engine)
    return engine

@pytest.fixture(scope="function")
def session(*, database: Engine) -> Generator[Session, None, None]:
    with Session(database) as session:
        yield session

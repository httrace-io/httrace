import os
from sqlmodel import create_engine, Session
from sqlalchemy import event

# In production (Fly.io) the volume is mounted at /data
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./httrace.db")
engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _enable_wal(dbapi_conn, _connection_record):
    dbapi_conn.execute("PRAGMA journal_mode=WAL")


def get_session():
    with Session(engine) as session:
        yield session

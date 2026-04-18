from datetime import datetime
from typing import Optional, Any
from sqlmodel import SQLModel, Field, Column, JSON


class CaptureRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    # Tenant scoping — every record is owned by the API key that ingested it
    api_key: str = Field(index=True, sa_column_kwargs={"server_default": ""})
    service: str = Field(index=True)
    session_id: Optional[str] = Field(default=None, index=True)
    fingerprint: str = Field(index=True)

    method: str
    path: str = Field(index=True)
    query_params: dict = Field(default_factory=dict, sa_column=Column(JSON))
    req_headers: dict = Field(default_factory=dict, sa_column=Column(JSON))
    req_body: Any = Field(default=None, sa_column=Column(JSON))

    status_code: int
    resp_headers: dict = Field(default_factory=dict, sa_column=Column(JSON))
    resp_body: Any = Field(default=None, sa_column=Column(JSON))
    latency_ms: float

    captured_at: datetime = Field(default_factory=datetime.utcnow)
    cluster_id: Optional[str] = Field(default=None, index=True)


class WaitlistEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    signed_up_at: datetime = Field(default_factory=datetime.utcnow)


class GeneratedTest(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    # Tenant scoping
    api_key: str = Field(index=True, sa_column_kwargs={"server_default": ""})
    service: str = Field(index=True)
    test_name: str
    test_code: str
    source_fingerprints: list = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow)

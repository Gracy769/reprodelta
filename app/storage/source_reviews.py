"""Private, revision-keyed source reviews in the existing connection database."""
from __future__ import annotations

import uuid

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def _table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS source_reviews (
        id UUID PRIMARY KEY, owner_id TEXT NOT NULL, provider TEXT NOT NULL,
        repository TEXT NOT NULL, number INTEGER NOT NULL, base_sha TEXT NOT NULL,
        head_sha TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        result JSONB NOT NULL,
        UNIQUE(owner_id, provider, repository, number, base_sha, head_sha)
    )""")


def save(dsn: str, owner: str, result: dict) -> str:
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=10) as conn:
        _table(conn)
        row = conn.execute("""INSERT INTO source_reviews
            (id,owner_id,provider,repository,number,base_sha,head_sha,result)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(owner_id,provider,repository,number,base_sha,head_sha)
            DO UPDATE SET result=EXCLUDED.result, created_at=NOW() RETURNING id""",
            (uuid.uuid4(), owner, result["provider"], result["repository"], result["number"],
             result["base_sha"], result["head_sha"], Jsonb(result))).fetchone()
    return str(row["id"])


def recent(dsn: str, owner: str) -> list[dict]:
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=10) as conn:
        _table(conn)
        rows = conn.execute("""SELECT id,provider,repository,number,head_sha,created_at
            FROM source_reviews WHERE owner_id=%s ORDER BY created_at DESC LIMIT 20""", (owner,)).fetchall()
    return [{**row, "id": str(row["id"])} for row in rows]


def get(dsn: str, owner: str, review_id: uuid.UUID) -> dict | None:
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=10) as conn:
        _table(conn)
        row = conn.execute("SELECT result FROM source_reviews WHERE id=%s AND owner_id=%s",
                           (review_id, owner)).fetchone()
    return {**row["result"], "review_id": str(review_id)} if row else None

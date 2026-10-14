"""Persistent account identity and private, generated identicons."""

from __future__ import annotations

import secrets

import psycopg
from psycopg.rows import dict_row

from app.api.connections import _database_dsn


def mask_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    local, domain = email.rsplit("@", 1)
    if not local or not domain:
        return None
    if len(local) <= 2:
        return f"{local[0]}***@{domain}"
    visible = local[: min(4, len(local) - 1)]
    return f"{visible}***{local[-1]}@{domain}"


def upsert_profile(owner_id: str, name: str, email: str | None) -> dict:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS user_profiles (
                owner_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                email TEXT,
                avatar_seed TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )"""
        )
        row = conn.execute(
            """INSERT INTO user_profiles (owner_id, display_name, email, avatar_seed)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (owner_id) DO UPDATE SET
                   display_name=EXCLUDED.display_name,
                   email=COALESCE(EXCLUDED.email, user_profiles.email)
               RETURNING display_name, email, avatar_seed""",
            (owner_id, name[:100], email, secrets.token_hex(20)),
        ).fetchone()
    return dict(row)


def avatar_svg(seed: str) -> str:
    """Build a symmetric five-column identicon from a stored random seed."""
    raw = bytes.fromhex(seed)
    palette = ("#15803d", "#3256a8", "#793fa2", "#c46632", "#207c83", "#bd466c")
    color = palette[raw[0] % len(palette)]
    background = ("#edf8ef", "#edf2ff", "#f4edfb", "#fff1e8", "#e9f7f6", "#fff0f5")[raw[0] % len(palette)]
    cells = []
    for y in range(5):
        for x in range(3):
            if raw[1 + y * 3 + x] & 1:
                for mirrored in {x, 4 - x}:
                    cells.append(f'<rect x="{12 + mirrored * 16}" y="{12 + y * 16}" width="16" height="16"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 104 104" role="img" aria-label="Generated account avatar">'
            f'<rect width="104" height="104" rx="18" fill="{background}"/>'
            f'<g fill="{color}">{"".join(cells)}</g></svg>')

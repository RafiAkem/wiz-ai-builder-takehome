from __future__ import annotations

import csv
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.config import DEFAULT_DATABASE_PATH, SEED_CSV_PATH
from app.normalization import (
    clean,
    display_name,
    email_domain,
    normalize_company,
    normalize_email,
    normalize_name,
    normalize_phone,
    normalize_status,
    parse_date,
)
from app.source_extraction import extract_source

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY,
    first_name TEXT NOT NULL DEFAULT '',
    last_name TEXT NOT NULL DEFAULT '',
    full_name TEXT NOT NULL,
    job_title TEXT NOT NULL DEFAULT '',
    company TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'New',
    lifecycle_stage TEXT NOT NULL DEFAULT '',
    original_source TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT '',
    created_at TEXT,
    modified_at TEXT,
    notes TEXT NOT NULL DEFAULT '',
    lead_score INTEGER,
    source_channel TEXT NOT NULL DEFAULT 'Other',
    source_detail TEXT NOT NULL DEFAULT 'Unclassified',
    normalized_name TEXT NOT NULL DEFAULT '',
    normalized_company TEXT NOT NULL DEFAULT '',
    normalized_email TEXT NOT NULL DEFAULT '',
    email_domain TEXT NOT NULL DEFAULT '',
    normalized_phone TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_owner ON leads(owner);
CREATE INDEX IF NOT EXISTS idx_leads_country ON leads(country);
CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(normalized_email);
CREATE INDEX IF NOT EXISTS idx_leads_phone ON leads(normalized_phone);
CREATE INDEX IF NOT EXISTS idx_leads_domain ON leads(email_domain);
CREATE INDEX IF NOT EXISTS idx_leads_company ON leads(normalized_company);
"""

PUBLIC_COLUMNS = (
    "id", "first_name", "last_name", "full_name", "job_title", "company", "email",
    "phone", "country", "status", "lifecycle_stage", "original_source", "owner",
    "created_at", "modified_at", "notes", "lead_score", "source_channel", "source_detail",
)


class LeadStore:
    def __init__(self, path: str | Path = DEFAULT_DATABASE_PATH):
        self.path = str(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self, seed_path: str | Path = SEED_CSV_PATH) -> int:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            count = connection.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
            if count:
                return count
            with Path(seed_path).open(newline="", encoding="utf-8-sig") as stream:
                records = [self._from_csv(row) for row in csv.DictReader(stream)]
            connection.executemany(self._insert_sql(), records)
            return len(records)

    @staticmethod
    def _from_csv(row: dict[str, str]) -> dict[str, object]:
        full_name = display_name(row["First Name"], row["Last Name"], row["Full Name"])
        source = extract_source(row["Notes"], row["Original Source"])
        email = normalize_email(row["Email"])
        score = clean(row["Lead Score"])
        return {
            "id": int(row["Record ID"]), "first_name": clean(row["First Name"]),
            "last_name": clean(row["Last Name"]), "full_name": full_name,
            "job_title": clean(row["Job Title"]), "company": clean(row["Company Name"]),
            "email": clean(row["Email"]), "phone": clean(row["Phone Number"]),
            "country": clean(row["Country/Region"]), "status": normalize_status(row["Lead Status"]),
            "lifecycle_stage": clean(row["Lifecycle Stage"]), "original_source": clean(row["Original Source"]),
            "owner": clean(row["Contact Owner"]), "created_at": parse_date(row["Create Date"]),
            "modified_at": parse_date(row["Last Modified Date"]), "notes": clean(row["Notes"]),
            "lead_score": int(score) if score else None, "source_channel": source.channel,
            "source_detail": source.detail, "normalized_name": normalize_name("", "", full_name),
            "normalized_company": normalize_company(row["Company Name"]), "normalized_email": email,
            "email_domain": email_domain(email), "normalized_phone": normalize_phone(row["Phone Number"]),
        }

    @staticmethod
    def _insert_sql() -> str:
        columns = (
            "id", "first_name", "last_name", "full_name", "job_title", "company", "email", "phone",
            "country", "status", "lifecycle_stage", "original_source", "owner", "created_at", "modified_at",
            "notes", "lead_score", "source_channel", "source_detail", "normalized_name", "normalized_company",
            "normalized_email", "email_domain", "normalized_phone",
        )
        names = ", ".join(columns)
        values = ", ".join(f":{name}" for name in columns)
        return f"INSERT INTO leads ({names}) VALUES ({values})"

    @staticmethod
    def _filters(status: str | None, owner: str | None, country: str | None, q: str | None) -> tuple[str, list[object]]:
        clauses: list[str] = []
        params: list[object] = []
        for column, value in (("status", status), ("owner", owner), ("country", country)):
            if value:
                clauses.append(f"LOWER({column}) = LOWER(?)")
                params.append(clean(value))
        if q:
            clauses.append("LOWER(full_name || ' ' || company || ' ' || email) LIKE ?")
            params.append(f"%{clean(q).casefold()}%")
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), params

    def list(self, *, status: str | None = None, owner: str | None = None, country: str | None = None,
             q: str | None = None, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        where, params = self._filters(status, owner, country, q)
        columns = ", ".join(PUBLIC_COLUMNS)
        with self.connect() as connection:
            total = connection.execute(f"SELECT COUNT(*) FROM leads{where}", params).fetchone()[0]
            rows = connection.execute(
                f"SELECT {columns} FROM leads{where} ORDER BY id LIMIT ? OFFSET ?", [*params, limit, offset]
            ).fetchall()
        return [dict(row) for row in rows], total

    def get(self, lead_id: int, *, internal: bool = False) -> dict | None:
        columns = "*" if internal else ", ".join(PUBLIC_COLUMNS)
        with self.connect() as connection:
            row = connection.execute(f"SELECT {columns} FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return dict(row) if row else None

    def update(self, lead_id: int, changes: dict[str, object]) -> dict | None:
        if not changes:
            return self.get(lead_id)
        values = dict(changes)
        if "status" in values:
            values["status"] = normalize_status(values["status"])
        assignments = ", ".join(f"{column} = ?" for column in values)
        with self.connect() as connection:
            cursor = connection.execute(
                f"UPDATE leads SET {assignments} WHERE id = ?", [*values.values(), lead_id]
            )
            if not cursor.rowcount:
                return None
        return self.get(lead_id)

    def all_internal(self) -> list[dict]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM leads ORDER BY id")]

    def dashboard(self) -> dict:
        with self.connect() as connection:
            total = connection.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
            statuses = dict(connection.execute("SELECT status, COUNT(*) FROM leads GROUP BY status ORDER BY status"))
            channels = dict(connection.execute("SELECT source_channel, COUNT(*) FROM leads GROUP BY source_channel ORDER BY source_channel"))
        return {"total": total, "by_status": statuses, "by_source_channel": channels}

    def insert(self, record: dict[str, object]) -> dict:
        with self.connect() as connection:
            next_id = connection.execute("SELECT COALESCE(MAX(id), 100000000) + 1 FROM leads").fetchone()[0]
            values = {**record, "id": next_id}
            connection.execute(self._insert_sql(), values)
        return self.get(next_id)  # type: ignore[return-value]

    def replace_ingest_fields(self, lead_id: int, fields: dict[str, object]) -> dict:
        assignments = ", ".join(f"{column} = ?" for column in fields)
        with self.connect() as connection:
            connection.execute(f"UPDATE leads SET {assignments} WHERE id = ?", [*fields.values(), lead_id])
        return self.get(lead_id)  # type: ignore[return-value]

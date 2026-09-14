import argparse
from pathlib import Path

from app.config import DEFAULT_DATABASE_PATH, SEED_CSV_PATH
from app.database import LeadStore

DROPPED_COLUMNS = (
    "City",
    "Original Source Drill-Down 1",
    "Annual Revenue",
    "Marketing contact status",
    "GDPR consent",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Load the supplied CRM export into SQLite.")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--seed", type=Path, default=SEED_CSV_PATH)
    parser.add_argument("--reset", action="store_true", help="Delete the target database before loading.")
    args = parser.parse_args()

    args.database.parent.mkdir(parents=True, exist_ok=True)
    if args.reset and args.database.exists():
        args.database.unlink()

    store = LeadStore(args.database)
    count = store.initialize(args.seed)
    rows = store.all_internal()
    full_name_only = next(row for row in rows if row["full_name"] and not row["first_name"])
    normalized_status = next(row for row in rows if row["status"] == "New")
    normalized_date = next(row for row in rows if row["created_at"] and "T" in row["created_at"])

    print(f"Loaded rows: {count}")
    print(f"Dropped blank columns: {', '.join(DROPPED_COLUMNS)}")
    print(f"Full-name-only check: {full_name_only['id']} -> {full_name_only['full_name']}")
    print(f"Status check: {normalized_status['id']} -> {normalized_status['status']}")
    print(f"Date check: {normalized_date['id']} -> {normalized_date['created_at']}")


if __name__ == "__main__":
    main()

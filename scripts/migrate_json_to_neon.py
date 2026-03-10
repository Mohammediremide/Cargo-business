import argparse
import json
import os
import sys

import psycopg
from psycopg.types.json import Json


DEFAULT_FILES = [
    "bookings.json",
    "pending_payments.json",
    "withdrawals.json",
    "delivered_bookings.json",
    "config.json",
    "users.json",
    "notifications.json",
    "chat_messages.json",
]


def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return None


def main():
    parser = argparse.ArgumentParser(
        description="Migrate JSON data files into Neon kv_store."
    )
    parser.add_argument(
        "--data-dir",
        default=os.path.join(os.path.dirname(__file__), "..", "cargo_fish_app"),
        help="Directory containing JSON files (default: cargo_fish_app)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing keys in kv_store",
    )
    parser.add_argument(
        "--files",
        nargs="*",
        default=DEFAULT_FILES,
        help="Specific JSON files to migrate",
    )
    args = parser.parse_args()

    database_url = (
        os.environ.get("DATABASE_URL")
        or os.environ.get("POSTGRES_URL")
        or os.environ.get("POSTGRES_PRISMA_URL")
        or os.environ.get("NEON_DATABASE_URL")
    )
    if not database_url:
        print("DATABASE_URL is not set.", file=sys.stderr)
        sys.exit(1)

    data_dir = os.path.abspath(args.data_dir)
    files = args.files

    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(
            """
            create table if not exists kv_store (
                key text primary key,
                value jsonb not null,
                updated_at timestamptz not null default now()
            )
            """
        )

        for filename in files:
            path = os.path.join(data_dir, filename)
            data = load_json(path)
            if data is None:
                print(f"skip: {filename} (missing or invalid JSON)")
                continue

            if args.overwrite:
                conn.execute(
                    """
                    insert into kv_store (key, value, updated_at)
                    values (%s, %s, now())
                    on conflict (key)
                    do update set value = excluded.value, updated_at = now()
                    """,
                    (filename, Json(data)),
                )
                print(f"upsert: {filename}")
            else:
                row = conn.execute(
                    "select 1 from kv_store where key = %s",
                    (filename,),
                ).fetchone()
                if row:
                    print(f"skip: {filename} (already exists)")
                    continue
                conn.execute(
                    "insert into kv_store (key, value) values (%s, %s)",
                    (filename, Json(data)),
                )
                print(f"insert: {filename}")


if __name__ == "__main__":
    main()

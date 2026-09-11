"""Physical-schema and migration tests for generic metadata indexes."""

import json
import pickle
import sqlite3

from app.storage_sqlite import SQLiteStorage


FAKE_EMB = [0.1, 0.2, 0.3, 0.4]


def _table_names(db):
    return {row[0] for row in db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()}


def test_fresh_database_uses_generic_physical_index():
    db = SQLiteStorage(":memory:")
    tables = _table_names(db)

    assert {"index_values", "memory_index_values"} <= tables
    assert {"keywords", "memory_keywords", "categories", "memory_categories"}.isdisjoint(tables)


def test_same_value_isolated_by_dimension():
    db = SQLiteStorage(":memory:")
    db.add("text", FAKE_EMB,
           metadata={"keywords": ["design"], "categories": ["design"]})

    rows = db.conn.execute(
        "SELECT dimension, value FROM index_values ORDER BY dimension"
    ).fetchall()
    assert [(row["dimension"], row["value"]) for row in rows] == [
        ("categories", "design"), ("keywords", "design")
    ]


def test_legacy_dimension_tables_migrate_from_authoritative_metadata(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE memories (
            id TEXT PRIMARY KEY, text TEXT NOT NULL, embedding BLOB,
            metadata TEXT NOT NULL DEFAULT '{}', namespace TEXT NOT NULL DEFAULT 'default',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            deleted INTEGER NOT NULL DEFAULT 0, synced INTEGER NOT NULL DEFAULT 0,
            summary TEXT
        );
        CREATE TABLE keywords (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL);
        CREATE TABLE memory_keywords (
            memory_id TEXT NOT NULL, keyword_id INTEGER NOT NULL,
            PRIMARY KEY (memory_id, keyword_id)
        );
        CREATE TABLE categories (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL);
        CREATE TABLE memory_categories (
            memory_id TEXT NOT NULL, category_id INTEGER NOT NULL,
            PRIMARY KEY (memory_id, category_id)
        );
    """)
    conn.execute(
        "INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?)",
        ("m1", "legacy", pickle.dumps(FAKE_EMB),
         json.dumps({"keywords": ["Project"], "categories": ["Bugfix"]}),
         "ns", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00", None),
    )
    # Deliberately stale legacy rows prove migration trusts metadata, not caches.
    conn.execute("INSERT INTO keywords(name) VALUES ('stale')")
    conn.execute("INSERT INTO categories(name) VALUES ('stale')")
    conn.commit()
    conn.close()

    db = SQLiteStorage(str(path))

    assert {"keywords", "memory_keywords", "categories", "memory_categories"}.isdisjoint(
        _table_names(db)
    )
    assert db.get_stats()["keyword_counts"] == {"project": 1}
    assert db.get_stats()["category_counts"] == {"bugfix": 1}
    assert [m["id"] for m in db.search(FAKE_EMB, keywords=["project"], categories=["bugfix"])] == ["m1"]

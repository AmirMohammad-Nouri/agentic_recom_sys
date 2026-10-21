"""SQLite persistence layer for profiles, goals, event logs, and recommendation snapshots."""
import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    current_profile_version INTEGER DEFAULT 1,
    current_goal_version INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS profile_versions (
    version_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    created_at REAL NOT NULL,
    declared_json TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS goal_versions (
    version_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    name TEXT NOT NULL,
    created_at REAL NOT NULL,
    weights_json TEXT NOT NULL,
    diversity REAL NOT NULL,
    mode TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    timestamp REAL NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recommendation_snapshots (
    request_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    timestamp REAL NOT NULL,
    profile_version INTEGER NOT NULL,
    goal_version INTEGER NOT NULL,
    config_json TEXT NOT NULL,
    results_json TEXT NOT NULL,
    ranking_table_json TEXT NOT NULL,
    reasoning_trace_json TEXT NOT NULL
);
"""


class Database:
    def __init__(self, db_path="foodrec.db"):
        self.db_path = Path(db_path)
        self.init_db()

    def get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        with self.get_conn() as conn:
            conn.executescript(SCHEMA)

    def create_user_if_not_exists(self, user_id):
        now = time.time()
        with self.get_conn() as conn:
            conn.execute("INSERT OR IGNORE INTO users (user_id, created_at) VALUES (?, ?)", (user_id, now))

    def save_profile(self, user_id, declared):
        self.create_user_if_not_exists(user_id)
        now = time.time()
        with self.get_conn() as conn:
            cur = conn.execute("SELECT MAX(version) FROM profile_versions WHERE user_id = ?", (user_id,))
            row = cur.fetchone()
            new_ver = (row[0] or 0) + 1 if row[0] is not None else 1
            conn.execute(
                "INSERT INTO profile_versions (user_id, version, created_at, declared_json) VALUES (?, ?, ?, ?)",
                (user_id, new_ver, now, json.dumps(declared))
            )
            conn.execute("UPDATE users SET current_profile_version = ? WHERE user_id = ?", (new_ver, user_id))
            return new_ver

    def get_latest_profile(self, user_id):
        with self.get_conn() as conn:
            cur = conn.execute(
                "SELECT version, declared_json FROM profile_versions WHERE user_id = ? ORDER BY version DESC LIMIT 1",
                (user_id,)
            )
            row = cur.fetchone()
            if row:
                return {"version": row["version"], "declared": json.loads(row["declared_json"])}
            return None

    def save_goal(self, user_id, name, weights, diversity=0.3, mode="lightweight"):
        self.create_user_if_not_exists(user_id)
        now = time.time()
        with self.get_conn() as conn:
            cur = conn.execute("SELECT MAX(version) FROM goal_versions WHERE user_id = ?", (user_id,))
            row = cur.fetchone()
            new_ver = (row[0] or 0) + 1 if row[0] is not None else 1
            conn.execute(
                """INSERT INTO goal_versions 
                   (user_id, version, name, created_at, weights_json, diversity, mode)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (user_id, new_ver, name, now, json.dumps(weights), diversity, mode)
            )
            conn.execute("UPDATE users SET current_goal_version = ? WHERE user_id = ?", (new_ver, user_id))
            return new_ver

    def get_latest_goal(self, user_id):
        with self.get_conn() as conn:
            cur = conn.execute(
                "SELECT version, name, weights_json, diversity, mode FROM goal_versions WHERE user_id = ? ORDER BY version DESC LIMIT 1",
                (user_id,)
            )
            row = cur.fetchone()
            if row:
                return {
                    "version": row["version"], "name": row["name"],
                    "weights": json.loads(row["weights_json"]),
                    "diversity": row["diversity"], "mode": row["mode"]
                }
            return None

    def log_event(self, event_id, user_id, event_type, payload):
        self.create_user_if_not_exists(user_id)
        now = time.time()
        with self.get_conn() as conn:
            conn.execute(
                "INSERT INTO events (event_id, user_id, timestamp, event_type, payload_json) VALUES (?, ?, ?, ?, ?)",
                (event_id, user_id, now, event_type, json.dumps(payload))
            )

    def get_events(self, user_id, limit=50):
        with self.get_conn() as conn:
            cur = conn.execute(
                "SELECT event_id, timestamp, event_type, payload_json FROM events WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
                (user_id, limit)
            )
            return [{"event_id": r["event_id"], "ts": r["timestamp"], "type": r["event_type"],
                     "payload": json.loads(r["payload_json"])} for r in cur.fetchall()]

    def save_snapshot(self, request_id, user_id, prof_ver, goal_ver, config, results, ranking_table, trace):
        now = time.time()
        with self.get_conn() as conn:
            conn.execute(
                """INSERT INTO recommendation_snapshots 
                   (request_id, user_id, timestamp, profile_version, goal_version, config_json, results_json, ranking_table_json, reasoning_trace_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (request_id, user_id, now, prof_ver, goal_ver, json.dumps(config),
                 json.dumps(results), json.dumps(ranking_table), json.dumps(trace))
            )

    def get_snapshot(self, request_id):
        with self.get_conn() as conn:
            cur = conn.execute("SELECT * FROM recommendation_snapshots WHERE request_id = ?", (request_id,))
            row = cur.fetchone()
            if row:
                return {
                    "request_id": row["request_id"], "user_id": row["user_id"],
                    "timestamp": row["timestamp"], "profile_version": row["profile_version"],
                    "goal_version": row["goal_version"], "config": json.loads(row["config_json"]),
                    "results": json.loads(row["results_json"]), "ranking_table": json.loads(row["ranking_table_json"]),
                    "reasoning_trace": json.loads(row["reasoning_trace_json"])
                }
            return None
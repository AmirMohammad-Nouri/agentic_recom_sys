"""SQLite persistence: versioned profiles and goals, the append-only event log, immutable
request snapshots, supervisor decisions, conversations and user-added documents.

The event log is the source of truth. The learner and the knowledge graph are projections
that can be rebuilt by replaying events in `seq` order.
"""
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS profile_versions (
    user_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    created_at REAL NOT NULL,
    declared_json TEXT NOT NULL,
    source TEXT NOT NULL,
    note TEXT,
    PRIMARY KEY (user_id, version)
);
CREATE TABLE IF NOT EXISTS goal_versions (
    user_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    name TEXT NOT NULL,
    created_at REAL NOT NULL,
    weights_json TEXT NOT NULL,
    diversity REAL NOT NULL,
    mode TEXT NOT NULL,
    source TEXT NOT NULL,
    note TEXT,
    PRIMARY KEY (user_id, version)
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    user_id TEXT NOT NULL,
    ts REAL NOT NULL,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    context_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS events_user ON events(user_id, seq);
CREATE TABLE IF NOT EXISTS requests (
    request_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    ts REAL NOT NULL,
    origin TEXT NOT NULL,
    profile_version INTEGER NOT NULL,
    goal_version INTEGER NOT NULL,
    result_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS requests_user ON requests(user_id, ts);
CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    title TEXT NOT NULL,
    body_json TEXT NOT NULL,
    source TEXT NOT NULL,
    resolved_ts REAL,
    resolution_json TEXT
);
CREATE INDEX IF NOT EXISTS decisions_user ON decisions(user_id, ts);
CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    ts REAL NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    trace_json TEXT NOT NULL DEFAULT '[]',
    attachments_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS messages_user ON messages(user_id, ts);
CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


def new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)

    def _conn(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    # ---- users -------------------------------------------------------------------
    def ensure_user(self, user_id):
        with self._conn() as c:
            c.execute("INSERT OR IGNORE INTO users VALUES (?, ?)", (user_id, time.time()))

    def users(self):
        with self._conn() as c:
            return [r["user_id"] for r in c.execute("SELECT user_id FROM users ORDER BY created_at")]

    # ---- versioned profile and goal ------------------------------------------------
    def save_profile(self, user_id, declared, source="user", note=None):
        self.ensure_user(user_id)
        with self._lock, self._conn() as c:
            v = (c.execute("SELECT MAX(version) FROM profile_versions WHERE user_id=?",
                           (user_id,)).fetchone()[0] or 0) + 1
            c.execute("INSERT INTO profile_versions VALUES (?,?,?,?,?,?)",
                      (user_id, v, time.time(), json.dumps(declared), source, note))
        return v

    def profile(self, user_id, version=None):
        q = "SELECT * FROM profile_versions WHERE user_id=?"
        args = [user_id]
        if version:
            q += " AND version=?"
            args.append(version)
        with self._conn() as c:
            r = c.execute(q + " ORDER BY version DESC LIMIT 1", args).fetchone()
        return None if r is None else {"version": r["version"], "created_at": r["created_at"],
                                       "declared": json.loads(r["declared_json"]),
                                       "source": r["source"], "note": r["note"]}

    def profile_history(self, user_id):
        with self._conn() as c:
            rows = c.execute("SELECT * FROM profile_versions WHERE user_id=? ORDER BY version DESC",
                             (user_id,)).fetchall()
        return [{"version": r["version"], "created_at": r["created_at"], "source": r["source"],
                 "note": r["note"], "declared": json.loads(r["declared_json"])} for r in rows]

    def save_goal(self, user_id, name, weights, diversity, mode, source="user", note=None):
        self.ensure_user(user_id)
        with self._lock, self._conn() as c:
            v = (c.execute("SELECT MAX(version) FROM goal_versions WHERE user_id=?",
                           (user_id,)).fetchone()[0] or 0) + 1
            c.execute("INSERT INTO goal_versions VALUES (?,?,?,?,?,?,?,?,?)",
                      (user_id, v, name, time.time(), json.dumps(weights), diversity, mode, source, note))
        return v

    @staticmethod
    def _goal_row(r):
        return {"version": r["version"], "name": r["name"], "created_at": r["created_at"],
                "weights": json.loads(r["weights_json"]), "diversity": r["diversity"],
                "mode": r["mode"], "source": r["source"], "note": r["note"]}

    def goal(self, user_id):
        with self._conn() as c:
            r = c.execute("SELECT * FROM goal_versions WHERE user_id=? ORDER BY version DESC LIMIT 1",
                          (user_id,)).fetchone()
        return None if r is None else self._goal_row(r)

    def goal_history(self, user_id):
        with self._conn() as c:
            rows = c.execute("SELECT * FROM goal_versions WHERE user_id=? ORDER BY version DESC",
                             (user_id,)).fetchall()
        return [self._goal_row(r) for r in rows]

    # ---- event log -----------------------------------------------------------------
    def append_event(self, user_id, type_, payload, context=None, ts=None):
        self.ensure_user(user_id)
        ev = {"event_id": new_id("ev"), "user_id": user_id, "ts": ts or time.time(),
              "type": type_, "payload": payload, "context": context or {}}
        with self._conn() as c:
            cur = c.execute("INSERT INTO events (event_id,user_id,ts,type,payload_json,context_json) "
                            "VALUES (?,?,?,?,?,?)",
                            (ev["event_id"], user_id, ev["ts"], type_, json.dumps(payload),
                             json.dumps(ev["context"])))
            ev["seq"] = cur.lastrowid
        return ev

    @staticmethod
    def _event_row(r):
        return {"seq": r["seq"], "event_id": r["event_id"], "user_id": r["user_id"], "ts": r["ts"],
                "type": r["type"], "payload": json.loads(r["payload_json"]),
                "context": json.loads(r["context_json"])}

    def events(self, user_id=None, since=None, until=None, types=None, limit=None, newest_first=False):
        q, args = "SELECT * FROM events WHERE 1=1", []
        if user_id:
            q += " AND user_id=?"
            args.append(user_id)
        if since is not None:
            q += " AND ts>=?"
            args.append(since)
        if until is not None:
            q += " AND ts<=?"
            args.append(until)
        if types:
            q += f" AND type IN ({','.join('?' * len(types))})"
            args.extend(types)
        q += " ORDER BY seq " + ("DESC" if newest_first else "ASC")
        if limit:
            q += " LIMIT ?"
            args.append(limit)
        with self._conn() as c:
            return [self._event_row(r) for r in c.execute(q, args)]

    # ---- immutable request snapshots ----------------------------------------------
    def save_request(self, user_id, origin, profile_version, goal_version, result):
        rid = result.get("request_id") or new_id("req")
        result["request_id"] = rid
        with self._conn() as c:
            c.execute("INSERT INTO requests VALUES (?,?,?,?,?,?,?)",
                      (rid, user_id, time.time(), origin, profile_version, goal_version,
                       json.dumps(result, default=float)))
        return rid

    def request(self, request_id):
        with self._conn() as c:
            r = c.execute("SELECT result_json FROM requests WHERE request_id=?", (request_id,)).fetchone()
        return None if r is None else json.loads(r["result_json"])

    def last_list(self, user_id):
        """Most recent snapshot that actually showed recipes (what "#2" refers to)."""
        with self._conn() as c:
            for r in c.execute("SELECT result_json FROM requests WHERE user_id=? ORDER BY ts DESC LIMIT 20",
                               (user_id,)):
                res = json.loads(r["result_json"])
                if res.get("results"):
                    return res
        return None

    def last_request(self, user_id, before=None):
        q, args = "SELECT result_json FROM requests WHERE user_id=?", [user_id]
        if before:
            q += " AND ts < (SELECT ts FROM requests WHERE request_id=?)"
            args.append(before)
        with self._conn() as c:
            r = c.execute(q + " ORDER BY ts DESC LIMIT 1", args).fetchone()
        return None if r is None else json.loads(r["result_json"])

    # ---- supervisor decisions --------------------------------------------------------
    def add_decision(self, user_id, kind, title, body, source, status="open"):
        d = {"decision_id": new_id("dec"), "user_id": user_id, "ts": time.time(), "kind": kind,
             "status": status, "title": title, "body": body, "source": source,
             "resolved_ts": None, "resolution": None}
        with self._conn() as c:
            c.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (d["decision_id"], user_id, d["ts"], kind, status, title,
                       json.dumps(body, default=float), source, None, None))
        return d

    def resolve_decision(self, decision_id, status, resolution):
        with self._conn() as c:
            c.execute("UPDATE decisions SET status=?, resolved_ts=?, resolution_json=? WHERE decision_id=?",
                      (status, time.time(), json.dumps(resolution, default=float), decision_id))
        return self.decision(decision_id)

    @staticmethod
    def _decision_row(r):
        return {"decision_id": r["decision_id"], "user_id": r["user_id"], "ts": r["ts"],
                "kind": r["kind"], "status": r["status"], "title": r["title"],
                "body": json.loads(r["body_json"]), "source": r["source"],
                "resolved_ts": r["resolved_ts"],
                "resolution": json.loads(r["resolution_json"]) if r["resolution_json"] else None}

    def decision(self, decision_id):
        with self._conn() as c:
            r = c.execute("SELECT * FROM decisions WHERE decision_id=?", (decision_id,)).fetchone()
        return None if r is None else self._decision_row(r)

    def decisions(self, user_id, status=None, kind=None, since=None, limit=100):
        q, args = "SELECT * FROM decisions WHERE user_id=?", [user_id]
        if status:
            q += " AND status=?"
            args.append(status)
        if kind:
            q += " AND kind=?"
            args.append(kind)
        if since is not None:
            q += " AND ts>=?"
            args.append(since)
        with self._conn() as c:
            return [self._decision_row(r) for r in
                    c.execute(q + " ORDER BY ts DESC LIMIT ?", args + [limit])]

    # ---- conversation ----------------------------------------------------------------
    def add_message(self, user_id, role, text, trace=None, attachments=None):
        m = {"message_id": new_id("msg"), "user_id": user_id, "ts": time.time(), "role": role,
             "text": text, "trace": trace or [], "attachments": attachments or {}}
        with self._conn() as c:
            c.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?)",
                      (m["message_id"], user_id, m["ts"], role, text,
                       json.dumps(m["trace"], default=float), json.dumps(m["attachments"], default=float)))
        return m

    def messages(self, user_id, limit=100):
        with self._conn() as c:
            rows = c.execute("SELECT * FROM messages WHERE user_id=? ORDER BY ts DESC LIMIT ?",
                             (user_id, limit)).fetchall()
        return [{"message_id": r["message_id"], "ts": r["ts"], "role": r["role"], "text": r["text"],
                 "trace": json.loads(r["trace_json"]),
                 "attachments": json.loads(r["attachments_json"])} for r in reversed(rows)]

    def clear_messages(self, user_id):
        with self._conn() as c:
            c.execute("DELETE FROM messages WHERE user_id=?", (user_id,))

    # ---- user-added knowledge documents -------------------------------------------
    def add_document(self, title, text, source="user"):
        doc_id = new_id("doc")
        with self._conn() as c:
            c.execute("INSERT INTO documents VALUES (?,?,?,?,?)", (doc_id, title, text, source, time.time()))
        return doc_id

    def documents(self):
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM documents ORDER BY created_at")]

    def delete_document(self, doc_id):
        with self._conn() as c:
            c.execute("DELETE FROM documents WHERE doc_id=?", (doc_id,))

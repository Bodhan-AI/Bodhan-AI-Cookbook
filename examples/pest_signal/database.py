import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "cropshield.db"


def connect():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS reports(
          id INTEGER PRIMARY KEY AUTOINCREMENT, crop TEXT NOT NULL, symptoms TEXT NOT NULL,
          suspected_disease TEXT, original_language TEXT NOT NULL, original_text TEXT NOT NULL,
          translated_text TEXT NOT NULL, location TEXT NOT NULL, date TEXT NOT NULL,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS alerts(
          id INTEGER PRIMARY KEY AUTOINCREMENT, region TEXT NOT NULL, crop TEXT NOT NULL,
          symptoms_key TEXT NOT NULL, report_count INTEGER NOT NULL, report_ids TEXT NOT NULL,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP, status TEXT DEFAULT 'Pending',
          UNIQUE(region,crop,symptoms_key));
        """)


def create_report(p):
    with connect() as c:
        cur = c.execute(
            """INSERT INTO reports(crop,symptoms,suspected_disease,original_language,
        original_text,translated_text,location,date) VALUES(?,?,?,?,?,?,?,?)""",
            (
                p["crop"],
                p["symptoms"],
                p.get("suspected_disease", ""),
                p["original_language"],
                p["original_text"],
                p["translated_text"],
                p["location"],
                p["date"],
            ),
        )
        return cur.lastrowid


def rowdict(r):
    return dict(r) if r else None


def get_report(i):
    with connect() as c:
        return rowdict(c.execute("SELECT * FROM reports WHERE id=?", (i,)).fetchone())


def get_reports():
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM reports ORDER BY id DESC")]


def search_reports(crop="", location="", symptoms=""):
    q = "SELECT * FROM reports WHERE 1=1"
    args = []
    for field, val in [("crop", crop), ("location", location), ("symptoms", symptoms)]:
        if val:
            q += f" AND {field} LIKE ?"
            args.append("%" + val + "%")
    q += " ORDER BY id DESC"
    with connect() as c:
        return [dict(r) for r in c.execute(q, args)]


def get_alerts():
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM alerts ORDER BY created_at DESC")]


def update_alert(i, status):
    with connect() as c:
        cur = c.execute(
            "UPDATE alerts SET status=? WHERE id=?",
            (status, i),
        )
        return cur.rowcount > 0


def upsert_alert(region, crop, key, ids):
    with connect() as c:
        c.execute(
            """INSERT INTO alerts(region,crop,symptoms_key,report_count,report_ids,status)
        VALUES(?,?,?,?,?,'Pending') ON CONFLICT(region,crop,symptoms_key) DO UPDATE SET
        report_count=excluded.report_count, report_ids=excluded.report_ids, created_at=CURRENT_TIMESTAMP""",
            (region, crop, key, len(ids), json.dumps(ids)),
        )

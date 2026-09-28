import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_upgrade_preserves_data_and_quarantines_legacy_work(tmp_path):
    database = tmp_path / "legacy.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{database}", "APP_ENV": "test"}

    def run(*args):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr

    run("upgrade", "0001_initial")
    with sqlite3.connect(database) as conn:
        user = "12345678123456781234567812345678"
        conn.execute(
            "INSERT INTO users(id, phone, timezone, created_at) VALUES (?, ?, ?, ?)",
            (user, "5511999999999", "America/Sao_Paulo", "2026-09-26 12:00:00"),
        )
        for provider in ("google", "microsoft"):
            conn.execute(
                "INSERT INTO calendar_connections(user_id,provider,calendar_id,encrypted_access_token,active) VALUES (?,?,?,?,?)",
                (user, provider, "primary", "encrypted-placeholder", 1),
            )
        conn.execute(
            "INSERT INTO inbound_messages(external_id,phone,payload,correlation_id,status,received_at) VALUES (?,?,?,?,?,?)",
            ("old-webhook", "5511999999999", "{}", "migration", "queued", "2026-09-26 12:00:00"),
        )
    run("upgrade", "0003_event_reminders")
    with sqlite3.connect(database) as conn:
        conn.execute(
            "INSERT INTO events(id,user_id,title,category,start_at,provider,status,reminder_minutes) VALUES (?,?,?,?,?,?,?,?)",
            (
                "abcdef1234567890abcdef1234567890",
                user,
                "Evento antigo",
                "gym",
                "2026-10-01 18:00:00",
                "fake",
                "active",
                30,
            ),
        )
    run("upgrade", "head")
    run("check")
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT phone FROM users").fetchone()[0] == "5511999999999"
        assert (
            conn.execute("SELECT COUNT(*) FROM calendar_connections WHERE active=1").fetchone()[0]
            == 0
        )
        assert conn.execute("SELECT status, attempts FROM inbound_messages").fetchone() == (
            "needs_review",
            0,
        )
        assert conn.execute(
            "SELECT title, reminder_minutes, reminder_offsets_minutes, reminders_exclusive FROM events"
        ).fetchone() == ("Evento antigo", 30, None, 0)

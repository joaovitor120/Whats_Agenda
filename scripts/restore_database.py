"""Restore the agenda on Linux after preserving the current database."""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from backup_database import (
    ROOT,
    SUMMARY_SQL,
    UTC,
    archive_sha256,
    backup_database,
    database_command,
)


def run_docker(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=ROOT,
        capture_output=True,
        check=True,
        timeout=timeout,
    )


def restore_database(archive: Path) -> dict:
    archive = archive.resolve()
    metadata = json.loads(archive.with_suffix(archive.suffix + ".json").read_text("utf-8"))
    if archive_sha256(archive) != metadata["sha256"]:
        raise ValueError("O SHA256 nao confere; transfira novamente o backup e seu manifesto.")
    with archive.open("rb") as file:
        if file.read(5) != b"PGDMP":
            raise ValueError("O arquivo nao e um backup PostgreSQL valido.")

    container_archive = f"/tmp/agenda-restore-{uuid4().hex}.dump"
    run_docker("up", "-d", "--wait", "postgres")
    try:
        run_docker("cp", str(archive), f"postgres:{container_archive}")
        run_docker("exec", "-T", "postgres", "pg_restore", "--list", container_archive)
        run_docker("stop", "-t", "30", "api", "worker", "beat")
        running = run_docker("ps", "--status", "running", "--services", "api", "worker", "beat")
        if running.stdout.strip():
            raise ValueError("A API e os workers precisam estar parados antes da restauracao.")

        before = (
            ROOT / "backups" / (f"vps-antes-restauracao-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}.dump")
        )
        backup_database(before)
        print(f"Backup anterior da VPS: {before}", flush=True)
        print("Restaurando o banco em uma unica transacao...", flush=True)
        subprocess.run(
            database_command(
                'exec pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
                '--role="$APP_DB_USER" --clean --if-exists --single-transaction '
                '--exit-on-error --no-owner --no-privileges "$1"',
                container_archive,
            ),
            cwd=ROOT,
            capture_output=True,
            check=True,
            timeout=600,
        )
        summary = subprocess.run(
            database_command(
                'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "$1"', SUMMARY_SQL
            ),
            cwd=ROOT,
            capture_output=True,
            check=True,
            timeout=30,
        )
        counts = json.loads(summary.stdout)
        if counts != metadata["counts_before_backup"]:
            raise ValueError("As contagens diferem do manifesto; mantenha o bot parado e confira.")
        print(json.dumps(counts, ensure_ascii=False), flush=True)
        print("Banco restaurado. A API e os workers continuam parados.")
        print("Aplique migrations: docker compose run --rm migrate")
        print("Depois inicie: docker compose up -d api worker beat")
        return counts
    finally:
        run_docker("exec", "-T", "postgres", "rm", "-f", container_archive)


def main() -> None:
    parser = argparse.ArgumentParser(description="Restaura o banco completo na VPS Linux.")
    parser.add_argument("archive", type=Path, help="Backup .dump, acompanhado pelo .dump.json")
    parser.add_argument(
        "--replace-current-database",
        action="store_true",
        help="Substitui o banco atual, salvando antes uma copia em backups/",
    )
    args = parser.parse_args()
    if not sys.platform.startswith("linux"):
        parser.exit(1, "Execute esta restauracao na VPS Linux, nao no computador Windows.\n")
    if not args.replace_current_database:
        parser.exit(1, "A restauracao exige --replace-current-database.\n")
    try:
        restore_database(args.archive)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        if isinstance(error, ValueError):
            message = str(error)
        elif isinstance(error, subprocess.CalledProcessError):
            message = f"Um comando Docker/PostgreSQL falhou (codigo {error.returncode})."
        else:
            message = type(error).__name__
        parser.exit(1, f"Falha: {message} Nao reinicie o bot ate conferir a restauracao.\n")


if __name__ == "__main__":
    main()

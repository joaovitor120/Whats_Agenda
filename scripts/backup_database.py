"""Save a PostgreSQL archive without passing binary data through a shell pipeline."""

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

# Host Python may be 3.10 even though the application container uses 3.14.
UTC = timezone.utc  # noqa: UP017
ROOT = Path(__file__).resolve().parents[1]
SUMMARY_SQL = """
SELECT json_build_object(
    'schema_revision', (SELECT version_num FROM alembic_version),
    'users', (SELECT count(*) FROM users),
    'events', (SELECT count(*) FROM events),
    'active_events', (SELECT count(*) FROM events WHERE status = 'active'),
    'reminders', (SELECT count(*) FROM reminders),
    'inbound_messages', (SELECT count(*) FROM inbound_messages),
    'outbound_messages', (SELECT count(*) FROM outbound_messages)
);
"""


def database_command(command: str, *args: str) -> list[str]:
    return ["docker", "compose", "exec", "-T", "postgres", "sh", "-c", command, "backup", *args]


def archive_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as archive:
        for chunk in iter(lambda: archive.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup_database(output: Path) -> dict:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = output.with_suffix(output.suffix + ".json")
    if output.exists() or manifest.exists():
        raise ValueError("O arquivo de destino ja existe; escolha outro nome.")
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
    # Exclusive binary mode prevents both overwriting and PowerShell text conversion.
    created_archive = False
    created_manifest = False
    try:
        with output.open("xb") as archive:
            created_archive = True
            os.chmod(output, 0o600)
            subprocess.run(
                database_command(
                    'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
                    "--format=custom --no-owner --no-privileges"
                ),
                cwd=ROOT,
                stdout=archive,
                stderr=subprocess.PIPE,
                check=True,
                timeout=300,
            )
        with output.open("rb") as archive:
            if archive.read(5) != b"PGDMP":
                raise ValueError("O arquivo gerado nao e um backup PostgreSQL valido.")
        # Docker on Windows does not reliably forward a file handle as exec stdin.
        container_archive = f"/tmp/agenda-backup-{uuid4().hex}.dump"
        try:
            subprocess.run(
                ["docker", "compose", "cp", str(output), f"postgres:{container_archive}"],
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=True,
                timeout=60,
            )
            subprocess.run(
                [
                    "docker",
                    "compose",
                    "exec",
                    "-T",
                    "postgres",
                    "pg_restore",
                    "--list",
                    container_archive,
                ],
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=True,
                timeout=60,
            )
        finally:
            subprocess.run(
                ["docker", "compose", "exec", "-T", "postgres", "rm", "-f", container_archive],
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=True,
                timeout=30,
            )
        metadata = {
            "created_at": datetime.now(UTC).isoformat(),
            "archive": output.name,
            "sha256": archive_sha256(output),
            "bytes": output.stat().st_size,
            "counts_before_backup": counts,
        }
        with manifest.open("x", encoding="utf-8") as file:
            created_manifest = True
            os.chmod(manifest, 0o600)
            json.dump(metadata, file, indent=2)
            file.write("\n")
    except Exception:
        if created_archive:
            output.unlink(missing_ok=True)
        if created_manifest:
            manifest.unlink(missing_ok=True)
        raise
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description="Backup completo do banco da agenda no Docker.")
    default_name = f"agenda-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}.dump"
    parser.add_argument("--output", type=Path, default=ROOT / "backups" / default_name)
    args = parser.parse_args()
    try:
        metadata = backup_database(args.output)
    except (OSError, ValueError, subprocess.SubprocessError):
        parser.exit(1, "Falha ao salvar o backup. Confira se o PostgreSQL esta em execucao.\n")
    print(f"Backup: {args.output.resolve()}")
    print(f"SHA256: {metadata['sha256']}")
    print(json.dumps(metadata["counts_before_backup"], ensure_ascii=False))
    print("Arquivo sensivel: contem dados da agenda e conversas; mantenha fora do Git.")


if __name__ == "__main__":
    main()

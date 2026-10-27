"""Validate migrations in an isolated local PostgreSQL cluster, never a remote DB."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pg-bin", type=Path, help="Directory containing initdb, pg_ctl, and psql")
    args = parser.parse_args()
    binary = args.pg_bin or (Path(shutil.which("psql")).parent if shutil.which("psql") else None)
    if binary is None or not (binary / "initdb").exists():
        parser.exit(1, "Install PostgreSQL or supply --pg-bin. This check never uses a hosted database.\n")
    root = Path(__file__).resolve().parents[1]
    env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    with tempfile.TemporaryDirectory(prefix="uptodate-schema-", dir="/private/tmp") as directory:
        local = Path(directory)
        cluster = local / "db"
        subprocess.run([str(binary / "initdb"), "-D", str(cluster), "-U", "uptodate_schema_test",
                        "--auth-local=trust", "--auth-host=reject", "--no-locale", "--encoding=UTF8"],
                       check=True, env=env, capture_output=True, text=True)
        control = [str(binary / "pg_ctl"), "-D", str(cluster)]
        started = False
        try:
            subprocess.run([*control, "-l", str(local / "server.log"), "-o", f"-k {local} -p 55439 -h ''", "-w", "start"],
                           check=True, env=env)
            started = True
            psql = [str(binary / "psql"), "-X", "-v", "ON_ERROR_STOP=1", "-h", str(local), "-p", "55439",
                    "-U", "uptodate_schema_test", "-d", "postgres"]
            files = [root / "supabase/tests/local_bootstrap.sql",
                     *sorted((root / "supabase/migrations").glob("*.sql")), root / "supabase/tests/schema_contract.sql"]
            for path in files:
                print("Checking", path.relative_to(root), flush=True)
                subprocess.run([*psql, "-f", str(path)], check=True, env=env)
            print("Local PostgreSQL schema/RLS contracts passed. No hosted database was accessed.")
        finally:
            if started:
                subprocess.run([*control, "-m", "fast", "-w", "stop"], check=True, env=env)


if __name__ == "__main__":
    main()

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .models import Asset, GenerateRequest, Job


class JobStore:
    def __init__(self, home: Path):
        home.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = home / "jobs.sqlite3"
        with self.connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, request TEXT NOT NULL,
                    status TEXT NOT NULL, asset TEXT, error TEXT, log TEXT
                )
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, request: GenerateRequest) -> Job:
        job_id = uuid4().hex
        with self.connect() as db:
            db.execute(
                "INSERT INTO jobs (id, request, status) VALUES (?, ?, 'queued')",
                (job_id, request.model_dump_json()),
            )
        return Job(
            id=job_id, status="queued", provider=request.selected_provider(), mode=request.mode
        )

    def _job(self, row: sqlite3.Row) -> Job:
        request = GenerateRequest.model_validate_json(row["request"])
        return Job(
            id=row["id"],
            status=row["status"],
            provider=request.selected_provider(),
            mode=request.mode,
            asset=Asset.model_validate_json(row["asset"]) if row["asset"] else None,
            error=row["error"],
            log=row["log"],
        )

    def get(self, job_id: str) -> Job | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._job(row) if row else None

    def recent(self, limit: int) -> list[Job]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM jobs ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        return [self._job(row) for row in rows]

    def pending(self) -> list[str]:
        with self.connect() as db:
            rows = db.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY rowid").fetchall()
        return [row["id"] for row in rows]

    @staticmethod
    def fail_orphaned(db: sqlite3.Connection) -> None:
        db.execute("""
            UPDATE jobs SET status='failed', error='Worker exited before completing the job.'
            WHERE status='running'
        """)

    def claim(self, job_id: str, log: Path) -> GenerateRequest | None:
        # The caller holds the cross-process engine lock. A running job at this point
        # belonged to a process that exited before recording its terminal status.
        with self.connect() as db:
            self.fail_orphaned(db)
            row = db.execute(
                """
                UPDATE jobs SET status='running', log=? WHERE id=? AND status='queued'
                RETURNING request
            """,
                (str(log), job_id),
            ).fetchone()
        return GenerateRequest.model_validate_json(row["request"]) if row else None

    def finish(self, job_id: str, *, asset: Asset | None = None, error: str | None = None) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET status=?, asset=?, error=? WHERE id=? AND status='running'",
                (
                    "succeeded" if asset else "failed",
                    asset.model_dump_json() if asset else None,
                    error,
                    job_id,
                ),
            )

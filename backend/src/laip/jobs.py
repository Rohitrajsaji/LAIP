"""PostgreSQL queue with owner-first locks, database leases and fenced publication.

Use an autocommit connection, or commit the caller's outer transaction before doing
external work. Publication callbacks receive this connection and must do DB work only.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


class LeaseLost(ValueError):
    """Expired, replaced, cancelled or terminal work cannot publish."""


class NondeterministicResult(ValueError):
    """A logical publication key already has a different fingerprint."""


@dataclass(frozen=True)
class Lease:
    job_id: str
    run_id: str
    kind: str
    worker_id: str
    fence: int
    lease_token: str
    attempt: int


@dataclass(frozen=True)
class PublicationKey:
    artifact_id: str
    provider_id: str
    provider_version: str
    source_revision: str | None
    configuration_sha256: str
    stage: str


class JobRepository:
    def __init__(self, connection: psycopg.Connection[Any]):
        self.connection = connection

    def _one(self, query: str, args: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, args)
            return cursor.fetchone()

    def _all(self, query: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, args)
            return cursor.fetchall()

    @staticmethod
    def _owner(job: dict[str, Any]) -> tuple[str, str, str]:
        if job["kind"] in ("analysis", "import"):
            return "runs", "run_id", job["owner_run_id"]
        if job["kind"] == "export":
            return "exports", "export_id", job["export_id"]
        return "retention_operations", "retention_id", job["retention_id"]

    def _lock(self, job_id: str, *, skip: bool = False) -> dict[str, Any] | None:
        # Advisory discovery takes no lock. All mutations lock owner before job.
        job = self._one("SELECT * FROM jobs WHERE job_id=%s", (job_id,))
        if job is None:
            return None
        table, column, owner_id = self._owner(job)
        suffix = " SKIP LOCKED" if skip else ""
        owner = self._one(
            f"SELECT state FROM {table} WHERE {column}=%s FOR UPDATE{suffix}", (owner_id,)
        )
        if owner is None:
            return None
        job = self._one(f"SELECT * FROM jobs WHERE job_id=%s FOR UPDATE{suffix}", (job_id,))
        if job is not None:
            job["owner_state"] = owner["state"]
        return job

    def _owner_state(self, job: dict[str, Any], state: str) -> None:
        table, column, owner_id = self._owner(job)
        terminal = state in ("succeeded", "partial", "failed", "cancelled")
        self.connection.execute(
            f"UPDATE {table} SET state=%s,completed_at="
            f"{'clock_timestamp()' if terminal else 'NULL'} WHERE {column}=%s",
            (state, owner_id),
        )

        if job["kind"] == "import":
            import_state = {"succeeded": "sealed", "cancel_requested": "running"}.get(state, state)
            self.connection.execute(
                "UPDATE imports SET state=%s WHERE import_id=%s",
                (import_state, job["owner_import_id"]),
            )

    def _event(self, job: dict[str, Any], event: str) -> None:
        self.connection.execute(
            "INSERT INTO job_events(job_id,attempt,fence,event) VALUES(%s,%s,%s,%s)",
            (job["job_id"], job["attempt"], job["fence"], event),
        )

    def enqueue(self, job_id: str, run_id: str, kind: str, owner_id: str | None = None) -> None:
        if kind not in ("import", "analysis", "export", "retention"):
            raise ValueError("unsupported job kind")
        owner_id = owner_id or run_id
        with self.connection.transaction():
            if kind in ("export", "retention"):
                table = "exports" if kind == "export" else "retention_operations"
                column = "export_id" if kind == "export" else "retention_id"
                owner = self._one(f"SELECT run_id FROM {table} WHERE {column}=%s", (owner_id,))
                if owner is None or owner["run_id"] != run_id:
                    raise ValueError("owner provenance mismatch")
            elif owner_id != run_id:
                raise ValueError("owner provenance mismatch")
            self.connection.execute(
                "INSERT INTO jobs(job_id,run_id,kind,owner_run_id,export_id,retention_id) "
                "VALUES(%s,%s,%s,%s,%s,%s)",
                (
                    job_id,
                    run_id,
                    kind,
                    run_id if kind in ("import", "analysis") else None,
                    owner_id if kind == "export" else None,
                    owner_id if kind == "retention" else None,
                ),
            )

            if kind == "import":
                self.connection.execute(
                    "UPDATE imports SET state='queued' WHERE import_id="
                    "(SELECT import_id FROM runs WHERE run_id=%s)",
                    (run_id,),
                )

    def claim(
        self,
        worker_id: str,
        lease_seconds: int = 60,
        *,
        authorized_job_ids: list[str] | None = None,
    ) -> Lease | None:
        if not worker_id or not 1 <= lease_seconds <= 3600:
            raise ValueError("invalid worker or lease duration")
        with self.connection.transaction():
            candidates = self._all(
                "SELECT job_id FROM jobs WHERE state IN ('queued','retry_wait') "
                "AND available_at<=clock_timestamp() "
                "AND (%s::text[] IS NULL OR job_id=ANY(%s::text[])) "
                "ORDER BY available_at,created_at,job_id LIMIT 100",
                (authorized_job_ids, authorized_job_ids),
            )
            for candidate in candidates:
                job = self._lock(candidate["job_id"], skip=True)
                if job is None or job["state"] not in ("queued", "retry_wait"):
                    continue
                if job["owner_state"] in ("succeeded", "partial", "failed", "cancelled"):
                    self._terminal(job, "cancelled", update_owner=False)
                    continue
                row = self._one(
                    "UPDATE jobs SET state='running',attempt=attempt+1,fence=fence+1,"
                    "worker_id=%s,lease_token=(fence+1)::text||':'||%s,"
                    "heartbeat_at=clock_timestamp(),lease_expires_at=clock_timestamp()+"
                    "make_interval(secs=>%s) WHERE job_id=%s AND available_at<=clock_timestamp() "
                    "AND NOT cancel_requested AND attempt<3 RETURNING *",
                    (worker_id, str(uuid4()), lease_seconds, job["job_id"]),
                )
                if row is None:
                    continue
                self._owner_state(row, "running")
                self.connection.execute(
                    "INSERT INTO job_attempts(job_id,attempt,fence,worker_id) VALUES(%s,%s,%s,%s)",
                    (row["job_id"], row["attempt"], row["fence"], worker_id),
                )
                self._event(row, "claimed")
                return Lease(**{name: row[name] for name in Lease.__dataclass_fields__})
        return None

    def heartbeat(self, lease: Lease, lease_seconds: int = 60) -> bool:
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("invalid lease duration")
        with self.connection.transaction():
            row = self._one(
                "UPDATE jobs SET heartbeat_at=clock_timestamp(),lease_expires_at="
                "clock_timestamp()+make_interval(secs=>%s) WHERE job_id=%s AND worker_id=%s "
                "AND fence=%s AND lease_token=%s AND state='running' "
                "AND lease_expires_at>clock_timestamp() AND NOT cancel_requested RETURNING job_id",
                (lease_seconds, lease.job_id, lease.worker_id, lease.fence, lease.lease_token),
            )
            return row is not None

    def _live(self, lease: Lease, *, allow_cancel: bool = False) -> dict[str, Any]:
        job = self._lock(lease.job_id)
        if job is None:
            raise LeaseLost("lease lost")
        live = self._one(
            "SELECT lease_expires_at>clock_timestamp() AS live FROM jobs WHERE job_id=%s",
            (lease.job_id,),
        )
        if (
            job["state"] != "running"
            or job["run_id"] != lease.run_id
            or job["kind"] != lease.kind
            or job["attempt"] != lease.attempt
            or job["worker_id"] != lease.worker_id
            or job["fence"] != lease.fence
            or job["lease_token"] != lease.lease_token
            or live is None
            or not live["live"]
            or (job["cancel_requested"] and not allow_cancel)
        ):
            raise LeaseLost("lease lost or cancellation requested")
        return job

    def _terminal(self, job: dict[str, Any], outcome: str, *, update_owner: bool = True) -> None:
        self.connection.execute(
            "UPDATE jobs SET state=%s,completed_at=clock_timestamp(),worker_id=NULL,"
            "lease_token=NULL,lease_expires_at=NULL WHERE job_id=%s",
            (outcome, job["job_id"]),
        )
        self.connection.execute(
            "UPDATE job_attempts SET outcome=%s,finished_at=clock_timestamp() "
            "WHERE job_id=%s AND attempt=%s",
            (outcome, job["job_id"], job["attempt"]),
        )
        if update_owner:
            self._owner_state(job, outcome)
        self._event(job, outcome)

    def finish(self, lease: Lease, outcome: str) -> str:
        if outcome not in ("succeeded", "partial", "failed", "cancelled"):
            raise ValueError("invalid outcome")
        with self.connection.transaction():
            job = self._live(lease, allow_cancel=True)
            effective = "cancelled" if job["cancel_requested"] else outcome
            self._terminal(job, effective)
            return effective

    def cancel(self, job_id: str, reason: str) -> str:
        if not reason or len(reason) > 1000:
            raise ValueError("bounded cancellation reason required")
        with self.connection.transaction():
            job = self._lock(job_id)
            if job is None:
                raise KeyError(job_id)
            if job["state"] in ("succeeded", "partial", "failed"):
                raise ValueError("owner terminal")
            if job["state"] == "cancelled":
                return "cancelled"
            self.connection.execute(
                "UPDATE jobs SET cancel_requested=true,cancel_reason=%s WHERE job_id=%s",
                (reason, job_id),
            )
            if job["state"] in ("queued", "retry_wait"):
                self._terminal(job, "cancelled")
                return "cancelled"
            self._owner_state(job, "cancel_requested")
            self._event(job, "cancel_requested")
            return "cancel_requested"

    def recover(self) -> int:
        count = 0
        with self.connection.transaction():
            candidates = self._all(
                "SELECT job_id FROM jobs WHERE state='running' "
                "AND lease_expires_at<=clock_timestamp() ORDER BY job_id LIMIT 100"
            )
            for candidate in candidates:
                job = self._lock(candidate["job_id"], skip=True)
                if job is None or job["state"] != "running":
                    continue
                expired = self._one(
                    "SELECT lease_expires_at<=clock_timestamp() AS expired "
                    "FROM jobs WHERE job_id=%s",
                    (job["job_id"],),
                )
                if expired is None or not expired["expired"]:
                    continue
                self.connection.execute(
                    "UPDATE jobs SET fence=fence+1 WHERE job_id=%s", (job["job_id"],)
                )
                self.connection.execute(
                    "UPDATE job_attempts SET outcome='lease_expired',finished_at=clock_timestamp() "
                    "WHERE job_id=%s AND attempt=%s",
                    (job["job_id"], job["attempt"]),
                )
                job["fence"] += 1
                if job["cancel_requested"] or job["attempt"] >= 3:
                    self._terminal(job, "cancelled" if job["cancel_requested"] else "failed")
                else:
                    self.connection.execute(
                        "UPDATE jobs SET state='retry_wait',worker_id=NULL,lease_token=NULL,"
                        "lease_expires_at=NULL,available_at=clock_timestamp()+"
                        "make_interval(secs=>%s) WHERE job_id=%s",
                        (min(20, 5 * 2 ** (job["attempt"] - 1)), job["job_id"]),
                    )
                    self._event(job, "retry_wait")
                count += 1
        return count

    def publish_checkpoint(
        self,
        lease: Lease,
        key: PublicationKey,
        fingerprint: str,
        publish: Callable[[psycopg.Connection[Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        with self.connection.transaction():
            job = self._live(lease)
            if job["kind"] not in ("analysis", "import"):
                raise ValueError("artifact publication requires analysis/import job")
            scope = self._one(
                "SELECT 1 FROM run_artifacts a JOIN runs r ON "
                "a.system_namespace=r.system_namespace WHERE a.artifact_id=%s "
                "AND r.run_id=%s AND a.run_id=r.run_id",
                (key.artifact_id, lease.run_id),
            )
            if scope is None:
                raise ValueError("cross namespace or missing artifact")
            frozen = self._one(
                "SELECT 1 FROM run_providers p JOIN runs r USING(run_id) WHERE "
                "p.run_id=%s AND p.provider_id=%s AND p.provider_version=%s AND "
                "p.source_revision IS NOT DISTINCT FROM %s AND "
                "r.payload->>'configuration_sha256'=%s",
                (
                    lease.run_id,
                    key.provider_id,
                    key.provider_version,
                    key.source_revision,
                    key.configuration_sha256,
                ),
            )
            if frozen is None:
                raise ValueError("PUBLICATION_PROVENANCE_MISMATCH")
            args = (
                lease.run_id,
                key.artifact_id,
                key.provider_id,
                key.provider_version,
                key.source_revision,
                key.configuration_sha256,
                key.stage,
            )
            old = self._one(
                "SELECT fingerprint,payload FROM artifact_results WHERE run_id=%s "
                "AND artifact_id=%s "
                "AND provider_id=%s AND provider_version=%s "
                "AND source_revision IS NOT DISTINCT FROM %s "
                "AND configuration_sha256=%s AND stage=%s",
                args,
            )
            if old is not None:
                if old["fingerprint"] != fingerprint:
                    raise NondeterministicResult("publication fingerprint changed")
                return dict(old["payload"])
            payload = publish(self.connection)
            # Recheck DB time after callback. No external IO belongs in a callback.
            self._live(lease)
            self.connection.execute(
                "INSERT INTO artifact_results(run_id,artifact_id,provider_id,provider_version,"
                "source_revision,configuration_sha256,stage,fingerprint,payload,job_id) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (*args, fingerprint, Jsonb(payload), lease.job_id),
            )
            self._event(job, "checkpoint")
            return payload

    def publish_export(
        self,
        lease: Lease,
        fingerprint: str,
        publish: Callable[[psycopg.Connection[Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        """Pin and deduplicate an export publication without changing its basis run."""
        with self.connection.transaction():
            job = self._live(lease)
            if job["kind"] != "export":
                raise ValueError("export publication requires export job")
            export = self._one("SELECT * FROM exports WHERE export_id=%s", (job["export_id"],))
            assert export is not None
            args = tuple(
                export[name]
                for name in (
                    "export_id",
                    "snapshot_id",
                    "options_sha256",
                    "masking_policy_version",
                    "schema_version",
                )
            )
            old = self._one(
                "SELECT fingerprint,payload FROM export_results WHERE export_id=%s "
                "AND snapshot_id=%s AND options_sha256=%s "
                "AND masking_policy_version=%s AND schema_version=%s",
                args,
            )
            if old is not None:
                if old["fingerprint"] != fingerprint:
                    raise NondeterministicResult("export fingerprint changed")
                return dict(old["payload"])
            payload = publish(self.connection)
            self._live(lease)
            self.connection.execute(
                "INSERT INTO export_results(export_id,snapshot_id,options_sha256,"
                "masking_policy_version,schema_version,fingerprint,payload) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s)",
                (*args, fingerprint, Jsonb(payload)),
            )
            self._event(job, "export_checkpoint")
            return payload

    def retry(self, lease: Lease) -> str:
        """Release a live lease after a retryable failure, preserving checkpoints."""
        with self.connection.transaction():
            job = self._live(lease, allow_cancel=True)
            self.connection.execute(
                "UPDATE jobs SET fence=fence+1 WHERE job_id=%s", (job["job_id"],)
            )
            job["fence"] += 1
            if job["cancel_requested"] or job["attempt"] >= 3:
                outcome = "cancelled" if job["cancel_requested"] else "failed"
                self._terminal(job, outcome)
                return outcome
            self.connection.execute(
                "UPDATE job_attempts SET outcome='retryable_failure',finished_at=clock_timestamp() "
                "WHERE job_id=%s AND attempt=%s",
                (job["job_id"], job["attempt"]),
            )
            self.connection.execute(
                "UPDATE jobs SET state='retry_wait',worker_id=NULL,lease_token=NULL,"
                "lease_expires_at=NULL,available_at=clock_timestamp()+"
                "make_interval(secs=>%s) WHERE job_id=%s",
                (min(20, 5 * 2 ** (job["attempt"] - 1)), job["job_id"]),
            )
            self._event(job, "retry_wait")
            return "retry_wait"

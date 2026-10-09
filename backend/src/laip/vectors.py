"""Optional operator-installed vector storage. No embedding/model connections occur here."""

import hashlib
import math
from importlib.resources import files
from typing import Any

import psycopg
from psycopg import sql


class VectorRepository:
    def __init__(self, connection: psycopg.Connection[Any], namespace: str):
        self.connection = connection
        self.namespace = namespace

    def install(
        self,
        model_id: str,
        model_sha256: str,
        dimension: int,
        policy_version: str,
        *,
        approved: bool = False,
    ) -> None:
        if not approved:
            raise ValueError("VECTOR_PROFILE_APPROVAL_REQUIRED")
        if (
            not model_id
            or len(model_id) > 128
            or not policy_version
            or not 1 <= dimension <= 2000
            or len(model_sha256) != 64
            or any(c not in "0123456789abcdef" for c in model_sha256)
        ):
            raise ValueError("INVALID_VECTOR_PROFILE")
        raw = files("laip").joinpath("optional/vector.sql").read_bytes()
        checksum = hashlib.sha256(raw).hexdigest()
        with self.connection.transaction():
            self.connection.execute("SELECT pg_advisory_xact_lock(1280198992)")
            self.connection.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
            existing = self.connection.execute(
                "SELECT checksum FROM schema_extensions WHERE version='vector-0.1.0'"
            ).fetchone()
            if existing:
                if existing[0] != checksum:
                    raise ValueError("VECTOR_MIGRATION_CHECKSUM_MISMATCH")
            else:
                self.connection.execute(raw.decode())
                self.connection.execute(
                    "INSERT INTO schema_extensions(version,checksum) VALUES('vector-0.1.0',%s)",
                    (checksum,),
                )
            self.connection.execute(
                "INSERT INTO "
                "embedding_models(model_id,system_namespace,model_sha256,dimension,po"
                "licy_version,distance) VALUES(%s,%s,%s,%s,%s,'cosine') ON CONFLICT "
                "DO NOTHING",
                (model_id, self.namespace, model_sha256, dimension, policy_version),
            )
            profile = self._profile(model_id)
            if profile != (model_sha256, dimension, policy_version):
                raise ValueError("VECTOR_PROFILE_MISMATCH")
            name = "vectors_" + hashlib.sha256(model_id.encode()).hexdigest()[:24]
            self.connection.execute(
                sql.SQL(
                    "CREATE INDEX IF NOT EXISTS {} ON chunk_embeddings USING hnsw "
                    "((embedding::public.vector({})) public.vector_cosine_ops) WHERE "
                    "model_id={}"
                ).format(sql.Identifier(name), sql.Literal(dimension), sql.Literal(model_id))
            )

    def _profile(self, model_id: str) -> tuple[str, int, str]:
        row = self.connection.execute(
            "SELECT model_sha256,dimension,policy_version FROM embedding_models WHERE "
            "model_id=%s AND system_namespace=%s",
            (model_id, self.namespace),
        ).fetchone()
        if row is None:
            raise ValueError("VECTOR_MODEL_UNAVAILABLE")
        return str(row[0]), int(row[1]), str(row[2])

    def profile(self, model_id: str) -> dict[str, Any]:
        """Return the installed namespace-bound model basis without enabling it."""
        row = self.connection.execute("SELECT to_regclass('chunk_embeddings')").fetchone()
        if row is None or row[0] is None:
            raise ValueError("VECTOR_PROVIDER_UNAVAILABLE")
        revision, dimension, policy = self._profile(model_id)
        return {"model_sha256": revision, "dimension": dimension, "policy_version": policy}

    @staticmethod
    def _vector(values: list[float], dimension: int) -> str:
        if len(values) != dimension:
            raise ValueError("VECTOR_DIMENSION_MISMATCH")
        if any(not math.isfinite(v) for v in values) or not any(values):
            raise ValueError("INVALID_VECTOR")
        return "[" + ",".join(str(float(value)) for value in values) + "]"

    def put(self, model_id: str, chunk_id: str, values: list[float]) -> None:
        with self.connection.transaction():
            _, dimension, policy = self._profile(model_id)
            vector = self._vector(values, dimension)
            self.connection.execute(
                "INSERT INTO "
                "chunk_embeddings(model_id,chunk_id,system_namespace,policy_version,e"
                "mbedding) VALUES(%s,%s,%s,%s,%s::public.vector) ON CONFLICT DO "
                "NOTHING",
                (model_id, chunk_id, self.namespace, policy, vector),
            )
            row = self.connection.execute(
                "SELECT embedding OPERATOR(public.=) %s::public.vector FROM chunk_embeddings WHERE "
                "model_id=%s AND chunk_id=%s",
                (vector, model_id, chunk_id),
            ).fetchone()
            if not row or not row[0]:
                raise ValueError("VECTOR_PAYLOAD_MISMATCH")

    def nearest(
        self, model_id: str, values: list[float], *, snapshot_id: str, limit: int = 20
    ) -> list[str]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("INVALID_QUERY_LIMIT")
        try:
            with self.connection.transaction():
                previous_timeout = self.connection.execute("SHOW statement_timeout").fetchone()
                self.connection.execute("SELECT set_config('statement_timeout','30000',true)")
                _, dimension, _ = self._profile(model_id)
                vector = self._vector(values, dimension)
                rows = self.connection.execute(
                    sql.SQL(
                        "SELECT v.chunk_id FROM chunk_embeddings v JOIN chunks c "
                        "USING(chunk_id) WHERE v.model_id=%s AND "
                        "v.system_namespace=%s AND c.system_namespace=%s AND c.snapshot_id=%s "
                        "ORDER BY v.embedding::public.vector({}) OPERATOR(public.<=>) "
                        "%s::public.vector({}) LIMIT %s"
                    ).format(sql.Literal(dimension), sql.Literal(dimension)),
                    (model_id, self.namespace, self.namespace, snapshot_id, vector, limit),
                ).fetchall()
                if previous_timeout is not None:
                    self.connection.execute(
                        "SELECT set_config('statement_timeout',%s,true)", (previous_timeout[0],)
                    )
        except psycopg.errors.QueryCanceled:
            raise ValueError("VECTOR_QUERY_LIMIT") from None
        return [str(row[0]) for row in rows]

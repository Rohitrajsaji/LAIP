CREATE TABLE exports (
 export_id text PRIMARY KEY,
 run_id text NOT NULL REFERENCES runs(run_id),
 snapshot_id text NOT NULL REFERENCES snapshots(snapshot_id),
 options_sha256 text NOT NULL CHECK (options_sha256 ~ '^[a-f0-9]{64}$'),
 masking_policy_version text NOT NULL,
 schema_version text NOT NULL DEFAULT '0.1.0',
 state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','running','cancel_requested','succeeded','partial','failed','cancelled')),
 completed_at timestamptz,
 CHECK ((state IN ('succeeded','partial','failed','cancelled')) = (completed_at IS NOT NULL))
);
CREATE TABLE retention_operations (
 retention_id text PRIMARY KEY,
 run_id text NOT NULL REFERENCES runs(run_id),
 policy jsonb NOT NULL DEFAULT '{}',
 state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','running','cancel_requested','succeeded','partial','failed','cancelled')),
 completed_at timestamptz,
 CHECK ((state IN ('succeeded','partial','failed','cancelled')) = (completed_at IS NOT NULL))
);
CREATE TABLE jobs (
 job_id text PRIMARY KEY,
 run_id text NOT NULL REFERENCES runs(run_id),
 kind text NOT NULL CHECK(kind IN ('import','analysis','export','retention')),
 owner_run_id text REFERENCES runs(run_id),
 export_id text REFERENCES exports(export_id),
 retention_id text REFERENCES retention_operations(retention_id),
 state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','retry_wait','running','succeeded','partial','failed','cancelled')),
 attempt integer NOT NULL DEFAULT 0 CHECK(attempt BETWEEN 0 AND 3),
 fence bigint NOT NULL DEFAULT 0 CHECK(fence >= 0),
 worker_id text,
 lease_token text,
 heartbeat_at timestamptz,
 lease_expires_at timestamptz,
 cancel_requested boolean NOT NULL DEFAULT false,
 cancel_reason text,
 available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 completed_at timestamptz,
 CHECK ((kind IN ('import','analysis') AND owner_run_id = run_id AND export_id IS NULL AND retention_id IS NULL)
 OR (kind='export' AND owner_run_id IS NULL AND export_id IS NOT NULL AND retention_id IS NULL)
 OR (kind='retention' AND owner_run_id IS NULL AND export_id IS NULL AND retention_id IS NOT NULL)),
 CHECK ((state='running') = (worker_id IS NOT NULL AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)),
 CHECK ((state IN ('succeeded','partial','failed','cancelled')) = (completed_at IS NOT NULL))
);
CREATE INDEX jobs_queue ON jobs(available_at,created_at,job_id) WHERE state IN ('queued','retry_wait');
CREATE INDEX jobs_expiry ON jobs(lease_expires_at) WHERE state='running';
CREATE TABLE job_attempts (
 job_id text NOT NULL REFERENCES jobs(job_id),
 attempt integer NOT NULL,
 fence bigint NOT NULL,
 worker_id text NOT NULL,
 started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 finished_at timestamptz,
 outcome text,
 PRIMARY KEY(job_id,attempt)
);
CREATE TABLE job_events (
 sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 job_id text NOT NULL REFERENCES jobs(job_id),
 attempt integer NOT NULL,
 fence bigint NOT NULL,
 event text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE artifact_results (
 result_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 run_id text NOT NULL REFERENCES runs(run_id),
 artifact_id text NOT NULL REFERENCES artifacts(artifact_id),
 provider_id text NOT NULL,
 provider_version text NOT NULL,
 source_revision text,
 configuration_sha256 text NOT NULL CHECK(configuration_sha256 ~ '^[a-f0-9]{64}$'),
 stage text NOT NULL,
 fingerprint text NOT NULL CHECK(fingerprint ~ '^[a-f0-9]{64}$'),
 payload jsonb NOT NULL,
 job_id text NOT NULL REFERENCES jobs(job_id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE NULLS NOT DISTINCT(run_id,artifact_id,provider_id,provider_version,source_revision,configuration_sha256,stage)
);
CREATE TABLE export_results (
 export_id text NOT NULL REFERENCES exports(export_id),
 snapshot_id text NOT NULL REFERENCES snapshots(snapshot_id),
 options_sha256 text NOT NULL,
 masking_policy_version text NOT NULL,
 schema_version text NOT NULL,
 fingerprint text NOT NULL CHECK(fingerprint ~ '^[a-f0-9]{64}$'),
 payload jsonb NOT NULL,
 UNIQUE(export_id,snapshot_id,options_sha256,masking_policy_version,schema_version)
);
ALTER TABLE exports ADD UNIQUE(export_id,run_id);
ALTER TABLE retention_operations ADD UNIQUE(retention_id,run_id);
ALTER TABLE jobs ADD UNIQUE(job_id,run_id);
ALTER TABLE jobs ADD FOREIGN KEY(export_id,run_id) REFERENCES exports(export_id,run_id);
ALTER TABLE jobs ADD FOREIGN KEY(retention_id,run_id) REFERENCES retention_operations(retention_id,run_id);
ALTER TABLE artifact_results ADD FOREIGN KEY(run_id,artifact_id) REFERENCES run_artifacts(run_id,artifact_id);
ALTER TABLE artifact_results ADD FOREIGN KEY(job_id,run_id) REFERENCES jobs(job_id,run_id);
CREATE FUNCTION check_export_namespace() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS (SELECT 1 FROM runs r JOIN snapshots s ON r.system_namespace=s.system_namespace
   WHERE r.run_id=NEW.run_id AND s.snapshot_id=NEW.snapshot_id) THEN
   RAISE EXCEPTION 'export snapshot namespace mismatch' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER export_namespace BEFORE INSERT OR UPDATE ON exports
 FOR EACH ROW EXECUTE FUNCTION check_export_namespace();

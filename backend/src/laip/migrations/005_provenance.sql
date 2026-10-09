-- Relational upstream provenance and provider revisions cannot escape a run's frozen scope.
ALTER TABLE evidence ADD COLUMN upstream_artifact_id text;
ALTER TABLE evidence DISABLE TRIGGER immutable_history;
UPDATE evidence SET upstream_artifact_id=payload#>>'{upstream_record,payload_artifact_id}';
ALTER TABLE evidence ENABLE TRIGGER immutable_history;
ALTER TABLE evidence ADD FOREIGN KEY(run_id,upstream_artifact_id) REFERENCES run_artifacts(run_id,artifact_id);
ALTER TABLE evidence ADD CHECK(upstream_artifact_id IS NOT DISTINCT FROM payload#>>'{upstream_record,payload_artifact_id}');
CREATE INDEX evidence_upstream ON evidence(upstream_artifact_id,run_id);
ALTER TABLE run_providers ADD COLUMN source_revision_key text GENERATED ALWAYS AS (coalesce('revision:'||source_revision,'none')) STORED;
ALTER TABLE run_providers ADD UNIQUE(run_id,provider_id,provider_version,source_revision_key);
ALTER TABLE artifact_results ADD COLUMN source_revision_key text GENERATED ALWAYS AS (coalesce('revision:'||source_revision,'none')) STORED;
ALTER TABLE artifact_results ADD FOREIGN KEY(run_id,provider_id,provider_version,source_revision_key) REFERENCES run_providers(run_id,provider_id,provider_version,source_revision_key);
CREATE FUNCTION validate_result_configuration() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM runs WHERE run_id=NEW.run_id AND payload->>'configuration_sha256'=NEW.configuration_sha256) THEN
  RAISE EXCEPTION 'result configuration differs from frozen run' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER result_configuration BEFORE INSERT ON artifact_results FOR EACH ROW EXECUTE FUNCTION validate_result_configuration();

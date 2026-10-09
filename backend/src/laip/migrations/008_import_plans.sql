-- Accepted uploads/checkouts can resume from private immutable bytes, never a changing caller tree.
ALTER TABLE artifacts ADD UNIQUE(artifact_id,import_id);
CREATE TABLE import_plans(
 run_id text PRIMARY KEY,import_id text NOT NULL,system_namespace text NOT NULL,
 plan_artifact_id text NOT NULL,configuration_sha256 text NOT NULL,
 FOREIGN KEY(run_id,import_id) REFERENCES runs(run_id,import_id),
 FOREIGN KEY(run_id,system_namespace) REFERENCES runs(run_id,system_namespace),
 FOREIGN KEY(import_id,system_namespace) REFERENCES imports(import_id,system_namespace),
 FOREIGN KEY(plan_artifact_id,import_id) REFERENCES artifacts(artifact_id,import_id),
 FOREIGN KEY(plan_artifact_id,system_namespace) REFERENCES artifacts(artifact_id,system_namespace)
);
CREATE TABLE import_input_blobs(
 run_id text NOT NULL REFERENCES import_plans(run_id),sha256 text NOT NULL REFERENCES blobs(sha256),
 PRIMARY KEY(run_id,sha256)
);
CREATE INDEX import_input_blobs_sha256 ON import_input_blobs(sha256);
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON import_plans FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON import_input_blobs FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();

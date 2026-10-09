CREATE TABLE analyzer_fact_bundles(
 bundle_id text PRIMARY KEY,
 run_id text NOT NULL,
 artifact_id text NOT NULL,
 system_namespace text NOT NULL,
 payload jsonb NOT NULL,
 FOREIGN KEY(run_id,system_namespace) REFERENCES runs(run_id,system_namespace),
 FOREIGN KEY(artifact_id,system_namespace) REFERENCES artifacts(artifact_id,system_namespace)
);
CREATE INDEX analyzer_fact_bundle_artifact ON analyzer_fact_bundles(system_namespace,artifact_id);
CREATE INDEX analyzer_fact_bundle_run ON analyzer_fact_bundles(system_namespace,run_id);
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON analyzer_fact_bundles FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
CREATE TABLE rule_subjects(
 revision_id text NOT NULL,
 entity_id text NOT NULL,
 system_namespace text NOT NULL,
 PRIMARY KEY(revision_id,entity_id),
 FOREIGN KEY(revision_id,system_namespace) REFERENCES rule_revisions(revision_id,system_namespace),
 FOREIGN KEY(entity_id,system_namespace) REFERENCES entities(entity_id,system_namespace)
);
CREATE INDEX rule_subject_entity ON rule_subjects(system_namespace,entity_id);
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON rule_subjects FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();

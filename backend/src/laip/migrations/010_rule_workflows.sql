CREATE TABLE rule_bundle_bases(
 run_id text PRIMARY KEY,
 system_namespace text NOT NULL,
 payload jsonb NOT NULL,
 FOREIGN KEY(run_id,system_namespace) REFERENCES runs(run_id,system_namespace)
);
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON rule_bundle_bases FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
CREATE TABLE workflow_revisions(
 workflow_id text PRIMARY KEY,
 run_id text NOT NULL,
 entity_id text NOT NULL,
 system_namespace text NOT NULL,
 payload jsonb NOT NULL,
 FOREIGN KEY(run_id,system_namespace) REFERENCES runs(run_id,system_namespace),
 FOREIGN KEY(entity_id,system_namespace) REFERENCES entities(entity_id,system_namespace)
);
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON workflow_revisions FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
CREATE INDEX workflow_entity_run ON workflow_revisions(system_namespace,entity_id,run_id);
CREATE INDEX workflow_run ON workflow_revisions(run_id,system_namespace);

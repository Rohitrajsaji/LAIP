-- Import occurrences cannot link artifacts across systems; sealed status history is immutable.
ALTER TABLE import_entries ADD COLUMN system_namespace text;
UPDATE import_entries e SET system_namespace=i.system_namespace FROM imports i WHERE e.import_id=i.import_id;
ALTER TABLE import_entries ALTER COLUMN system_namespace SET NOT NULL;
ALTER TABLE import_entries ADD FOREIGN KEY(import_id,system_namespace) REFERENCES imports(import_id,system_namespace);
ALTER TABLE import_entries ADD FOREIGN KEY(artifact_id,system_namespace) REFERENCES artifacts(artifact_id,system_namespace);
CREATE INDEX import_entries_artifact ON import_entries(artifact_id,system_namespace);
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON import_entries FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();

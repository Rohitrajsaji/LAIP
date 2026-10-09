-- Durable processing state for imports is owned by exactly one import job.
ALTER TABLE imports DROP CONSTRAINT imports_state_check;
ALTER TABLE imports ADD CHECK(state IN ('staging','queued','running','sealed','partial','failed','cancelled'));
ALTER TABLE runs ADD UNIQUE(run_id,import_id);
ALTER TABLE jobs ADD COLUMN owner_import_id text REFERENCES imports(import_id);
UPDATE jobs j SET owner_import_id=r.import_id FROM runs r WHERE j.owner_run_id=r.run_id AND j.kind='import';
ALTER TABLE jobs ADD UNIQUE(owner_import_id);
ALTER TABLE jobs ADD FOREIGN KEY(owner_run_id,owner_import_id) REFERENCES runs(run_id,import_id);
CREATE FUNCTION bind_import_job_owner() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.kind='import' THEN
  SELECT import_id INTO NEW.owner_import_id FROM runs WHERE run_id=NEW.owner_run_id;
 ELSE NEW.owner_import_id:=NULL;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER import_job_owner BEFORE INSERT ON jobs FOR EACH ROW EXECUTE FUNCTION bind_import_job_owner();
ALTER TABLE jobs ADD CHECK((kind='import')=(owner_import_id IS NOT NULL));

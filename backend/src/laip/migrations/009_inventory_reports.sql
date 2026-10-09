CREATE TABLE inventory_reports(
 run_id text PRIMARY KEY,
 import_id text NOT NULL,
 system_namespace text NOT NULL,
 configuration_sha256 text NOT NULL CHECK(configuration_sha256 ~ '^[a-f0-9]{64}$'),
 payload jsonb NOT NULL,
 FOREIGN KEY(run_id,system_namespace) REFERENCES runs(run_id,system_namespace),
 FOREIGN KEY(run_id,import_id) REFERENCES runs(run_id,import_id),
 FOREIGN KEY(import_id,system_namespace) REFERENCES imports(import_id,system_namespace)
);
CREATE INDEX inventory_reports_namespace_import ON inventory_reports(system_namespace,import_id);
CREATE TRIGGER inventory_reports_immutable BEFORE UPDATE OR DELETE ON inventory_reports
 FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
CREATE FUNCTION validate_inventory_report_basis() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM runs r WHERE r.run_id=NEW.run_id
 AND r.import_id=NEW.import_id AND r.system_namespace=NEW.system_namespace
 AND r.payload->>'configuration_sha256'=NEW.configuration_sha256
 AND r.payload->>'stage'='offline_inventory') THEN
  RAISE EXCEPTION 'Inventory report basis differs from frozen run' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER inventory_report_basis BEFORE INSERT ON inventory_reports
 FOR EACH ROW EXECUTE FUNCTION validate_inventory_report_basis();

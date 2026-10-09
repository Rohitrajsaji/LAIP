CREATE FUNCTION reject_history_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'immutable history cannot be changed' USING ERRCODE='23514'; END $$;
DO $$ DECLARE name text; BEGIN
 FOREACH name IN ARRAY ARRAY['systems','entities','entity_observations','artifacts','origin_maps','origin_map_segments','run_providers','evidence','evidence_subjects','evidence_support','evidence_contradictions','dependencies','dependency_candidates','dependency_evidence','rule_revisions','rule_evidence','rule_scopes','reviews','snapshots','snapshot_members','chunks','chunk_links','artifact_results','export_results','embedding_models','job_events','audit_events'] LOOP
  EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION reject_history_mutation()',name);
 END LOOP;
END $$;
-- Each accepted operation owns one durable job; retries use the same row/attempt history.
ALTER TABLE jobs ADD UNIQUE(owner_run_id);
ALTER TABLE jobs ADD UNIQUE(export_id);
ALTER TABLE jobs ADD UNIQUE(retention_id);

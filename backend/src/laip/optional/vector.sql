CREATE TABLE chunk_embeddings(model_id text NOT NULL,chunk_id text NOT NULL,system_namespace text NOT NULL,policy_version text NOT NULL,embedding public.vector NOT NULL,PRIMARY KEY(model_id,chunk_id),FOREIGN KEY(model_id,system_namespace,policy_version) REFERENCES embedding_models(model_id,system_namespace,policy_version),FOREIGN KEY(chunk_id,system_namespace,policy_version) REFERENCES chunks(chunk_id,system_namespace,policy_version));
CREATE INDEX embedding_chunk ON chunk_embeddings(chunk_id,system_namespace,policy_version);
CREATE FUNCTION validate_embedding_dimension() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF public.vector_dims(NEW.embedding) <> (SELECT dimension FROM embedding_models WHERE model_id=NEW.model_id) THEN
  RAISE EXCEPTION 'embedding dimension mismatch' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER embedding_dimension BEFORE INSERT OR UPDATE ON chunk_embeddings FOR EACH ROW EXECUTE FUNCTION validate_embedding_dimension();

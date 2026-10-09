CREATE TABLE analyst_job_plans (
 job_id text PRIMARY KEY REFERENCES jobs(job_id),
 system_namespace text NOT NULL REFERENCES systems(system_namespace),
 payload jsonb NOT NULL,
 CHECK (jsonb_typeof(payload)='object')
);
CREATE INDEX analyst_job_plans_namespace ON analyst_job_plans(system_namespace,job_id);

"""Enforce per-organization job-state capacity counters in PostgreSQL.

Revision ID: 0008_job_capacity_enforcement
Revises: 0007_b2b_lifecycle

The API and worker transition jobs in separate processes and some proven
upload/enqueue paths intentionally commit in several durability stages.  A
database trigger therefore owns the counters: every writer gets the same
atomic limits and a crashed process cannot leave application-maintained
counters out of sync.
"""

from alembic import op

revision = "0008_job_capacity_enforcement"
down_revision = "0007_b2b_lifecycle"
branch_labels = None
depends_on = None


_FUNCTION = """
CREATE OR REPLACE FUNCTION instadescribe_enforce_job_capacity()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    old_bucket text;
    new_bucket text;
    release_reservation boolean := false;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        old_bucket := CASE
            WHEN OLD.status = 'AWAITING_UPLOAD' THEN 'awaiting'
            WHEN OLD.status IN ('UPLOAD_COMPLETE', 'QUEUED') THEN 'queued'
            WHEN OLD.status = 'PROCESSING' THEN 'processing'
            ELSE NULL
        END;
    END IF;
    IF TG_OP <> 'DELETE' THEN
        new_bucket := CASE
            WHEN NEW.status = 'AWAITING_UPLOAD' THEN 'awaiting'
            WHEN NEW.status IN ('UPLOAD_COMPLETE', 'QUEUED') THEN 'queued'
            WHEN NEW.status = 'PROCESSING' THEN 'processing'
            ELSE NULL
        END;
    END IF;
    IF TG_OP = 'DELETE' THEN
        release_reservation := true;
    ELSIF TG_OP = 'UPDATE'
          AND NEW.status IN ('FAILED', 'CANCELLED')
          AND OLD.status NOT IN ('FAILED', 'CANCELLED') THEN
        release_reservation := true;
    END IF;

    IF TG_OP = 'UPDATE'
       AND OLD.organization_id = NEW.organization_id
       AND old_bucket IS NOT DISTINCT FROM new_bucket
       AND NOT release_reservation THEN
        RETURN NEW;
    END IF;

    IF old_bucket IS NOT NULL THEN
        UPDATE organization_job_capacity
        SET awaiting_upload_jobs = awaiting_upload_jobs
                - CASE WHEN old_bucket = 'awaiting' THEN 1 ELSE 0 END,
            queued_jobs = queued_jobs
                - CASE WHEN old_bucket = 'queued' THEN 1 ELSE 0 END,
            processing_jobs = processing_jobs
                - CASE WHEN old_bucket = 'processing' THEN 1 ELSE 0 END,
            version = version + 1,
            updated_at = now()
        WHERE organization_id = OLD.organization_id;
    END IF;

    IF new_bucket IS NOT NULL THEN
        INSERT INTO organization_quotas (organization_id)
        VALUES (NEW.organization_id)
        ON CONFLICT (organization_id) DO NOTHING;
        INSERT INTO organization_job_capacity (organization_id)
        VALUES (NEW.organization_id)
        ON CONFLICT (organization_id) DO NOTHING;

        IF new_bucket = 'awaiting' THEN
            UPDATE organization_job_capacity AS capacity
            SET awaiting_upload_jobs = capacity.awaiting_upload_jobs + 1,
                version = capacity.version + 1,
                updated_at = now()
            FROM organization_quotas AS quota
            WHERE capacity.organization_id = NEW.organization_id
              AND quota.organization_id = NEW.organization_id
              AND capacity.awaiting_upload_jobs < quota.max_awaiting_upload_jobs;
        ELSIF new_bucket = 'queued' THEN
            UPDATE organization_job_capacity AS capacity
            SET queued_jobs = capacity.queued_jobs + 1,
                version = capacity.version + 1,
                updated_at = now()
            FROM organization_quotas AS quota
            WHERE capacity.organization_id = NEW.organization_id
              AND quota.organization_id = NEW.organization_id
              AND capacity.queued_jobs < quota.max_queued_jobs;
        ELSE
            UPDATE organization_job_capacity AS capacity
            SET processing_jobs = capacity.processing_jobs + 1,
                version = capacity.version + 1,
                updated_at = now()
            FROM organization_quotas AS quota
            WHERE capacity.organization_id = NEW.organization_id
              AND quota.organization_id = NEW.organization_id
              AND capacity.processing_jobs < quota.max_processing_jobs;
        END IF;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'organization job capacity exceeded'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'organization_job_capacity_limit';
        END IF;
    END IF;

    IF release_reservation THEN
        UPDATE organization_usage_periods AS usage
        SET reserved_media_seconds = usage.reserved_media_seconds - reservation.reserved_seconds,
            version = usage.version + 1,
            updated_at = now()
        FROM quota_reservations AS reservation
        WHERE reservation.organization_id = OLD.organization_id
          AND reservation.job_id = OLD.id
          AND reservation.state = 'reserved'
          AND usage.organization_id = reservation.organization_id
          AND usage.id = reservation.usage_period_id
          AND usage.reserved_media_seconds >= reservation.reserved_seconds;
        IF FOUND THEN
            UPDATE quota_reservations
            SET state = 'released', finalized_at = now(), updated_at = now()
            WHERE organization_id = OLD.organization_id
              AND job_id = OLD.id
              AND state = 'reserved';
        END IF;
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$
"""


def upgrade() -> None:
    op.drop_constraint("event_type_valid", "job_events", type_="check")
    op.create_check_constraint(
        "event_type_valid",
        "job_events",
        "event_type IN ('job.needs_review', 'job.completed', 'job.failed', "
        "'job.cancelled', 'render.requested')",
    )
    op.execute(_FUNCTION)
    op.execute(
        "CREATE TRIGGER trg_jobs_enforce_organization_capacity "
        "BEFORE INSERT OR DELETE OR UPDATE OF status, organization_id ON jobs "
        "FOR EACH ROW EXECUTE FUNCTION instadescribe_enforce_job_capacity()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_jobs_enforce_organization_capacity ON jobs")
    op.execute("DROP FUNCTION IF EXISTS instadescribe_enforce_job_capacity()")
    op.execute("DELETE FROM job_events WHERE event_type = 'render.requested'")
    op.drop_constraint("event_type_valid", "job_events", type_="check")
    op.create_check_constraint(
        "event_type_valid",
        "job_events",
        "event_type IN ('job.needs_review', 'job.completed', 'job.failed', 'job.cancelled')",
    )

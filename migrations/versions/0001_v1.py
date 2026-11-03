"""Schéma initial de l’API V1 : personnes, organisations, postes, lots et historique métier."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_v1"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "import_batch",
        sa.Column("import_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("file_format", sa.String(length=100), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column("inserted_count", sa.Integer(), nullable=False),
        sa.Column("updated_count", sa.Integer(), nullable=False),
        sa.Column("unchanged_count", sa.Integer(), nullable=False),
        sa.Column("rejected_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint(
            "status IN ('processing', 'completed', 'completed_with_errors', 'failed')",
            name="ck_import_status",
        ),
        sa.CheckConstraint(
            "total_rows = inserted_count + updated_count + unchanged_count + rejected_count",
            name="ck_import_total",
        ),
        sa.CheckConstraint(
            "total_rows >= 0 AND inserted_count >= 0 AND updated_count >= 0 AND unchanged_count >= 0 AND rejected_count >= 0",
            name="ck_import_counts",
        ),
        sa.PrimaryKeyConstraint("import_id"),
    )
    op.create_index(
        op.f("ix_import_batch_file_hash"), "import_batch", ["file_hash"], unique=False
    )
    op.create_table(
        "organization",
        sa.Column("org_id", sa.BigInteger(), nullable=False),
        sa.Column("org_name", sa.Text(), nullable=False),
        sa.Column("org_vivo_uri", sa.Text(), nullable=True),
        sa.CheckConstraint("length(btrim(org_name)) > 0", name="ck_org_name"),
        sa.PrimaryKeyConstraint("org_id"),
        sa.UniqueConstraint("org_vivo_uri", name="uq_org_uri"),
    )
    op.create_index(
        op.f("ix_organization_org_name"), "organization", ["org_name"], unique=False
    )
    op.create_index(
        "uq_org_name_without_uri",
        "organization",
        ["org_name"],
        unique=True,
        postgresql_where=sa.text("org_vivo_uri IS NULL"),
    )
    op.create_table(
        "person",
        sa.Column("person_id", sa.BigInteger(), nullable=False),
        sa.Column("first_name", sa.Text(), nullable=False),
        sa.Column("middle_name", sa.Text(), nullable=True),
        sa.Column("last_name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("fax", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "length(btrim(first_name)) > 0 AND length(btrim(last_name)) > 0",
            name="ck_person_names",
        ),
        sa.PrimaryKeyConstraint("person_id"),
        sa.UniqueConstraint(
            "first_name",
            "middle_name",
            "last_name",
            name="uq_person_identity",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_table(
        "job",
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("person_id", sa.BigInteger(), nullable=False),
        sa.Column("org_id", sa.BigInteger(), nullable=False),
        sa.Column("job_title", sa.Text(), nullable=True),
        sa.Column("job_type", sa.Text(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["organization.org_id"]),
        sa.ForeignKeyConstraint(["person_id"], ["person.person_id"]),
        sa.PrimaryKeyConstraint("job_id"),
        sa.UniqueConstraint(
            "person_id",
            "org_id",
            "job_title",
            "job_type",
            "start_date",
            name="uq_job_identity",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(op.f("ix_job_org_id"), "job", ["org_id"], unique=False)
    op.create_index(op.f("ix_job_person_id"), "job", ["person_id"], unique=False)
    op.create_table(
        "person_history",
        sa.Column("history_id", sa.BigInteger(), nullable=False),
        sa.Column("person_id", sa.BigInteger(), nullable=False),
        sa.Column("import_id", sa.Uuid(), nullable=False),
        sa.Column("old_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("new_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "changed_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["import_id"], ["import_batch.import_id"]),
        sa.ForeignKeyConstraint(["person_id"], ["person.person_id"]),
        sa.PrimaryKeyConstraint("history_id"),
    )
    op.create_index(
        op.f("ix_person_history_import_id"),
        "person_history",
        ["import_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_person_history_person_id"),
        "person_history",
        ["person_id"],
        unique=False,
    )


def downgrade():
    op.drop_table("person_history")
    op.drop_table("job")
    op.drop_table("person")
    op.drop_table("organization")
    op.drop_table("import_batch")

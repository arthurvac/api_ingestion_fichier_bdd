"""Conserver les URI VIVO partagées sans fusionner les organisations."""

from alembic import op
import sqlalchemy as sa

revision = "0002_shared_vivo_uri"
down_revision = "0001_v1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint(
        "uq_org_identity",
        "organization",
        ["org_name", "org_vivo_uri"],
        postgresql_nulls_not_distinct=True,
    )
    op.drop_constraint("uq_org_uri", "organization", type_="unique")
    op.drop_index("uq_org_name_without_uri", table_name="organization")


def downgrade():
    # Un retour incompatible échoue atomiquement, sans supprimer les URI partagées.
    op.create_unique_constraint("uq_org_uri", "organization", ["org_vivo_uri"])
    op.create_index(
        "uq_org_name_without_uri",
        "organization",
        ["org_name"],
        unique=True,
        postgresql_where=sa.text("org_vivo_uri IS NULL"),
    )
    op.drop_constraint("uq_org_identity", "organization", type_="unique")

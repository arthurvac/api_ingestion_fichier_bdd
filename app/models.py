import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Person(Base):
    __tablename__ = "person"
    __table_args__ = (
        UniqueConstraint(
            "first_name",
            "middle_name",
            "last_name",
            name="uq_person_identity",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "length(btrim(first_name)) > 0 AND length(btrim(last_name)) > 0",
            name="ck_person_names",
        ),
    )
    person_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    first_name: Mapped[str] = mapped_column(Text)
    middle_name: Mapped[str | None] = mapped_column(Text)
    last_name: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    fax: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)


class Organization(Base):
    __tablename__ = "organization"
    __table_args__ = (
        UniqueConstraint(
            "org_name",
            "org_vivo_uri",
            name="uq_org_identity",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint("length(btrim(org_name)) > 0", name="ck_org_name"),
    )
    org_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    org_name: Mapped[str] = mapped_column(Text, index=True)
    org_vivo_uri: Mapped[str | None] = mapped_column(Text)


class Job(Base):
    __tablename__ = "job"
    __table_args__ = (
        UniqueConstraint(
            "person_id",
            "org_id",
            "job_title",
            "job_type",
            "start_date",
            name="uq_job_identity",
            postgresql_nulls_not_distinct=True,
        ),
    )
    job_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("person.person_id"), index=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organization.org_id"), index=True)
    job_title: Mapped[str | None] = mapped_column(Text)
    job_type: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[date | None] = mapped_column(Date)


class ImportBatch(Base):
    __tablename__ = "import_batch"
    __table_args__ = (
        CheckConstraint(
            "status IN ('processing', 'completed', 'completed_with_errors', 'failed')",
            name="ck_import_status",
        ),
        CheckConstraint(
            "total_rows >= 0 AND inserted_count >= 0 AND updated_count >= 0 AND unchanged_count >= 0 AND rejected_count >= 0",
            name="ck_import_counts",
        ),
        CheckConstraint(
            "total_rows = inserted_count + updated_count + unchanged_count + rejected_count",
            name="ck_import_total",
        ),
    )
    import_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    filename: Mapped[str] = mapped_column(String(255))
    file_format: Mapped[str] = mapped_column(String(100))
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="processing")
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    inserted_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    unchanged_count: Mapped[int] = mapped_column(Integer, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provenance: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class PersonHistory(Base):
    __tablename__ = "person_history"
    history_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("person.person_id"), index=True)
    import_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("import_batch.import_id"), index=True
    )
    old_data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    new_data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    changed_fields: Mapped[list[str]] = mapped_column(JSONB)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

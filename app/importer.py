import hashlib
import time
from collections import OrderedDict
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.dates import DateDetector
from app.errors import ImportFailure, Issue, fail
from app.mapping import map_values, resolve_columns
from app.models import ImportBatch, Job, Organization, Person, PersonHistory
from app.readers import read_tables
from app.schema import (
    IDENTITY_FIELDS,
    JobData,
    Options,
    PersonData,
    Record,
    UploadResponse,
    normalize_header,
)


def reconcile_person(session, data, import_id, created, cache):
    identity = tuple(getattr(data, field) for field in IDENTITY_FIELDS)
    current = cache.pop(identity, None)
    if current is None:
        current = session.scalar(
            select(Person)
            .where(
                *(
                    getattr(Person, field).is_not_distinct_from(value)
                    for field, value in zip(IDENTITY_FIELDS, identity)
                )
            )
            .with_for_update()
        )
    if current is None:
        current = Person(**data.model_dump())
        session.add(current)
        session.flush()
        created.add(current.person_id)
        outcome = "inserted"
    else:
        old = {name: getattr(current, name) for name in PersonData.model_fields}
        changes = {
            name: getattr(data, name)
            for name, field in PersonData.model_fields.items()
            if (field.json_schema_extra or {}).get("update")
            and getattr(data, name) is not None
            and getattr(data, name) != old[name]
        }
        for name, value in changes.items():
            setattr(current, name, value)
        audited = sorted(
            name
            for name in changes
            if (PersonData.model_fields[name].json_schema_extra or {}).get("history")
        )
        if audited and current.person_id not in created:
            history_fields = {
                name
                for name, field in PersonData.model_fields.items()
                if name in IDENTITY_FIELDS
                or (field.json_schema_extra or {}).get("history")
            }
            session.add(
                PersonHistory(
                    person_id=current.person_id,
                    import_id=import_id,
                    old_data={
                        name: value
                        for name, value in old.items()
                        if name in history_fields
                    },
                    new_data={
                        name: value
                        for name, value in (old | changes).items()
                        if name in history_fields
                    },
                    changed_fields=audited,
                )
            )
        outcome = "updated" if changes else "unchanged"
    cache[identity] = current
    if len(cache) > 1000:
        cache.popitem(last=False)
    return current, outcome


def get_or_create_organization(session, data):
    if data.org_vivo_uri:
        found = session.scalar(
            select(Organization).where(Organization.org_vivo_uri == data.org_vivo_uri)
        )
        if found:
            return found.org_id
        found = session.scalar(
            select(Organization).where(
                Organization.org_name == data.org_name,
                Organization.org_vivo_uri.is_(None),
            )
        )
        if found:
            found.org_vivo_uri = data.org_vivo_uri
            session.flush()
            return found.org_id
    else:
        matches = list(
            session.scalars(
                select(Organization)
                .where(Organization.org_name == data.org_name)
                .limit(2)
            )
        )
        if len(matches) > 1:
            fail(
                "ambiguous_organization",
                "Multiple organizations have this name; provide an organization URI.",
                409,
                column="org_name",
            )
        if matches:
            return matches[0].org_id
    entity = Organization(**data.model_dump())
    session.add(entity)
    session.flush()
    return entity.org_id


def insert_job(session, person_id, org_id, data):
    statement = (
        insert(Job)
        .values(person_id=person_id, org_id=org_id, **data.model_dump())
        .on_conflict_do_nothing(constraint="uq_job_identity")
        .returning(Job.job_id)
    )
    return session.scalar(statement) is not None


def _record(row, columns, dates):
    values = map_values(row.values, columns)
    if "job" in values and "start_date" in values["job"]:
        values["job"]["start_date"] = dates.parse(
            values["job"]["start_date"],
            {
                "row": row.number,
                "column": columns["job.start_date"].source,
                "sheet": row.sheet,
            },
        )
    if "job" in values and not values.get("organization", {}).get("org_name"):
        fail(
            "missing_organization",
            "A job requires an organization name.",
            row=row.number,
            column=(
                columns["organization.org_name"].source
                if "organization.org_name" in columns
                else "org_name"
            ),
            sheet=row.sheet,
        )
    try:
        return Record.model_validate(values)
    except ValidationError as exc:
        issues = []
        for error in exc.errors(include_input=False, include_url=False):
            target = ".".join(str(part) for part in error["loc"])
            section, _, name = target.partition(".")
            missing = values.get(section, {}).get(name) is None
            message = (
                "Missing required value"
                if missing
                else (
                    "Invalid email address"
                    if target == "person.email"
                    else "Invalid value or type; text fields must remain text."
                )
            )
            issues.append(
                Issue(
                    code="missing_value" if missing else "invalid_value",
                    message=message,
                    row=row.number,
                    sheet=row.sheet,
                    column=(
                        columns[target].source if target in columns else name or None
                    ),
                )
            )
        raise ImportFailure("File validation failed", issues) from None


def _check_duplicates(record, seen, uris, row, columns):
    identity = tuple(getattr(record.person, field) for field in IDENTITY_FIELDS)
    previous = seen.setdefault(identity, {})
    for name, field in PersonData.model_fields.items():
        value = getattr(record.person, name)
        if not (field.json_schema_extra or {}).get("update") or value is None:
            continue
        if name in previous and previous[name] != value:
            fail(
                "conflicting_person_attributes",
                "Rows with the same person identity contain conflicting values.",
                row=row.number,
                column=columns[f"person.{name}"].source,
                sheet=row.sheet,
            )
        previous[name] = value
    org = record.organization
    if org and org.org_vivo_uri:
        previous_name = uris.setdefault(org.org_vivo_uri, org.org_name)
        if previous_name != org.org_name:
            fail(
                "ambiguous_organization_uri",
                "One organization URI refers to multiple organization names.",
                row=row.number,
                column=columns["organization.org_vivo_uri"].source,
                sheet=row.sheet,
            )


def import_file(
    file, filename: str, options: Options, session: Session, settings: Settings
) -> UploadResponse:
    deadline = time.monotonic() + settings.import_timeout_seconds

    def check_deadline():
        if time.monotonic() >= deadline:
            fail(
                "processing_timeout",
                "The import exceeded its time limit; no data was changed.",
                503,
            )

    def sql_budget():
        check_deadline()
        session.execute(
            text("SELECT set_config('statement_timeout', :value, true)"),
            {"value": str(max(1, int((deadline - time.monotonic()) * 1000)))},
        )

    checksum, size = hashlib.sha256(), 0
    file.seek(0)
    while block := file.read(1024 * 1024):
        check_deadline()
        size += len(block)
        if size > settings.max_upload_bytes:
            fail("file_too_large", "The file exceeds the upload size limit.", 413)
        checksum.update(block)
    with read_tables(file, filename, options, settings) as (
        kind,
        tables,
        warnings,
        physical,
    ):
        resolved = []
        for table in tables:
            columns, column_warnings = resolve_columns(
                table.headers, options, table.sheet
            )
            warnings.extend(column_warnings)
            resolved.append((table, columns))
        annual_aliases = {
            normalize_header(name)
            for name in JobData.model_fields["start_date"].json_schema_extra[
                "annual_aliases"
            ]
        }
        date_sources = [
            columns["job.start_date"].source
            for _, columns in resolved
            if "job.start_date" in columns
        ]
        allow_year = options.year_policy == "january_first" or (
            options.year_policy is None
            and bool(date_sources)
            and all(normalize_header(name) in annual_aliases for name in date_sources)
        )
        dates, total = DateDetector(allow_year), 0
        for table, columns in resolved:
            for row in table.rows(settings, kind):
                check_deadline()
                total += 1
                if total > settings.max_rows:
                    fail(
                        "too_many_rows",
                        "The maximum row count was exceeded.",
                        413,
                        row=row.number,
                        sheet=row.sheet,
                    )
                if "job.start_date" in columns:
                    column = columns["job.start_date"]
                    dates.observe(
                        row.values[column.position],
                        {
                            "row": row.number,
                            "column": column.source,
                            "sheet": row.sheet,
                        },
                    )
        if not total:
            fail("empty_dataset", "The file contains no data rows.")
        dates.resolve()
        if dates.annual_seen:
            warnings.append(
                Issue(
                    code="annual_precision",
                    message="Year-only dates were stored as January 1 under the declared source policy.",
                )
            )
        errors, seen, uris, cache = [], {}, {}, OrderedDict()
        created, updated, touched = set(), set(), set()
        counts = dict(inserted=0, updated=0, unchanged=0)
        jobs_created, current_row = 0, None
        try:
            with session.begin():
                sql_budget()
                session.execute(
                    text("SELECT set_config('lock_timeout', :value, true)"),
                    {
                        "value": str(
                            max(1, int(settings.database_lock_timeout_seconds * 1000))
                        )
                    },
                )
                # Le verrou SQL protège aussi les imports reçus par d'autres instances.
                session.execute(text("SELECT pg_advisory_xact_lock(728194021)"))
                batch = ImportBatch(
                    filename=filename,
                    file_format=kind,
                    file_hash=checksum.hexdigest(),
                    status="processing",
                )
                session.add(batch)
                session.flush()
                for table, columns in resolved:
                    for row in table.rows(settings, kind):
                        current_row = row
                        sql_budget()
                        try:
                            record = _record(row, columns, dates)
                            _check_duplicates(record, seen, uris, row, columns)
                        except ImportFailure as exc:
                            errors.extend(
                                issue.model_copy(
                                    update={
                                        "row": issue.row or row.number,
                                        "sheet": issue.sheet or row.sheet,
                                    }
                                )
                                for issue in exc.errors
                            )
                            if len(errors) >= settings.max_errors:
                                raise ImportFailure(
                                    "File validation failed",
                                    errors[: settings.max_errors],
                                    truncated=True,
                                ) from None
                            continue
                        person, outcome = reconcile_person(
                            session, record.person, batch.import_id, created, cache
                        )
                        touched.add(person.person_id)
                        if outcome == "updated":
                            updated.add(person.person_id)
                        counts[outcome] += 1
                        if record.organization:
                            org_id = get_or_create_organization(
                                session, record.organization
                            )
                            if record.job:
                                jobs_created += insert_job(
                                    session, person.person_id, org_id, record.job
                                )
                        session.flush()
                if errors:
                    raise ImportFailure(
                        "File validation failed", errors[: settings.max_errors]
                    )
                sql_budget()
                batch.status = "completed"
                batch.total_rows = total
                for name, value in counts.items():
                    setattr(batch, name + "_count", value)
                batch.completed_at = datetime.now(timezone.utc)
                batch.provenance = {
                    "reader": physical,
                    "jobs_created": jobs_created,
                    "year_policy": "january_first" if allow_year else "reject",
                }
                session.flush()
                import_id = batch.import_id
        except IntegrityError:
            fail(
                "database_constraint",
                "The import violates a database constraint; no data was changed.",
                409,
                row=current_row.number if current_row else None,
            )
        except DBAPIError as exc:
            state = getattr(exc.orig, "sqlstate", None)
            if state == "55P03":
                fail(
                    "import_busy",
                    "Another transaction holds a required lock; retry the import.",
                    409,
                )
            if state == "57014":
                fail(
                    "processing_timeout",
                    "A database operation timed out; no data was changed.",
                    503,
                )
            fail(
                "transaction_failed",
                "The database transaction failed; no data was changed.",
                503,
            )
        if len(warnings) > settings.max_errors:
            warnings = warnings[: settings.max_errors - 1] + [
                Issue(
                    code="warnings_truncated",
                    message="Additional column warnings were omitted.",
                )
            ]
        return UploadResponse(
            import_id=import_id,
            rows_processed=total,
            persons_created=len(created),
            persons_updated=len(updated - created),
            persons_unchanged=len(touched - created - updated),
            jobs_created=jobs_created,
            warnings=warnings,
        )

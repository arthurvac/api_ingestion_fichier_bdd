import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Event
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.importer as importer
from app.config import Settings, get_settings
from app.database import get_session
from app.main import create_app
from app.models import ImportBatch, Job, Organization, Person, PersonHistory
from tests.unit.test_files import csv_bytes, xlsx_bytes

pytestmark = pytest.mark.integration


def post(client, headers, rows, *, kind="csv", options=None):
    content = csv_bytes(headers, rows) if kind == "csv" else xlsx_bytes(headers, rows)
    return client.post(
        "/upload",
        files={"file": ("source." + kind, content)},
        data={"options": json.dumps(options)} if options else {},
    )


def number(session, model):
    return session.scalar(select(func.count()).select_from(model))


def counts(factory):
    with factory() as session:
        return [
            number(session, model)
            for model in (Person, Organization, Job, PersonHistory, ImportBatch)
        ]


def make_app(factory, settings=None):
    app = create_app(settings)

    def sessions():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = sessions
    return app


@pytest.mark.parametrize("kind", ["csv", "xlsx"])
def test_creation_and_identical_reimport_have_real_counts(
    api_client, session_factory, kind
):
    headers = ["first_name", "last_name", "organization", "job_type"]
    rows = [["Alice", "Example", "Institute", "Permanent"]]
    first = post(api_client, headers, rows, kind=kind)
    assert first.status_code == 200
    assert first.json()["success"] is True
    assert first.json()["message"] == "File imported successfully"
    assert (
        first.json()["persons_created"],
        first.json()["persons_updated"],
        first.json()["jobs_created"],
    ) == (1, 0, 1)
    second = post(api_client, headers, rows, kind=kind)
    assert second.status_code == 200
    assert (
        second.json()["persons_created"],
        second.json()["persons_updated"],
        second.json()["persons_unchanged"],
        second.json()["jobs_created"],
    ) == (0, 0, 1, 0)
    assert counts(session_factory) == [1, 1, 1, 0, 2]


def test_update_history_and_preservation_of_missing_or_empty_contacts(
    api_client, session_factory
):
    first = post(
        api_client,
        [
            "first_name",
            "middle_name",
            "last_name",
            "email",
            "phone",
            "fax",
            "person_title",
        ],
        [
            [
                " Alice   Marie ",
                "  ",
                "Example",
                "old@example.org",
                "00123",
                "00456",
                "Dr",
            ]
        ],
    )
    assert first.status_code == 200
    changed = post(
        api_client,
        ["NOM", "PRÉNOM", "email", "phone"],
        [["Example", "Alice Marie", "new@example.org", ""]],
    )
    assert changed.status_code == 200 and changed.json()["persons_updated"] == 1
    unchanged = post(
        api_client, ["first_name", "last_name"], [["Alice Marie", "Example"]]
    )
    assert unchanged.status_code == 200 and unchanged.json()["persons_updated"] == 0
    with session_factory() as session:
        person = session.scalar(select(Person))
        assert (
            person.middle_name,
            person.email,
            person.phone,
            person.fax,
            person.title,
        ) == (None, "new@example.org", "00123", "00456", "Dr")
        history = session.scalar(select(PersonHistory))
        assert history.old_data["email"] == "old@example.org"
        assert history.new_data["email"] == "new@example.org"
        assert history.changed_fields == ["email"]
        assert history.new_data["phone"] == "00123"
        assert history.import_id == UUID(changed.json()["import_id"])
        assert history.changed_at.utcoffset().total_seconds() == 0
        assert number(session, Person) == number(session, PersonHistory) == 1


def test_middle_name_null_spaces_case_and_shared_email_preserve_identity_rules(
    api_client, session_factory
):
    first = post(
        api_client,
        ["first_name", "middle_name", "last_name", "email"],
        [
            ["Alice", "", "Example", "shared@example.org"],
            ["alice", "", "Example", "shared@example.org"],
            ["Alice", " Louise ", "Example", "shared@example.org"],
        ],
    )
    assert first.status_code == 200 and first.json()["persons_created"] == 3
    second = post(api_client, ["first_name", "last_name"], [[" Alice ", "Example"]])
    assert second.json()["persons_created"] == 0
    with session_factory() as session:
        assert number(session, Person) == 3 and number(session, PersonHistory) == 0


def test_duplicates_merge_compatible_contacts_and_preserve_multiple_jobs(
    api_client, session_factory
):
    headers = ["first_name", "last_name", "email", "phone", "organization", "job_title"]
    rows = [
        ["Alice", "Example", "alice@example.org", "", "Institute", "Researcher"],
        ["Alice", "Example", "", "00123", "Institute", "Director"],
        ["Alice", "Example", "alice@example.org", "00123", "Institute", "Director"],
    ]
    response = post(api_client, headers, rows)
    assert response.status_code == 200
    assert (
        response.json()["rows_processed"],
        response.json()["persons_created"],
        response.json()["persons_updated"],
        response.json()["jobs_created"],
    ) == (3, 1, 0, 2)
    with session_factory() as session:
        person = session.scalar(select(Person))
        assert person.phone == "00123" and person.email == "alice@example.org"
        assert number(session, PersonHistory) == 0
        jobs = list(session.scalars(select(Job)))
        assert len(jobs) == 2 and all(job.person_id == person.person_id for job in jobs)
        batch = session.scalar(select(ImportBatch))
        assert (
            batch.total_rows
            == batch.inserted_count + batch.updated_count + batch.unchanged_count
        )


@pytest.mark.parametrize("kind", ["csv", "xlsx"])
def test_conflicting_duplicate_identity_rolls_back_entire_file(
    api_client, session_factory, kind
):
    response = post(
        api_client,
        ["first_name", "last_name", "email"],
        [
            ["Alice", "Example", "a@example.org"],
            ["Other", "Person", "c@example.org"],
            ["Alice", "Example", "b@example.org"],
        ],
        kind=kind,
    )
    assert response.status_code == 422 and response.json()["success"] is False
    assert response.json()["errors"][0]["code"] == "conflicting_person_attributes"
    assert response.json()["errors"][0]["row"] == 4
    assert counts(session_factory) == [0, 0, 0, 0, 0]


@pytest.mark.parametrize("kind", ["csv", "xlsx"])
def test_invalid_optional_value_rolls_back_prior_updates_jobs_and_history(
    api_client, session_factory, kind
):
    baseline = post(
        api_client,
        ["first_name", "last_name", "email"],
        [["Alice", "Example", "old@example.org"]],
    )
    assert baseline.status_code == 200
    before = counts(session_factory)
    response = post(
        api_client,
        ["first_name", "last_name", "email", "organization", "job_title"],
        [
            ["Alice", "Example", "new@example.org", "New Institute", "Director"],
            ["Bob", "Other", "b@example.org", "New Institute", "Researcher"],
            ["Bad", "Row", "private-invalid-email", "Other Institute", "Manager"],
        ],
        kind=kind,
    )
    assert response.status_code == 422
    assert response.json()["message"] == "File validation failed"
    assert response.json()["errors"][0]["row"] == 4
    assert response.json()["errors"][0]["column"] == "email"
    assert response.json()["errors"][0]["message"] == "Invalid email address"
    assert "private-invalid-email" not in response.text
    assert counts(session_factory) == before
    with session_factory() as session:
        assert session.scalar(select(Person.email)) == "old@example.org"


def test_missing_name_and_numeric_phone_are_detailed_errors(
    api_client, session_factory
):
    response = post(
        api_client,
        ["first_name", "last_name", "phone"],
        [["", "Example", "00123"], ["Alice", "Example", 123]],
        kind="xlsx",
    )
    assert response.status_code == 422
    assert [issue["row"] for issue in response.json()["errors"]] == [2, 3]
    assert response.json()["errors"][0]["message"] == "Missing required value"
    assert response.json()["errors"][1]["column"] == "phone"
    assert counts(session_factory) == [0, 0, 0, 0, 0]


def test_empty_job_is_not_created_and_job_requires_organization(
    api_client, session_factory
):
    valid = post(
        api_client,
        ["first_name", "last_name", "organization", "job_title", "start_date"],
        [["Alice", "Example", "", "", ""]],
    )
    assert valid.status_code == 200 and valid.json()["jobs_created"] == 0
    invalid = post(
        api_client,
        ["first_name", "last_name", "job_title"],
        [["Bob", "Example", "Researcher"]],
    )
    assert invalid.status_code == 422
    assert invalid.json()["errors"][0]["code"] == "missing_organization"
    assert counts(session_factory) == [1, 0, 0, 0, 1]


def test_uri_priority_enrichment_and_ambiguous_name(api_client, session_factory):
    headers = ["first_name", "last_name", "org_name", "org_vivo_uri", "job_type"]
    assert (
        post(
            api_client, headers, [["Alice", "Example", "Institute", "", "Permanent"]]
        ).status_code
        == 200
    )
    assert (
        post(
            api_client,
            headers,
            [
                [
                    "Alice",
                    "Example",
                    "Institute",
                    "https://example.org/org/1",
                    "Permanent",
                ]
            ],
        ).status_code
        == 200
    )
    assert counts(session_factory) == [1, 1, 1, 0, 2]
    assert (
        post(
            api_client,
            headers,
            [["Bob", "Example", "Institute", "https://example.org/org/2", "Permanent"]],
        ).status_code
        == 200
    )
    before = counts(session_factory)
    ambiguous = post(
        api_client, headers, [["Carol", "Example", "Institute", "", "Permanent"]]
    )
    assert (
        ambiguous.status_code == 409
        and ambiguous.json()["errors"][0]["code"] == "ambiguous_organization"
    )
    assert counts(session_factory) == before


def test_shared_ontology_uri_cannot_merge_organizations(api_client, session_factory):
    response = post(
        api_client,
        ["first_name", "last_name", "org_name", "org_vivo_uri"],
        [
            ["Alice", "Example", "Institute A", "https://example.org/class/University"],
            ["Bob", "Example", "Institute B", "https://example.org/class/University"],
        ],
    )
    assert response.status_code == 422
    assert response.json()["errors"][0]["code"] == "ambiguous_organization_uri"
    assert counts(session_factory) == [0, 0, 0, 0, 0]


def test_date_detection_and_native_sql_date(api_client, session_factory):
    response = post(
        api_client,
        ["first_name", "last_name", "organization", "start_date"],
        [
            ["Alice", "Example", "Institute", "03/04/2024"],
            ["Bob", "Other", "Institute", "02/29/2024"],
        ],
    )
    assert response.status_code == 200
    with session_factory() as session:
        dates = list(session.scalars(select(Job.start_date).order_by(Job.job_id)))
        assert dates == [date(2024, 3, 4), date(2024, 2, 29)]
        assert (
            session.scalar(text("SELECT pg_typeof(start_date)::text FROM job LIMIT 1"))
            == "date"
        )
    ambiguous = post(
        api_client,
        ["first_name", "last_name", "organization", "start_date"],
        [["Other", "Person", "Institute", "03/04/2024"]],
    )
    assert ambiguous.status_code == 422
    assert ambiguous.json()["errors"][0]["code"] == "ambiguous_date_format"


def test_annual_precision_requires_policy_except_declared_legacy_aliases(
    api_client, session_factory
):
    headers = ["first_name", "last_name", "organization", "start_date"]
    response = post(api_client, headers, [["Alice", "Example", "Institute", "2020"]])
    assert response.status_code == 422 and counts(session_factory) == [0, 0, 0, 0, 0]
    response = post(
        api_client,
        headers,
        [["Alice", "Example", "Institute", "2020"]],
        options={"year_policy": "january_first"},
    )
    assert response.status_code == 200
    assert response.json()["warnings"][0]["code"] == "annual_precision"


def test_unknown_columns_and_explicit_mapping_are_supported(api_client):
    response = post(
        api_client,
        ["given", "family", "extra"],
        [["Alice", "Example", "unused"]],
        options={
            "mapping": {"person.first_name": "given", "person.last_name": "family"}
        },
    )
    assert response.status_code == 200
    assert response.json()["warnings"][0]["column"] == "extra"
    ambiguous = post(api_client, ["name", "last_name"], [["Bob", "Example"]])
    assert ambiguous.status_code == 422
    assert ambiguous.json()["errors"][0]["code"] == "ambiguous_mapping"


def test_database_constraint_failure_rolls_back_all_rows(
    api_client, session_factory, monkeypatch
):
    original = importer.insert_job
    calls = []

    def broken(session, person_id, org_id, data):
        calls.append(1)
        if len(calls) == 2:
            session.execute(insert(Job).values(person_id=person_id, org_id=999999))
        return original(session, person_id, org_id, data)

    monkeypatch.setattr(importer, "insert_job", broken)
    response = post(
        api_client,
        ["first_name", "last_name", "organization", "job_type"],
        [
            ["Alice", "Example", "Institute", "Permanent"],
            ["Bob", "Other", "Institute", "Permanent"],
        ],
    )
    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "database_constraint"
    assert counts(session_factory) == [0, 0, 0, 0, 0]


def test_commit_failure_rolls_back_data_and_history(api_client, session_factory):
    assert (
        post(
            api_client,
            ["first_name", "last_name", "email"],
            [["Alice", "Example", "old@example.org"]],
        ).status_code
        == 200
    )
    before = counts(session_factory)

    def fail_commit(session):
        if session.info.get("fail_commit"):
            raise RuntimeError("private commit failure")

    def broken_sessions():
        with session_factory() as session:
            session.info["fail_commit"] = True
            yield session

    api_client.app.dependency_overrides[get_session] = broken_sessions
    event.listen(Session, "before_commit", fail_commit)
    try:
        response = post(
            api_client,
            ["first_name", "last_name", "email"],
            [["Alice", "Example", "new@example.org"]],
        )
        assert response.status_code == 500
        assert "private commit failure" not in response.text
    finally:
        event.remove(Session, "before_commit", fail_commit)
    assert counts(session_factory) == before
    with session_factory() as session:
        assert session.scalar(select(Person.email)) == "old@example.org"


@pytest.mark.parametrize(
    "lock_seconds,import_seconds,code,status",
    [(0.05, 5, "import_busy", 409), (5, 0.1, "processing_timeout", 503)],
)
def test_real_sql_lock_and_statement_timeouts_leave_no_partial_state(
    api_client, session_factory, monkeypatch, lock_seconds, import_seconds, code, status
):
    monkeypatch.setattr(get_settings(), "database_lock_timeout_seconds", lock_seconds)
    monkeypatch.setattr(get_settings(), "import_timeout_seconds", import_seconds)
    with session_factory() as blocking, blocking.begin():
        blocking.execute(text("SELECT pg_advisory_xact_lock(728194021)"))
        response = post(api_client, ["first_name", "last_name"], [["Alice", "Example"]])
        assert response.status_code == status
        assert response.json()["errors"][0]["code"] == code
        assert counts(session_factory) == [0, 0, 0, 0, 0]


def test_two_api_instances_reconcile_without_duplicates(session_factory, monkeypatch):
    entered, release = Event(), Event()
    original = importer.reconcile_person
    calls = []

    def delayed(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(importer, "reconcile_person", delayed)
    headers, rows = ["first_name", "last_name", "organization", "job_type"], [
        ["Alice", "Example", "Institute", "Permanent"]
    ]
    with (
        TestClient(make_app(session_factory), raise_server_exceptions=False) as first,
        TestClient(make_app(session_factory), raise_server_exceptions=False) as second,
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        one = pool.submit(post, first, headers, rows)
        assert entered.wait(5)
        two = pool.submit(post, second, headers, rows)
        release.set()
        results = [one.result(timeout=10), two.result(timeout=10)]
    assert [response.status_code for response in results] == [200, 200]
    assert sum(response.json()["persons_created"] for response in results) == 1
    assert sum(response.json()["jobs_created"] for response in results) == 1
    assert counts(session_factory) == [1, 1, 1, 0, 2]


def test_busy_upload_is_rejected_before_multipart_while_health_responds(
    session_factory, monkeypatch
):
    entered, release = Event(), Event()
    original = importer.reconcile_person

    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(importer, "reconcile_person", delayed)
    app = make_app(session_factory, Settings(max_active_uploads=1))
    with (
        TestClient(app, raise_server_exceptions=False) as client,
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        pending = pool.submit(
            post, client, ["first_name", "last_name"], [["Alice", "Example"]]
        )
        assert entered.wait(5)
        try:
            response = client.post(
                "/upload",
                content=b"invalid multipart",
                headers={"content-type": "multipart/form-data"},
            )
            assert (
                response.status_code == 429 and response.headers["Retry-After"] == "2"
            )
            assert client.get("/health").status_code == 200
        finally:
            release.set()
        assert pending.result(timeout=10).status_code == 200


def test_postgresql_unique_identity_nulls_and_foreign_keys(session_factory):
    with session_factory() as session, session.begin():
        session.add(Person(first_name="Alice", last_name="Example"))
    with pytest.raises(IntegrityError):
        with session_factory() as session, session.begin():
            session.add(
                Person(first_name="Alice", last_name="Example", middle_name=None)
            )
    with pytest.raises(IntegrityError):
        with session_factory() as session, session.begin():
            session.add(Job(person_id=999999, org_id=999999))


def test_field_history_policy_controls_which_values_are_retained(
    api_client, session_factory, monkeypatch
):
    from app.schema import PersonData

    field = PersonData.model_fields["email"]
    monkeypatch.setattr(
        field, "json_schema_extra", field.json_schema_extra | {"history": False}
    )
    headers = ["first_name", "last_name", "email", "phone"]
    assert (
        post(
            api_client, headers, [["Alice", "Example", "old@example.org", "00123"]]
        ).status_code
        == 200
    )
    response = post(
        api_client, headers, [["Alice", "Example", "new@example.org", "00456"]]
    )
    assert response.status_code == 200 and response.json()["persons_updated"] == 1
    with session_factory() as session:
        history = session.scalar(select(PersonHistory))
        assert history.changed_fields == ["phone"]
        assert "email" not in history.old_data and "email" not in history.new_data
        assert (history.old_data["phone"], history.new_data["phone"]) == (
            "00123",
            "00456",
        )
        assert session.scalar(select(Person.email)) == "new@example.org"


def test_large_identity_set_and_reimport_after_cache_eviction(
    api_client, session_factory
):
    rows = [[f"Person{i:04d}", "Synthetic"] for i in range(1001)]
    rows.append(rows[0])
    response = post(api_client, ["first_name", "last_name"], rows)
    assert response.status_code == 200
    assert response.json()["rows_processed"] == 1002
    assert response.json()["persons_created"] == 1001
    repeated = post(api_client, ["first_name", "last_name"], rows)
    assert repeated.status_code == 200
    assert (
        repeated.json()["persons_created"]
        == repeated.json()["persons_updated"]
        == repeated.json()["jobs_created"]
        == 0
    )
    assert repeated.json()["persons_unchanged"] == 1001
    assert counts(session_factory) == [1001, 0, 0, 0, 2]


def test_error_details_are_bounded_and_the_file_stays_atomic(session_factory):
    app = make_app(session_factory, Settings(max_errors=2))
    with TestClient(app, raise_server_exceptions=False) as client:
        response = post(
            client,
            ["first_name", "last_name", "email"],
            [
                ["Alice", "Example", "a@example.org"],
                ["Bad1", "Example", "invalid"],
                ["Bad2", "Example", "invalid"],
                ["Bad3", "Example", "invalid"],
            ],
        )
    assert response.status_code == 422
    assert len(response.json()["errors"]) == 2
    assert response.json()["errors_truncated"] is True
    assert counts(session_factory) == [0, 0, 0, 0, 0]

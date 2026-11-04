import csv
from datetime import date
from pathlib import Path

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from app.models import Person, Organization, Job, PersonHistory, ImportBatch

pytestmark = [pytest.mark.integration, pytest.mark.reference]


def reference_rows(path):
    if path.suffix == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream, delimiter=";"))
    else:
        workbook = load_workbook(path, read_only=True, data_only=False)
        try:
            iterator = workbook.active.iter_rows(values_only=True)
            headers = next(iterator)
            rows = [dict(zip(headers, row)) for row in iterator]
        finally:
            workbook.close()

    def clean(value):
        return " ".join(value.split()) or None if isinstance(value, str) else value

    for row in rows:
        if path.suffix == ".csv":
            first, _, middle = row["dataFirstNames"].partition(",")
            person = dict(
                zip(
                    (
                        "first_name",
                        "middle_name",
                        "last_name",
                        "email",
                        "phone",
                        "fax",
                        "title",
                    ),
                    (
                        first,
                        middle,
                        row["dataLastName"],
                        row.get("dataEmail"),
                        row.get("dataPhone"),
                        row.get("dataFax"),
                        row.get("dataTitle"),
                    ),
                )
            )
            job = (
                row["dataOrganization"],
                row["dataJobTitle"],
                row["dataPositionType"],
                row["dataJobStartDate"],
                row["dataURI"],
            )
        else:
            person = {
                target: row.get(source)
                for target, source in {
                    "first_name": "raw_first",
                    "middle_name": "raw_middle",
                    "last_name": "raw_last",
                    "email": "raw_email",
                    "phone": "raw_phone",
                    "fax": "raw_fax",
                    "title": "raw_title",
                }.items()
            }
            job = (
                row["raw_org_name"],
                row["raw_job_title"],
                row["raw_position_type"],
                row["raw_date"],
                row["raw_uri"],
            )
        person = {key: clean(value) for key, value in person.items()}
        yield person, (
            clean(job[0]),
            clean(job[1]),
            clean(job[2]),
            date(int(job[3]), 1, 1),
            clean(job[4]),
        )


def test_reference_sequence_values_jobs_history_and_immediate_reimports(
    api_client, session_factory
):
    root = Path(__file__).resolve().parents[2] / "data"
    paths = [
        root / name
        for name in ("data_1.csv", "data_2.xlsx", "data_3.xlsx", "data_4.xlsx")
    ]
    if not all(path.exists() for path in paths):
        pytest.skip("Local reference files are unavailable.")
    expected_people, expected_jobs, expected_history = {}, set(), []
    expected_organizations = set()
    for path, expected_created, expected_updated in zip(
        paths, (16, 24, 0, 0), (0, 0, 8, 4)
    ):
        previous_jobs = len(expected_jobs)
        for person, job in reference_rows(path):
            expected_organizations.add((job[0], job[4]))
            identity = tuple(
                person[key] for key in ("first_name", "middle_name", "last_name")
            )
            old = expected_people.get(identity)
            if old is None:
                expected_people[identity] = person
            else:
                changes = {
                    key: value
                    for key, value in person.items()
                    if key in ("email", "phone", "fax", "title")
                    and value is not None
                    and value != old[key]
                }
                if changes:
                    new = old | changes
                    expected_history.append(
                        (identity, path.name, old, new, sorted(changes))
                    )
                    expected_people[identity] = new
            expected_jobs.add((identity, *job))
        response = api_client.post(
            "/upload", files={"file": (path.name, path.read_bytes())}
        )
        assert response.status_code == 200
        body = response.json()
        assert not any(issue["code"] == "ignored_column" for issue in body["warnings"])
        assert body.get("persons_created", body.get("inserted")) == expected_created
        assert body.get("persons_updated", body.get("updated")) == expected_updated
        if "jobs_created" in body:
            assert body["jobs_created"] == len(expected_jobs) - previous_jobs
        repeated = api_client.post(
            "/upload", files={"file": (path.name, path.read_bytes())}
        )
        assert repeated.status_code == 200
        assert (
            repeated.json().get("persons_created", repeated.json().get("inserted")) == 0
        )
        assert (
            repeated.json().get("persons_updated", repeated.json().get("updated")) == 0
        )
        if "jobs_created" in repeated.json():
            assert repeated.json()["jobs_created"] == 0
        with session_factory() as session:
            people = list(session.scalars(select(Person)))
            identities = {
                p.person_id: (p.first_name, p.middle_name, p.last_name) for p in people
            }
            actual = {
                identities[p.person_id]: {
                    field: getattr(p, field)
                    for field in (
                        "first_name",
                        "middle_name",
                        "last_name",
                        "email",
                        "phone",
                        "fax",
                        "title",
                    )
                }
                for p in people
            }
            if actual != expected_people:
                pytest.fail(
                    "Stored reference person values differ from the expected source reconciliation.",
                    pytrace=False,
                )
            organizations = {
                o.org_id: (o.org_name, o.org_vivo_uri)
                for o in session.scalars(select(Organization))
            }
            if set(organizations.values()) != expected_organizations:
                pytest.fail(
                    "Stored reference organization names or VIVO URIs differ.",
                    pytrace=False,
                )
            assert len(organizations) == len(expected_organizations)
            actual_jobs = {
                (
                    identities[j.person_id],
                    organizations[j.org_id][0],
                    j.job_title,
                    j.job_type,
                    j.start_date,
                    organizations[j.org_id][1],
                )
                for j in session.scalars(select(Job))
            }
            if actual_jobs != expected_jobs:
                pytest.fail("Stored reference jobs or relations differ.", pytrace=False)
            batches = {
                b.import_id: b.filename for b in session.scalars(select(ImportBatch))
            }
            history = list(
                session.scalars(
                    select(PersonHistory).order_by(PersonHistory.history_id)
                )
            )
            actual_history = [
                (
                    identities[h.person_id],
                    batches[h.import_id],
                    h.old_data,
                    h.new_data,
                    h.changed_fields,
                )
                for h in history
            ]
            if actual_history != expected_history:
                pytest.fail(
                    "Reference history snapshots or source references differ.",
                    pytrace=False,
                )
            assert all(h.changed_at.utcoffset().total_seconds() == 0 for h in history)
    assert (len(expected_people), len(expected_jobs), len(expected_history)) == (
        40,
        55,
        12,
    )

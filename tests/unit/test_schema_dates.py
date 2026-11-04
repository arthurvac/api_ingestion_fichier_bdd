from datetime import date, datetime

import pytest
from pydantic import ValidationError

from app.dates import DateDetector
from app.errors import ImportFailure
from app.mapping import map_values, resolve_columns
from app.models import Person, Organization, Job
from app.schema import (
    IDENTITY_FIELDS,
    MODELS,
    JobData,
    Options,
    PersonData,
    normalize_header,
)


def test_identity_normalization_preserves_case_and_accents():
    person = PersonData(
        first_name="  Jean   Luc  ", middle_name="  ", last_name="ÉXEMPLE"
    )
    assert (person.first_name, person.middle_name, person.last_name) == (
        "Jean Luc",
        None,
        "ÉXEMPLE",
    )
    assert normalize_header(" FIRST_name ") == normalize_header("First Name")
    assert normalize_header("名") != normalize_header("姓")
    assert IDENTITY_FIELDS == ("first_name", "middle_name", "last_name")


@pytest.mark.parametrize(
    "values",
    [
        {"first_name": ""},
        {"last_name": None},
        {"email": "invalid-address"},
        {"phone": 123},
        {"fax": True},
    ],
)
def test_invalid_identity_and_contacts_are_not_coerced(values):
    with pytest.raises(ValidationError):
        PersonData.model_validate(
            {"first_name": "Alice", "last_name": "Example"} | values
        )


@pytest.mark.parametrize("uri_column", ["dataURI", "raw_uri", "org_vivo_uri"])
def test_source_combined_names_and_uri_policy_are_central(uri_column):
    columns, warnings = resolve_columns(
        ["dataFirstNames", "dataLastName", uri_column], Options()
    )
    uri = "http://vivoweb.org/ontology/core#Department"
    values = map_values([" Anne Marie, Louise ", " Example ", uri], columns)
    assert values["person"] == {
        "first_name": "Anne Marie",
        "middle_name": "Louise",
        "last_name": "Example",
    }
    assert values["organization"] == {"org_vivo_uri": uri}
    assert warnings == []


def test_vivo_uri_can_still_be_explicitly_ignored():
    columns, warnings = resolve_columns(
        ["first_name", "last_name", "dataURI"],
        Options(ignored_columns=["dataURI"]),
    )
    values = map_values(["Alice", "Example", "http://example.org/type"], columns)
    assert "organization" not in values
    assert warnings[0].code == "ignored_column"
    assert warnings[0].column == "dataURI"


def test_target_fields_match_active_sql_models():
    for section, model in MODELS.items():
        sql_model = {"person": Person, "organization": Organization, "job": Job}[
            section
        ]
        assert all(name in sql_model.__table__.columns for name in model.model_fields)
    for name in ("email", "phone", "fax", "title"):
        policy = PersonData.model_fields[name].json_schema_extra
        assert policy["update"] and policy["history"]


@pytest.mark.parametrize(
    "options",
    [
        {"mapping": {"person.unknown": "source"}},
        {"mapping": {"person.first_name": {"source": "x"}}},
        {"year_policy": "dmy"},
        {"analysis_id": "obsolete"},
        {"sheets": ["Same", "Same"]},
    ],
)
def test_unknown_and_unsafe_options_are_rejected(options):
    with pytest.raises(ValidationError):
        Options.model_validate(options)


@pytest.mark.parametrize(
    "values,code",
    [
        (["03/04/2025", "05/06/2025"], "ambiguous_date_format"),
        (["04/04/2025"], "ambiguous_date_format"),
        (["13/02/2025", "02/14/2025"], "mixed_date_format"),
        (["13/02/2025", "14-03-2025"], "mixed_date_format"),
        (["03/04/25"], "two_digit_year_forbidden"),
        (["2025-04-03", "13/02/2025"], "mixed_date_format"),
        (["31/02/2025", "03/04/2025"], "ambiguous_date_format"),
        (["2025"], "incomplete_date"),
    ],
)
def test_dates_do_not_guess_ambiguous_or_mixed_conventions(values, code):
    detector = DateDetector()
    with pytest.raises(ImportFailure) as error:
        for index, value in enumerate(values, 2):
            detector.observe(value, {"row": index, "column": "start_date"})
        detector.resolve()
    assert error.value.errors[0].code == code
    assert error.value.errors[0].row >= 2


@pytest.mark.parametrize(
    "values,expected",
    [
        (["03/04/2024", "29/02/2024"], [date(2024, 4, 3), date(2024, 2, 29)]),
        (["03/04/2024", "02/29/2024"], [date(2024, 3, 4), date(2024, 2, 29)]),
        (["2024-04-03", "2024-02-29"], [date(2024, 4, 3), date(2024, 2, 29)]),
    ],
)
def test_late_date_evidence_applies_to_earlier_rows(values, expected):
    detector = DateDetector()
    for value in values:
        detector.observe(value, {"row": 2, "column": "start_date"})
    detector.resolve()
    assert [detector.parse(value, {}) for value in values] == expected


def test_typed_dates_year_precision_and_invalid_calendar():
    detector = DateDetector(allow_year=True)
    for value in [2020, datetime(2024, 2, 29), "13/02/2025"]:
        detector.observe(value, {})
    detector.resolve()
    assert detector.parse(2020, {}) == date(2020, 1, 1)
    assert detector.parse(datetime(2024, 2, 29), {}) == date(2024, 2, 29)
    assert detector.annual_seen
    for value in ["31/02/2025", 45200, 2020.5, True, float("nan")]:
        with pytest.raises(ImportFailure):
            detector.parse(value, {})
    assert (
        JobData(start_date=date(2024, 2, 29)).model_dump(mode="json")["start_date"]
        == "29/02/2024"
    )


def test_explicit_first_name_mapping_keeps_the_declared_middle_name_split():
    columns, _ = resolve_columns(
        ["dataFirstNames", "dataLastName"],
        Options(mapping={"person.first_name": "dataFirstNames"}),
    )
    values = map_values(["Anne, Louise", "Example"], columns)
    assert values["person"]["first_name"] == "Anne"
    assert values["person"]["middle_name"] == "Louise"

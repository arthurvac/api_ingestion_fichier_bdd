import csv
import io
import zipfile

import pytest
from openpyxl import Workbook

from app.config import Settings
from app.errors import ImportFailure
from app.mapping import map_values, resolve_columns
from app.readers import read_tables
from app.schema import Options


def csv_bytes(headers, rows, delimiter=";", encoding="utf-8"):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, delimiter=delimiter)
    writer.writerow(headers)
    writer.writerows(rows)
    return stream.getvalue().encode(encoding)


def xlsx_bytes(headers, rows, second=False):
    workbook = Workbook()
    first = workbook.active
    first.title = "Contacts"
    first.append(headers)
    for row in rows:
        first.append(row)
    if second:
        worksheet = workbook.create_sheet("Other")
        worksheet.append(headers)
        worksheet.append(["Bob", "Example"] + [None] * (len(headers) - 2))
    stream = io.BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


def read(content, filename="source.csv", options=None, settings=None):
    settings = settings or Settings()
    with read_tables(
        io.BytesIO(content), filename, Options.model_validate(options or {}), settings
    ) as (kind, tables, warnings, physical):
        result = []
        for table in tables:
            columns, notices = resolve_columns(
                table.headers, Options.model_validate(options or {}), table.sheet
            )
            warnings.extend(notices)
            result.extend(
                (row.number, row.sheet, map_values(row.values, columns))
                for row in table.rows(settings, kind)
            )
        return kind, result, warnings, physical


@pytest.mark.parametrize(
    "encoding", ["utf-8", "utf-8-sig", "utf-16", "utf-32", "cp1252", "iso8859-1"]
)
@pytest.mark.parametrize("delimiter", [",", ";", "\t", "|"])
def test_csv_encoding_and_delimiter_are_automatic(encoding, delimiter):
    kind, rows, _, physical = read(
        csv_bytes(["Prénom", "Nom"], [["Élodie", "Exemple"]], delimiter, encoding)
    )
    assert kind == "csv" and physical["delimiter"] == delimiter
    assert rows[0][2]["person"] == {"first_name": "Élodie", "last_name": "Exemple"}


def test_quote_multiline_and_physical_error_location():
    content = csv_bytes(
        ["first_name", "last_name"], [["Alice,\nMarie", "Example"], ["Bob", "Other"]]
    )
    _, rows, _, _ = read(content)
    assert [row[0] for row in rows] == [3, 4]
    assert rows[0][2]["person"]["first_name"] == "Alice, Marie"


def test_ambiguous_delimiter_is_not_guessed():
    with pytest.raises(ImportFailure) as error:
        read(b"first_name;last_name,email\nAlice;Example,a@example.org\n")
    assert error.value.errors[0].code == "delimiter_ambiguous"


def test_encoding_validation_reaches_last_byte():
    content = b"first_name;last_name\n" + b"Alice;Example\n" * 6000 + b"Bad;\xff\n"
    with pytest.raises(ImportFailure) as error:
        read(content, options={"encoding": "utf-8"})
    assert error.value.errors[0].code == "invalid_csv_encoding"


def test_mixed_encoding_is_not_silently_reinterpreted():
    content = (
        "first_name;last_name\nÉlodie;Example\n".encode()
        + b"Alice;Example\n" * 6000
        + b"Bad;\xe9\n"
    )
    with pytest.raises(ImportFailure) as error:
        read(content)
    assert error.value.errors[0].code == "encoding_requires_input"


def test_shifted_headers_extra_columns_and_aliases():
    content = b"Export\nLAST NAME; First Name ;note\nExample;  Alice  ;unused\n"
    _, rows, warnings, _ = read(content)
    assert rows[0][2]["person"] == {"last_name": "Example", "first_name": "Alice"}
    assert warnings[0].column == "note"
    assert warnings[0].code == "ignored_column"


@pytest.mark.parametrize("kind", ["csv", "xlsx"])
def test_unknown_unicode_headers_can_be_mapped(kind):
    headers, values = ["名", "姓"], [["Élodie", "李"]]
    content = (
        csv_bytes(headers, values) if kind == "csv" else xlsx_bytes(headers, values)
    )
    _, rows, _, _ = read(
        content,
        "source." + kind,
        {"mapping": {"person.first_name": "名", "person.last_name": "姓"}},
    )
    assert rows[0][2]["person"] == {"first_name": "Élodie", "last_name": "李"}


@pytest.mark.parametrize("kind", ["csv", "xlsx"])
def test_headerless_preserves_first_row(kind):
    content = (
        csv_bytes(["Alice", "Example"], [["Bob", "Other"]])
        if kind == "csv"
        else xlsx_bytes(["Alice", "Example"], [["Bob", "Other"]])
    )
    _, rows, _, _ = read(
        content,
        "source." + kind,
        {
            "header_row": 0,
            "mapping": {"person.first_name": "@c1", "person.last_name": "@c2"},
        },
    )
    assert len(rows) == 2 and rows[0][0] == 1
    assert rows[0][2]["person"]["first_name"] == "Alice"


@pytest.mark.parametrize(
    "headers,code",
    [
        (["first_name", "First Name", "last_name"], "duplicate_headers"),
        (["first_name", "name"], "ambiguous_mapping"),
        (["first_name", "email"], "missing_required_column"),
        (["first_name", "last_name", "prenom"], "ambiguous_mapping"),
    ],
)
def test_column_ambiguity_and_missing_targets_are_explicit(headers, code):
    with pytest.raises(ImportFailure) as error:
        read(csv_bytes(headers, [["Alice"] * len(headers)]))
    assert error.value.errors[0].code == code


def test_explicit_mapping_resolves_alias_collision():
    _, rows, warnings, _ = read(
        csv_bytes(
            ["first_name", "prenom", "last_name"], [["Alice", "Other", "Example"]]
        ),
        options={"mapping": {"person.first_name": "prenom"}},
    )
    assert rows[0][2]["person"]["first_name"] == "Other"
    assert warnings[0].column == "first_name"


def test_multiple_sheets_require_selection():
    content = xlsx_bytes(
        ["first_name", "last_name"], [["Alice", "Example"]], second=True
    )
    with pytest.raises(ImportFailure) as error:
        read(content, "data.xlsx")
    assert error.value.errors[0].code == "table_selection_required"
    _, rows, _, _ = read(content, "data.xlsx", {"sheets": ["Contacts", "Other"]})
    assert [row[1] for row in rows] == ["Contacts", "Other"]


def test_xml_encoding_and_unicode_values():
    content = xlsx_bytes(["Prénom", "Nom"], [["Élodie", "李"]])
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(content)) as source,
        zipfile.ZipFile(output, "w") as target,
    ):
        for entry in source.infolist():
            value = source.read(entry.filename)
            if entry.filename.endswith(".xml"):
                value = (
                    '<?xml version="1.0" encoding="UTF-16"?>' + value.decode("utf-8")
                ).encode("utf-16")
            target.writestr(entry, value)
    kind, rows, _, physical = read(output.getvalue(), "data.xlsx")
    assert kind == "xlsx" and physical["encoding"] == "XML declarations"
    assert rows[0][2]["person"] == {"first_name": "Élodie", "last_name": "李"}


@pytest.mark.parametrize(
    "hazard,code",
    [
        ("formula", "formula_forbidden"),
        ("macro", "macros_forbidden"),
        ("xml", "invalid_xlsx"),
        ("archive", "archive_too_large"),
        ("options", "option_not_applicable"),
    ],
)
def test_xlsx_corruption_and_unsafe_content(hazard, code):
    content = xlsx_bytes(
        ["first_name", "last_name"],
        [["=1+1" if hazard == "formula" else "Alice", "Example"]],
    )
    if hazard in {"macro", "xml"}:
        output = io.BytesIO()
        with (
            zipfile.ZipFile(io.BytesIO(content)) as source,
            zipfile.ZipFile(output, "w") as target,
        ):
            for entry in source.infolist():
                value = source.read(entry.filename)
                target.writestr(
                    entry,
                    (
                        b"<invalid"
                        if hazard == "xml"
                        and entry.filename == "xl/worksheets/sheet1.xml"
                        else value
                    ),
                )
            if hazard == "macro":
                target.writestr("xl/vbaProject.bin", b"no code")
        content = output.getvalue()
    with pytest.raises(ImportFailure) as error:
        read(
            content,
            "data.xlsx",
            options={"encoding": "utf-8"} if hazard == "options" else None,
            settings=(
                Settings(max_xlsx_uncompressed_bytes=100)
                if hazard == "archive"
                else None
            ),
        )
    assert error.value.errors[0].code == code


@pytest.mark.parametrize(
    "content,code",
    [
        (b"", "empty_file"),
        (b"PKbroken", "invalid_xlsx"),
        (b"MZbinary", "unsupported_content"),
    ],
)
def test_empty_corrupt_and_binary_files(content, code):
    with pytest.raises(ImportFailure) as error:
        read(content)
    assert error.value.errors[0].code == code


def test_extension_does_not_define_the_parser():
    kind, rows, warnings, _ = read(
        csv_bytes(["first_name", "last_name"], [["Alice", "Example"]]), "wrong.xlsx"
    )
    assert kind == "csv" and len(rows) == 1
    assert warnings[0].code == "extension_mismatch"


def test_bad_row_width_and_record_limit():
    with pytest.raises(ImportFailure) as error:
        read(b"first_name;last_name\nAlice;Example;extra\n")
    assert error.value.errors[0].code == "column_count"
    assert error.value.errors[0].row == 2
    with pytest.raises(ImportFailure) as error:
        read(
            csv_bytes(["first_name", "last_name"], [["x" * 1100, "Example"]]),
            settings=Settings(max_record_bytes=1024),
        )
    assert error.value.status == 413

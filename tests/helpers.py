import csv
import io

from openpyxl import Workbook

CSV_HEADERS = [
    "dataFirstNames",
    "dataLastName",
    "dataEmail",
    "dataPhone",
    "dataFax",
    "dataTitle",
    "dataJobTitle",
    "dataPositionType",
    "dataOrganization",
    "dataJobStartDate",
    "dataURI",
]


def csv_content(
    rows: list[dict] | None = None, headers: list[str] | None = None
) -> bytes:
    headers = headers or CSV_HEADERS
    rows = rows if rows is not None else [{}]
    defaults = dict(
        dataFirstNames="Alice",
        dataLastName="Exemple",
        dataEmail="alice@example.org",
        dataPhone="01 02 03 04 05",
        dataFax="",
        dataTitle="Dr",
        dataJobTitle="Chercheuse",
        dataPositionType="CDI",
        dataOrganization="Institut Exemple",
        dataJobStartDate="2020",
        dataURI="",
    )
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream, fieldnames=headers, delimiter=";", extrasaction="ignore"
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(defaults | row)
    return stream.getvalue().encode("utf-8-sig")


def xlsx_content(sheet: str = "data_2", rows: list[list] | None = None) -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = sheet
    worksheet.append(
        [
            "raw_first",
            "raw_last",
            "raw_middle",
            "raw_email",
            "raw_phone",
            "raw_fax",
            "raw_title",
            "raw_job_title",
            "raw_position_type",
            "raw_org_name",
            "raw_date",
            "raw_uri",
        ]
    )
    for row in rows or [
        [
            "Alice",
            "Exemple",
            None,
            "alice@example.org",
            None,
            None,
            None,
            "Chercheuse",
            "CDI",
            "Institut Exemple",
            2020,
            None,
        ]
    ]:
        worksheet.append(row)
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()

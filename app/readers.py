import codecs
import csv
import io
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from charset_normalizer import from_bytes
from openpyxl import load_workbook

from app.config import Settings
from app.errors import ImportFailure, Issue, fail
from app.mapping import aliases
from app.schema import Options, normalize_header


@dataclass
class Row:
    number: int
    values: list
    sheet: str | None = None


@dataclass
class Table:
    headers: list[str]
    header_row: int
    sheet: str | None
    read: Callable[[], Iterator[Row]]

    def rows(self, settings: Settings, kind: str):
        iterator = self.read()
        try:
            for row in iterator:
                if row.number <= self.header_row or all(
                    value is None or not str(value).strip() for value in row.values
                ):
                    continue
                values = row.values
                if kind == "xlsx":
                    while len(values) > len(self.headers) and values[-1] is None:
                        values.pop()
                    values.extend([None] * max(0, len(self.headers) - len(values)))
                if len(values) != len(self.headers):
                    fail(
                        "column_count",
                        "The row has a different number of columns than the header.",
                        row=row.number,
                        sheet=self.sheet,
                    )
                if (
                    sum(
                        len(str(value).encode("utf-8"))
                        for value in values
                        if value is not None
                    )
                    > settings.max_record_bytes
                ):
                    fail(
                        "record_too_large",
                        "A source row exceeds the allowed size.",
                        413,
                        row=row.number,
                        sheet=self.sheet,
                    )
                yield row
        finally:
            iterator.close()


def _decode_check(file, encoding):
    file.seek(0)
    decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
    while block := file.read(1024 * 1024):
        text = decoder.decode(block)
        if not isinstance(text, str) or any(
            (ord(c) < 32 and c not in "\t\r\n") or 127 <= ord(c) < 160 for c in text
        ):
            raise UnicodeError("Non-text control characters.")
    decoder.decode(b"", final=True)


def detect_encoding(file, explicit=None):
    file.seek(0)
    sample = file.read(65536)
    if explicit:
        try:
            _decode_check(file, explicit)
        except (UnicodeError, LookupError, TypeError, ValueError):
            fail(
                "invalid_csv_encoding",
                "The entire CSV must decode strictly using the requested encoding.",
            )
        return explicit
    for bom, encoding in (
        (codecs.BOM_UTF32_LE, "utf-32"),
        (codecs.BOM_UTF32_BE, "utf-32"),
        (codecs.BOM_UTF8, "utf-8-sig"),
        (codecs.BOM_UTF16_LE, "utf-16"),
        (codecs.BOM_UTF16_BE, "utf-16"),
    ):
        if sample.startswith(bom):
            try:
                _decode_check(file, encoding)
            except UnicodeError:
                fail(
                    "invalid_csv_encoding",
                    "The CSV bytes contradict their encoding marker.",
                )
            return encoding
    try:
        _decode_check(file, "utf-8-sig")
        return "utf-8-sig"
    except UnicodeError:
        pass
    try:
        prefix = codecs.getincrementaldecoder("utf-8")().decode(sample)
    except UnicodeError:
        prefix = ""
    if any(ord(c) > 127 for c in prefix):
        fail(
            "encoding_requires_input",
            "A UTF-8 prefix is followed by inconsistent bytes. Check the source encoding.",
        )
    candidates = []
    proposed = [
        match.encoding
        for match in from_bytes(
            sample, cp_isolation=["cp1252", "latin_1"], enable_fallback=False
        )
    ]
    for encoding in dict.fromkeys([*proposed, "cp1252", "iso8859-1"]):
        try:
            _decode_check(file, encoding)
            candidates.append(encoding)
        except UnicodeError:
            continue
    if candidates:
        # Dans ce jeu contrôlé, les divergences restantes impliquent des contrôles C1 refusés.
        return candidates[0]
    fail(
        "encoding_requires_input",
        "The encoding cannot be detected reliably; provide options.encoding.",
    )


@contextmanager
def csv_reader(file, encoding, delimiter):
    file.seek(0)
    wrapper = io.TextIOWrapper(file, encoding=encoding, newline="")
    reader = csv.reader(wrapper, delimiter=delimiter, strict=True)
    try:
        yield reader
    except (csv.Error, UnicodeError):
        fail(
            "invalid_csv",
            "Malformed CSV record or invalid text encoding.",
            row=reader.line_num,
        )
    finally:
        wrapper.detach()


def detect_delimiter(file, encoding, explicit=None):
    if explicit:
        return explicit
    file.seek(0)
    wrapper = io.TextIOWrapper(file, encoding=encoding, newline="")
    try:
        sample = wrapper.read(65536)
    finally:
        wrapper.detach()
    try:
        proposed = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        proposed = None
    candidates = []
    for delimiter in dict.fromkeys([proposed, ",", ";", "\t", "|"]):
        if delimiter is None:
            continue
        widths = []
        try:
            for _, values in zip(
                range(100),
                csv.reader(
                    io.StringIO(sample, newline=""), delimiter=delimiter, strict=True
                ),
            ):
                if values and any(value.strip() for value in values):
                    widths.append(len(values))
        except csv.Error:
            pass
        if widths:
            width = max(set(widths), key=widths.count)
            if width > 1:
                candidates.append((delimiter, widths.count(width) / len(widths)))
    if not candidates and not any(c in sample for c in ",;\t|"):
        return ","
    if candidates:
        best = max(score for _, score in candidates)
        winners = [delimiter for delimiter, score in candidates if score == best]
        if len(winners) == 1:
            return winners[0]
    fail(
        "delimiter_ambiguous",
        "No unique CSV delimiter was detected; provide options.delimiter.",
    )


def _csv_rows(file, encoding, delimiter):
    with csv_reader(file, encoding, delimiter) as reader:
        for values in reader:
            yield Row(reader.line_num, values)


def _xlsx_rows(sheet):
    sheet.reset_dimensions()
    iterator = sheet.iter_rows()
    try:
        for number, cells in enumerate(iterator, 1):
            for cell in cells:
                if cell.data_type in {"f", "e"}:
                    fail(
                        (
                            "formula_forbidden"
                            if cell.data_type == "f"
                            else "invalid_cell"
                        ),
                        "Formulas and spreadsheet error cells are not accepted.",
                        row=number,
                        column=cell.column_letter,
                        sheet=sheet.title,
                    )
            yield Row(number, [cell.value for cell in cells], sheet.title)
    except ImportFailure:
        raise
    except Exception:
        fail(
            "invalid_xlsx",
            "A worksheet contains malformed XML or invalid cell data.",
            sheet=sheet.title,
        )
    finally:
        iterator.close()


def _header(read, options, settings, sheet=None):
    known = set(aliases()) | {
        normalize_header(source) for source in options.mapping.values()
    }
    candidates = []
    iterator = read()
    try:
        for row in iterator:
            values = list(row.values)
            while values and values[-1] is None:
                values.pop()
            if len(values) > settings.max_columns:
                fail(
                    "too_many_columns",
                    "The maximum column count was exceeded.",
                    413,
                    sheet=sheet,
                    row=row.number,
                )
            if options.header_row == 0 and any(value is not None for value in values):
                return Table([f"@c{i+1}" for i in range(len(values))], 0, sheet, read)
            if values and all(
                isinstance(v, str) and v.strip() and len(v) <= 256 for v in values
            ):
                if options.header_row is None or row.number == options.header_row:
                    score = sum(normalize_header(value) in known for value in values)
                    candidates.append(
                        (
                            score,
                            len(values),
                            row.number,
                            [v.strip().lstrip("\ufeff") for v in values],
                        )
                    )
            if row.number >= (options.header_row or settings.max_header_rows):
                break
    finally:
        iterator.close()
    if not candidates:
        fail(
            "header_missing",
            "No usable header was found; provide options.header_row.",
            sheet=sheet,
        )
    candidates.sort(key=lambda item: (-item[0], -item[1], item[2]))
    score, _, number, headers = candidates[0]
    if not score and options.header_row is None:
        fail(
            "header_ambiguous",
            "The header cannot be identified; specify its row or provide a column mapping.",
            sheet=sheet,
        )
    return Table(headers, number, sheet, read)


@contextmanager
def read_tables(file, filename: str, options: Options, settings: Settings):
    file.seek(0)
    prefix = file.read(8)
    if not prefix:
        fail("empty_file", "The file is empty.")
    if prefix.startswith((b"MZ", b"%PDF", b"\x7fELF", b"\xd0\xcf")):
        fail("unsupported_content", "Only CSV and XLSX files are supported.", 415)
    kind = "xlsx" if prefix.startswith(b"PK") else "csv"
    warnings = []
    if Path(filename).suffix.lower() != f".{kind}":
        warnings.append(
            Issue(
                code="extension_mismatch",
                message="The file extension differs from its detected content.",
            )
        )
    if kind == "csv":
        if options.sheets:
            fail(
                "option_not_applicable", "Sheet selection does not apply to CSV files."
            )
        encoding = detect_encoding(file, options.encoding)
        delimiter = detect_delimiter(file, encoding, options.delimiter)
        table = _header(lambda: _csv_rows(file, encoding, delimiter), options, settings)
        yield kind, [table], warnings, {"encoding": encoding, "delimiter": delimiter}
        return
    if options.encoding or options.delimiter:
        fail(
            "option_not_applicable",
            "CSV encoding and delimiter options do not apply to XLSX files.",
        )
    try:
        file.seek(0)
        with zipfile.ZipFile(file) as archive:
            entries = archive.infolist()
            names = {entry.filename for entry in entries}
            if (
                len(entries) > 1000
                or sum(entry.file_size for entry in entries)
                > settings.max_xlsx_uncompressed_bytes
            ):
                fail(
                    "archive_too_large",
                    "The expanded workbook exceeds the allowed size.",
                    413,
                )
            if not {"[Content_Types].xml", "xl/workbook.xml"} <= names:
                fail("invalid_xlsx", "The archive is not an XLSX workbook.")
            if any(
                name.lower().endswith(".bin") or "vbaproject" in name.lower()
                for name in names
            ):
                fail(
                    "macros_forbidden",
                    "Workbook macros and embedded binary content are not accepted.",
                )
        file.seek(0)
        workbook = load_workbook(
            file, read_only=True, data_only=False, keep_links=False
        )
    except ImportFailure:
        raise
    except Exception as exc:
        code = (
            "xlsx_xml_encoding_error"
            if isinstance(exc, (UnicodeError, LookupError))
            or "encoding" in str(exc).lower()
            else "invalid_xlsx"
        )
        fail(code, "The XLSX archive or its XML encoding is invalid.")
    try:
        names = options.sheets or workbook.sheetnames
        if len(names) > 20 or not set(names) <= set(workbook.sheetnames):
            fail("invalid_sheet_selection", "Select at most 20 existing worksheets.")
        tables = [
            _header(
                lambda sheet=workbook[name]: _xlsx_rows(sheet), options, settings, name
            )
            for name in names
        ]
        if len(tables) > 1 and not options.sheets:
            fail(
                "table_selection_required",
                "The workbook has multiple worksheets; select options.sheets explicitly.",
            )
        yield kind, tables, warnings, {"encoding": "XML declarations", "sheets": names}
    finally:
        workbook.close()

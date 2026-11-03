import math
import re
from datetime import date, datetime

from app.errors import fail

NUMERIC = re.compile(r"^(\d{1,2})([/.-])(\d{1,2})\2(\d{4})$")
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
YEAR = re.compile(r"^\d{4}$")


def date_text(value):
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or value != int(value):
            return ""
        return str(int(value))
    return value.strip() if isinstance(value, str) else ""


class DateDetector:
    """Une convention par fichier, prouvée avant toute écriture métier."""

    def __init__(self, allow_year: bool = False):
        self.allow_year = allow_year
        self.annual_seen = False
        self.shapes, self.evidence = {}, {}
        self.numeric_location = None
        self.format = None

    def observe(self, value, location):
        if isinstance(value, date) or value is None:
            return
        text = date_text(value)
        if re.fullmatch(r"\d{1,2}[/.-]\d{1,2}[/.-]\d{2}", text):
            fail("two_digit_year_forbidden", "Use four-digit years.", **location)
        if YEAR.fullmatch(text):
            if not self.allow_year:
                fail(
                    "incomplete_date",
                    "Year-only dates require year_policy='january_first'.",
                    **location,
                )
            self.annual_seen = True
        elif ISO.fullmatch(text):
            self.shapes.setdefault("iso", location)
        elif match := NUMERIC.fullmatch(text):
            a, separator, b, year = match.groups()
            self.shapes.setdefault(separator, location)
            self.numeric_location = self.numeric_location or location
            for order, day, month in (("DMY", int(a), int(b)), ("MDY", int(b), int(a))):
                if 13 <= day <= 31 and 1 <= month <= 12:
                    try:
                        date(int(year), month, day)
                    except ValueError:
                        continue
                    self.evidence.setdefault(order, location)

    def resolve(self):
        if len(self.shapes) > 1 or len(self.evidence) > 1:
            locations = self.shapes if len(self.shapes) > 1 else self.evidence
            fail(
                "mixed_date_format",
                "Use one consistent date convention throughout the file.",
                **list(locations.values())[-1],
            )
        if self.numeric_location and not self.evidence:
            fail(
                "ambiguous_date_format",
                "No valid day above 12 distinguishes day and month. Use explicit ISO dates.",
                **self.numeric_location,
            )
        if "iso" in self.shapes:
            self.format = "%Y-%m-%d"
        elif self.evidence:
            separator = next(iter(self.shapes))
            self.format = separator.join(
                ("%d", "%m", "%Y") if "DMY" in self.evidence else ("%m", "%d", "%Y")
            )

    def parse(self, value, location):
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = date_text(value)
        try:
            if YEAR.fullmatch(text) and self.allow_year:
                return date(int(text), 1, 1)
            if self.format and (
                ISO.fullmatch(text)
                if self.format == "%Y-%m-%d"
                else NUMERIC.fullmatch(text)
            ):
                return datetime.strptime(text, self.format).date()
        except ValueError:
            pass
        fail(
            "invalid_date",
            "Invalid calendar date for the detected convention.",
            **location,
        )

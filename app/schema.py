import unicodedata
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from app.errors import Issue


def normalize_text(value):
    return " ".join(value.split()) or None if isinstance(value, str) else value


def normalize_header(value: str) -> str:
    text = unicodedata.normalize("NFKD", value).casefold()
    return "".join(c for c in text if c.isalnum() and not unicodedata.combining(c))


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_max_length=2048)

    @field_validator("*", mode="before")
    @classmethod
    def clean_text(cls, value):
        return normalize_text(value)


class PersonData(InputModel):
    first_name: str = Field(
        min_length=1,
        json_schema_extra={
            "aliases": [
                "first_name",
                "firstname",
                "given_name",
                "prénom",
                "prenom",
                "raw_first",
            ],
            "split_aliases": {"dataFirstNames": 0},
        },
    )
    middle_name: str | None = Field(
        default=None,
        json_schema_extra={
            "aliases": [
                "middle_name",
                "middle_names",
                "second_prénom",
                "deuxième_prénom",
                "raw_middle",
            ],
            "split_aliases": {"dataFirstNames": 1},
        },
    )
    last_name: str = Field(
        min_length=1,
        json_schema_extra={
            "aliases": [
                "last_name",
                "lastname",
                "family_name",
                "surname",
                "nom",
                "nom_de_famille",
                "raw_last",
                "dataLastName",
            ]
        },
    )
    email: EmailStr | None = Field(
        default=None,
        json_schema_extra={
            "aliases": [
                "email",
                "e-mail",
                "courriel",
                "adresse_email",
                "dataEmail",
                "raw_email",
            ],
            "update": True,
            "history": True,
        },
    )
    phone: str | None = Field(
        default=None,
        json_schema_extra={
            "aliases": [
                "phone",
                "telephone",
                "téléphone",
                "tel",
                "dataPhone",
                "raw_phone",
            ],
            "update": True,
            "history": True,
        },
    )
    fax: str | None = Field(
        default=None,
        json_schema_extra={
            "aliases": ["fax", "dataFax", "raw_fax"],
            "update": True,
            "history": True,
        },
    )
    title: str | None = Field(
        default=None,
        json_schema_extra={
            "aliases": [
                "person_title",
                "civilité",
                "honorific",
                "dataTitle",
                "raw_title",
            ],
            "update": True,
            "history": True,
        },
    )


class OrganizationData(InputModel):
    org_name: str = Field(
        min_length=1,
        json_schema_extra={
            "aliases": [
                "org_name",
                "organization",
                "organisation",
                "organization_name",
                "nom_organisation",
                "dataOrganization",
                "raw_org_name",
            ]
        },
    )
    org_vivo_uri: str | None = Field(
        default=None,
        json_schema_extra={
            "aliases": ["org_vivo_uri", "organization_uri", "uri_organisation"]
        },
    )


class JobData(InputModel):
    job_title: str | None = Field(
        default=None,
        json_schema_extra={
            "aliases": [
                "job_title",
                "poste",
                "intitulé_poste",
                "dataJobTitle",
                "raw_job_title",
            ]
        },
    )
    job_type: str | None = Field(
        default=None,
        json_schema_extra={
            "aliases": [
                "job_type",
                "position_type",
                "type_poste",
                "dataPositionType",
                "raw_position_type",
            ]
        },
    )
    start_date: date | None = Field(
        default=None,
        json_schema_extra={
            "aliases": [
                "start_date",
                "job_start_date",
                "date_début",
                "date_debut_poste",
                "dataJobStartDate",
                "raw_date",
            ],
            "annual_aliases": ["dataJobStartDate", "raw_date"],
        },
    )

    @field_validator("start_date", mode="before")
    @classmethod
    def require_interpreted_date(cls, value):
        if value is not None and not isinstance(value, date):
            raise ValueError("Dates must be interpreted before validation.")
        return value

    @field_serializer("start_date", when_used="json")
    def public_date(self, value):
        return f"{value.day:02d}/{value.month:02d}/{value.year:04d}" if value else None


class Record(BaseModel):
    person: PersonData
    organization: OrganizationData | None = None
    job: JobData | None = None

    @model_validator(mode="after")
    def require_organization(self):
        if self.job is not None and self.organization is None:
            raise ValueError("A job requires an organization.")
        return self


# Les alias, politiques et validations des champs sont définis dans les modèles ci-dessus.
MODELS = {"person": PersonData, "organization": OrganizationData, "job": JobData}
IDENTITY_FIELDS = ("first_name", "middle_name", "last_name")
AMBIGUOUS_HEADERS = {"name", "title", "titre", "date", "uri"}
IGNORED_COLUMNS = {
    "dataURI": "Ontology class URI, not an organization identifier.",
    "raw_uri": "Ontology class URI, not an organization identifier.",
}


def target_fields():
    return {
        f"{section}.{name}": field
        for section, model in MODELS.items()
        for name, field in model.model_fields.items()
    }


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")
    encoding: str | None = Field(default=None, max_length=64)
    delimiter: Literal[",", ";", "\t", "|"] | None = None
    sheets: list[str] | None = Field(default=None, min_length=1, max_length=20)
    header_row: int | None = Field(
        default=None,
        ge=0,
        le=10000,
        description="Header line (1-based), or 0 for positional mapping @cN.",
    )
    mapping: dict[str, str] = Field(default_factory=dict, max_length=64)
    ignored_columns: list[str] = Field(default_factory=list, max_length=256)
    year_policy: Literal["reject", "january_first"] | None = None

    @model_validator(mode="after")
    def validate_mapping(self):
        if set(self.mapping) - target_fields().keys():
            raise ValueError("Unknown target field in mapping.")
        if any(
            not source.strip() or len(source) > 256 for source in self.mapping.values()
        ):
            raise ValueError(
                "Source column names must be nonempty and at most 256 characters."
            )
        if self.sheets and len(set(self.sheets)) != len(self.sheets):
            raise ValueError("Duplicate sheet selection.")
        return self


class UploadResponse(BaseModel):
    success: bool = True
    message: str = "File imported successfully"
    import_id: UUID
    rows_processed: int
    persons_created: int
    persons_updated: int
    persons_unchanged: int
    jobs_created: int
    warnings: list[Issue] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    success: bool = False
    message: str
    errors: list[Issue]
    errors_truncated: bool = False

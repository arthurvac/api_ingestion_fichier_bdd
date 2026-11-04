from dataclasses import dataclass

from app.errors import Issue, fail
from app.schema import (
    AMBIGUOUS_HEADERS,
    MODELS,
    Options,
    normalize_header,
    normalize_text,
    target_fields,
)


@dataclass(frozen=True)
class Column:
    source: str
    position: int
    split_index: int | None = None


def aliases():
    result = {}
    for target, field in target_fields().items():
        policy = field.json_schema_extra or {}
        for source in [target, *policy.get("aliases", [])]:
            result.setdefault(normalize_header(source), []).append((target, None))
        for source, index in policy.get("split_aliases", {}).items():
            result.setdefault(normalize_header(source), []).append((target, index))
    return {key: list(dict.fromkeys(values)) for key, values in result.items()}


def resolve_columns(headers: list[str], options: Options, sheet=None):
    index, fields = aliases(), target_fields()
    normalized = [normalize_header(name) for name in headers]
    if len(set(normalized)) != len(normalized):
        fail(
            "duplicate_headers",
            "Rename duplicate column headers before importing.",
            sheet=sheet,
        )
    resolved, used, warnings = {}, set(), []
    ignored = {normalize_header(name) for name in options.ignored_columns}
    for target, source in options.mapping.items():
        matches = [
            i
            for i, name in enumerate(headers)
            if (
                f"@c{i+1}" == source
                if source.startswith("@c")
                else normalize_header(name) == normalize_header(source)
            )
        ]
        if len(matches) != 1:
            fail(
                "source_column_missing",
                "The explicitly mapped source column is missing.",
                column=source,
                sheet=sheet,
            )
        i = matches[0]
        if normalized[i] in ignored:
            fail(
                "invalid_mapping",
                "A column cannot be both mapped and ignored.",
                column=headers[i],
                sheet=sheet,
            )
        splits = (fields[target].json_schema_extra or {}).get("split_aliases", {})
        split_index = next(
            (
                part
                for name, part in splits.items()
                if normalize_header(name) == normalized[i]
            ),
            None,
        )
        resolved[target] = Column(headers[i], i, split_index)
        used.add(i)
    for i, (name, normal) in enumerate(zip(headers, normalized)):
        if normal in ignored:
            continue
        if i in used and not any(
            split is not None for _, split in index.get(normal, [])
        ):
            continue
        candidates = [
            (target, split)
            for target, split in index.get(normal, [])
            if target not in options.mapping
        ]
        for target, split in candidates:
            if target in resolved:
                fail(
                    "ambiguous_mapping",
                    f"Multiple columns map to {target}; provide an explicit mapping.",
                    column=name,
                    sheet=sheet,
                )
            resolved[target] = Column(name, i, split)
            used.add(i)
    for i, (name, normal) in enumerate(zip(headers, normalized)):
        if i in used:
            continue
        if normal in AMBIGUOUS_HEADERS and normal not in ignored:
            fail(
                "ambiguous_mapping",
                "This column is ambiguous; map or ignore it explicitly.",
                column=name,
                sheet=sheet,
            )
        warnings.append(
            Issue(
                code="ignored_column",
                message="Column has no selected target and was ignored.",
                column=name,
                sheet=sheet,
            )
        )
    for section, model in MODELS.items():
        if section != "person":
            continue
        for name, field in model.model_fields.items():
            if field.is_required() and f"{section}.{name}" not in resolved:
                fail(
                    "missing_required_column",
                    "A required person column is missing.",
                    column=name,
                    sheet=sheet,
                )
    return resolved, warnings


def map_values(values, columns):
    result = {"person": {}}
    for target, column in columns.items():
        value = values[column.position]
        if column.split_index is not None and value is not None:
            if not isinstance(value, str):
                fail(
                    "invalid_value",
                    "A combined name must be text.",
                    column=column.source,
                )
            parts = value.split(",", 1)
            value = (
                parts[column.split_index] if column.split_index < len(parts) else None
            )
        section, name = target.split(".")
        result.setdefault(section, {})[name] = normalize_text(value)
    for section in ("organization", "job"):
        if section in result and all(
            value is None for value in result[section].values()
        ):
            del result[section]
    return result

import hashlib
import io
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = [
    ("data_1.csv", "4a7a22a6cb576764ebb852425fced810200e7af5b410e476b290ca145207df7b"),
    ("data_2.xlsx", "eef5af8704ca90e82eeef690a6145606e219ee1e6be9718ca01a4d95dd108e4d"),
    ("data_3.xlsx", "9cd3e55eaf07de34b5adf843d720ba05c26a51e1d4ef3041e6d9239b99d1f38b"),
    ("data_4.xlsx", "174214d46604760b050807067ca9c89c7562b6a0e63b7de690dd5e06fb24118b"),
]


@pytest.mark.reference
@pytest.mark.parametrize("filename,expected", REFERENCE)
def test_original_source_hashes_are_unchanged(filename, expected):
    path = ROOT / "data" / filename
    if not path.exists():
        pytest.skip("Local reference file is unavailable.")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected


def test_v1_has_a_single_initial_revision():
    from alembic.script import ScriptDirectory

    config = Config(str(ROOT / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)
    revisions = list(scripts.walk_revisions())
    assert scripts.get_heads() == ["0001_v1"]
    assert len(revisions) == 1 and revisions[0].down_revision is None
    assert [path.name for path in (ROOT / "migrations/versions").glob("*.py")] == [
        "0001_v1.py"
    ]


def test_initial_schema_compiles_offline_without_obsolete_tables():
    output = io.StringIO()
    config = Config(str(ROOT / "alembic.ini"), output_buffer=output)
    command.upgrade(config, "head", sql=True)
    sql = output.getvalue()
    assert sql.count("CREATE TABLE ") == 6
    for table in ["person", "organization", "job", "import_batch", "person_history"]:
        assert f"CREATE TABLE {table} (" in sql
    assert "provenance JSONB" in sql
    assert "NULLS NOT DISTINCT" in sql and "JSONB" in sql
    for name in [
        "mapping_profile",
        "mapping_profile_revision",
        "import_analysis",
        "analysis_diagnostic",
        "import_error",
    ]:
        assert name not in sql
    assert "DROP TABLE" not in sql

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.database import get_session
from app.main import create_app


@pytest.fixture
def client():
    app = create_app(Settings(max_upload_bytes=4096))
    app.dependency_overrides[get_session] = lambda: None
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_only_upload_is_a_business_endpoint(client):
    schema = client.get("/openapi.json").json()
    assert set(schema["paths"]) == {"/upload", "/health"}
    assert client.get("/docs").status_code == 200
    for path in [
        "/formats",
        "/mapping-profiles",
        "/import-analyses",
        "/ingestion/schema",
        "/ingestion/resources",
        "/imports/anything",
    ]:
        response = client.get(path)
        assert response.status_code == 404
        assert response.json()["message"] == "Endpoint not found"


@pytest.mark.parametrize(
    "content,filename,code,status",
    [
        (b"", "empty.csv", "empty_file", 422),
        (b"MZbinary", "fake.csv", "unsupported_content", 415),
        (b"PKbroken", "fake.xlsx", "invalid_xlsx", 422),
        (
            b"first_name;name\nAlice;Example\n",
            "ambiguous.csv",
            "ambiguous_mapping",
            422,
        ),
    ],
)
def test_file_errors_are_english_and_explicit(client, content, filename, code, status):
    response = client.post("/upload", files={"file": (filename, content)})
    assert response.status_code == status
    body = response.json()
    assert body["success"] is False
    assert body["errors"][0]["code"] == code
    assert "Traceback" not in response.text


def test_missing_file_and_invalid_options_hide_values(client):
    assert client.post("/upload").status_code == 422
    response = client.post(
        "/upload",
        files={"file": ("source.csv", b"first_name;last_name\nAlice;Example\n")},
        data={"options": '{"analysis_id":"private-sensitive-value"}'},
    )
    assert response.status_code == 422
    assert "private-sensitive-value" not in response.text
    assert response.json()["errors"][0]["column"] == "options"
    assert response.json()["message"].startswith("Options must be")


def test_upload_limit_applies_inside_multipart(client):
    response = client.post("/upload", files={"file": ("large.csv", b"x" * 4097)})
    assert response.status_code == 413
    assert response.json()["errors"][0]["code"] == "file_too_large"


def test_body_without_content_length_is_bounded(client):
    response = client.post(
        "/upload",
        content=(block for block in [b"x" * 80000]),
        headers={"content-type": "multipart/form-data; boundary=abc"},
    )
    assert response.status_code == 413
    assert response.json()["success"] is False


def test_database_error_is_sanitized():
    app = create_app()

    def unavailable():
        raise SQLAlchemyError("private database host and password")

    app.dependency_overrides[get_session] = unavailable
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/health")
        assert response.status_code == 503
        assert "private" not in response.text and "password" not in response.text
        assert response.json()["errors"][0]["code"] == "database_unavailable"


def test_production_requires_external_secret():
    with pytest.raises(ValueError):
        Settings(environment="production")

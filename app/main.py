import csv
import logging
from pathlib import PurePosixPath

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartParser

from app.config import Settings, get_settings
from app.database import get_session
from app.errors import ImportFailure, Issue, fail
from app.http_limits import UploadLimits
from app.importer import import_file
from app.schema import ErrorResponse, Options, UploadResponse

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    MultiPartParser.spool_max_size = 64 * 1024
    csv.field_size_limit(settings.max_upload_bytes)
    app = FastAPI(
        title="CSV/XLSX ingestion API",
        version="1.0.0",
        responses={
            status: {"model": ErrorResponse}
            for status in (400, 409, 413, 415, 422, 429, 500, 503)
        },
    )
    app.add_middleware(
        UploadLimits,
        max_bytes=settings.max_upload_bytes + 64 * 1024,
        max_uploads=settings.max_active_uploads,
    )

    @app.post("/upload", response_model=UploadResponse)
    def upload(
        file: UploadFile = File(...),
        options: str | None = Form(
            None,
            description="Optional JSON: mapping (target to source column), ignored_columns, sheets, header_row, encoding, delimiter, year_policy.",
        ),
        session: Session = Depends(get_session),
    ):
        try:
            try:
                config = Options.model_validate_json(options) if options else Options()
            except (ValueError, ValidationError):
                fail(
                    "invalid_options",
                    "Options must be a valid JSON object with supported fields and target names.",
                    column="options",
                )
            filename = PurePosixPath(
                (file.filename or "upload").replace("\\", "/")
            ).name
            filename = "".join(c for c in filename if c.isprintable())[:255] or "upload"
            return import_file(file.file, filename, config, session, settings)
        finally:
            file.file.close()

    @app.get("/health", tags=["Technical"])
    def health(session: Session = Depends(get_session)):
        session.execute(text("SELECT 1"))
        return {"success": True, "message": "API and database are available"}

    @app.exception_handler(ImportFailure)
    async def import_failure(request: Request, exc: ImportFailure):
        body = ErrorResponse(
            message=exc.message, errors=exc.errors, errors_truncated=exc.truncated
        )
        return JSONResponse(body.model_dump(mode="json"), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        body = ErrorResponse(
            message="Invalid request parameters",
            errors=[
                Issue(
                    code="invalid_request",
                    message="Provide a file and valid multipart fields.",
                )
            ],
        )
        return JSONResponse(body.model_dump(mode="json"), status_code=422)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        message = {
            404: "Endpoint not found",
            405: "Method not allowed",
            413: "The request exceeds the upload size limit.",
        }.get(exc.status_code, "Invalid HTTP or multipart request")
        body = ErrorResponse(
            message=message,
            errors=[
                Issue(
                    code="file_too_large" if exc.status_code == 413 else "http_error",
                    message=message,
                )
            ],
        )
        return JSONResponse(body.model_dump(mode="json"), status_code=exc.status_code)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError):
        logger.error("Échec SQL de la requête (%s)", type(exc).__name__)
        body = ErrorResponse(
            message="Database unavailable; no import was committed",
            errors=[
                Issue(
                    code="database_unavailable",
                    message="The database operation could not be completed.",
                )
            ],
        )
        return JSONResponse(body.model_dump(mode="json"), status_code=503)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        logger.error("Échec de la requête (%s)", type(exc).__name__)
        body = ErrorResponse(
            message="The import could not be completed",
            errors=[
                Issue(code="internal_error", message="Unexpected processing failure.")
            ],
        )
        return JSONResponse(body.model_dump(mode="json"), status_code=500)

    return app


app = create_app()

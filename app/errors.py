from pydantic import BaseModel


class Issue(BaseModel):
    code: str
    message: str
    row: int | None = None
    column: str | None = None
    sheet: str | None = None


class ImportFailure(Exception):
    def __init__(
        self,
        message: str,
        errors: list[Issue],
        status: int = 422,
        *,
        truncated: bool = False,
    ):
        super().__init__(message)
        self.message, self.errors, self.status, self.truncated = (
            message,
            errors,
            status,
            truncated,
        )


def fail(code: str, message: str, status: int = 422, **location):
    raise ImportFailure(
        message, [Issue(code=code, message=message, **location)], status
    )

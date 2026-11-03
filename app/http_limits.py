import threading

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse


class UploadLimits:
    """Borne le corps HTTP et l'admission avant le parsing multipart."""

    def __init__(self, app, max_bytes, max_uploads):
        self.app, self.max_bytes = app, max_bytes
        self.slots = threading.BoundedSemaphore(max_uploads)

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != "/upload"
        ):
            await self.app(scope, receive, send)
            return
        try:
            declared = int(dict(scope.get("headers", [])).get(b"content-length", b"0"))
        except ValueError:
            declared = 0
        if declared > self.max_bytes:
            await self._refuse(
                scope,
                receive,
                send,
                413,
                "file_too_large",
                "The request exceeds the upload size limit.",
            )
            return
        if not self.slots.acquire(blocking=False):
            await self._refuse(
                scope,
                receive,
                send,
                429,
                "upload_busy",
                "Too many active uploads; retry shortly.",
            )
            return
        consumed = 0

        async def limited_receive():
            nonlocal consumed
            message = await receive()
            consumed += len(message.get("body", b""))
            if consumed > self.max_bytes:
                raise HTTPException(413, "Upload size limit exceeded.")
            return message

        try:
            await self.app(scope, limited_receive, send)
        finally:
            self.slots.release()

    async def _refuse(self, scope, receive, send, status, code, message):
        await JSONResponse(
            {
                "success": False,
                "message": message,
                "errors": [{"code": code, "message": message}],
                "errors_truncated": False,
            },
            status_code=status,
            headers={"Retry-After": "2"} if status == 429 else None,
        )(scope, receive, send)

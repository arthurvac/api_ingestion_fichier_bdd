FROM python:3.13-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 OPENPYXL_LXML=False TMPDIR=/service/tmp
WORKDIR /service
COPY requirements.lock ./
RUN python -m pip install --no-cache-dir -r requirements.lock
RUN groupadd --gid 10001 ingestion && useradd --uid 10001 --gid ingestion --create-home ingestion
RUN mkdir -p /service/tmp && chown ingestion:ingestion /service/tmp
COPY pyproject.toml alembic.ini ./
COPY app ./app
COPY migrations ./migrations

FROM base AS runtime
USER ingestion
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log"]

FROM base AS test
COPY requirements-dev.lock ./
RUN python -m pip install --no-cache-dir -r requirements-dev.lock
COPY tests ./tests
RUN chown -R ingestion:ingestion /service
USER ingestion
CMD ["python", "-m", "pytest", "-q", "--tb=short"]

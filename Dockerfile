FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock README.md ./
COPY app ./app
COPY evals ./evals
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

EXPOSE 8000
CMD ["/bin/sh", "-c", "uv run alembic upgrade head && uv run python -m app.seed && exec uv run uvicorn app.main:app --host 0.0.0.0 --port 8000"]


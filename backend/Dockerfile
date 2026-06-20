FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY alembic.ini /app/alembic.ini
COPY migrations /app/migrations
COPY puppyrun_api /app/puppyrun_api
COPY puppyrun_worker /app/puppyrun_worker
COPY puppyrun_agent /app/puppyrun_agent
COPY puppyrun_eval /app/puppyrun_eval

RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .

CMD ["sh", "-c", "uvicorn puppyrun_api.main:app --host ${PUPPYRUN_API_HOST:-0.0.0.0} --port ${PORT:-${PUPPYRUN_API_PORT:-8000}}"]

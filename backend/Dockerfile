FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY . /app
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -e ".[dev]"

CMD ["uvicorn", "puppyrun_api.main:app", "--host", "0.0.0.0", "--port", "8000"]

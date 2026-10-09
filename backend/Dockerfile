# Foresight FastAPI backend.
#
# No secrets are baked into the image: backend/.env is excluded by
# .dockerignore and supplied at runtime by docker-compose.yml (env_file).
# Python matches the version the project is developed and tested on (3.14 —
# see the note in requirements.txt about pydantic-core wheels).
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

RUN useradd --create-home --uid 10001 foresight
USER foresight

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

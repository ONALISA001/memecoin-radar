FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# API on $PORT (Railway/Render inject it; defaults to 8000 locally);
# the ingestion scheduler runs inside the app lifespan.
# For worker separation (prod), run `celery -A app.worker worker -B` in a second
# container sharing the same DATABASE_URL (see docs/ARCHITECTURE.md).
EXPOSE 8000
CMD ["sh", "-c", "python seed.py && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

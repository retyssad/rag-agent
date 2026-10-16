FROM python:3.11.12-slim
WORKDIR /app
COPY backend/requirements.lock.txt /app/backend/requirements.lock.txt
RUN pip install --no-cache-dir -r backend/requirements.lock.txt
COPY backend /app/backend
COPY data /app/data
ENV PYTHONPATH=/app/backend PYTHONUNBUFFERED=1
CMD ["sh", "-c", "alembic -c backend/alembic.ini upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"]

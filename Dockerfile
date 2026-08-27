FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY truth/ ./truth/
COPY app/ ./app/

# Cloud Run передаёт порт через $PORT
ENV PORT=8080
CMD exec uvicorn app.main:app --host 0.0.0.0 --port $PORT --timeout-keep-alive 600

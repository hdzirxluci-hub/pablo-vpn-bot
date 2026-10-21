FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

RUN addgroup --system pablo && adduser --system --ingroup pablo pablo

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=pablo:pablo app ./app
RUN chown pablo:pablo /app

USER pablo
EXPOSE 8080

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]

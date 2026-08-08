FROM python:3.13.7-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SCANNER_HOST=0.0.0.0 \
    SCANNER_PORT=8080 \
    CLAMD_HOST=clamav \
    CLAMD_PORT=3310

WORKDIR /app
COPY adapter.py /app/adapter.py

USER 65532:65532
EXPOSE 8080
CMD ["python", "/app/adapter.py"]

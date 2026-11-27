FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    JOB_MONITOR_STORE=firestore

WORKDIR /app
COPY pyproject.toml README.md ./
COPY job_board_mcp ./job_board_mcp
RUN python -m pip install --no-cache-dir '.[gcp]'

EXPOSE 8080
CMD ["python", "-m", "job_board_mcp.server", "--transport", "streamable-http"]

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /workspace
COPY services/worker /workspace/services/worker
RUN pip install --no-cache-dir /workspace/services/worker
RUN useradd --create-home --uid 10001 teamora
USER teamora
CMD ["python", "-m", "teamora_worker.main"]

FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN addgroup --system teamora && adduser --system --ingroup teamora teamora
WORKDIR /app

COPY pyproject.toml ./
COPY teamora_api ./teamora_api
RUN pip install .

USER teamora
EXPOSE 8000
CMD ["uvicorn", "teamora_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]

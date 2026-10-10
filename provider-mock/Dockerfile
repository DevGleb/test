FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY provider_mock ./provider_mock
RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8001
CMD ["uvicorn", "provider_mock.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8001"]

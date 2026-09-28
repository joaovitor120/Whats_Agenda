FROM python:3.14.3-slim-bookworm@sha256:f21c0d5a44c56805654c15abccc1b2fd576c8d93aca0a3f74b4aba2dc92510e2
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && groupadd --gid 10001 agenda \
    && useradd --uid 10001 --gid 10001 --no-create-home agenda
COPY --chown=10001:10001 app ./app
COPY --chown=10001:10001 alembic ./alembic
COPY --chown=10001:10001 alembic.ini ./
USER 10001:10001
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers"]


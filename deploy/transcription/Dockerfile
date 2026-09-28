FROM python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HUB_DISABLE_TELEMETRY=1 HF_HUB_DISABLE_XET=1 OMP_NUM_THREADS=2
WORKDIR /srv
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && groupadd --gid 10001 speech \
    && useradd --uid 10001 --gid 10001 --no-create-home speech
RUN python -c "import httpx; from huggingface_hub import snapshot_download, set_client_factory; set_client_factory(lambda: httpx.Client(trust_env=False, follow_redirects=True, timeout=60)); snapshot_download('Systran/faster-whisper-small', revision='536b0662742c02347bc0e980a01041f333bce120', local_dir='/opt/whisper-small', allow_patterns=['model.bin', 'config.json', 'tokenizer.json', 'vocabulary.*'])"
ENV HF_HUB_OFFLINE=1
COPY --chown=10001:10001 server.py ./
USER 10001:10001
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001", "--no-access-log", "--no-proxy-headers"]

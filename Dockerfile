FROM python:3.13-slim-bookworm@sha256:9d7f287598e1a5a978c015ee176d8216435aaf335ed69ac3c38dd1bbb10e8d64

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN groupadd --gid 10001 router \
    && useradd --uid 10001 --gid router --no-create-home --shell /usr/sbin/nologin router

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY router ./router
COPY shared ./shared
COPY run_router.py ./run_router.py

USER router

EXPOSE 8787

CMD ["python", "run_router.py"]

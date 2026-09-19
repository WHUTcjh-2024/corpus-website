FROM node:22-alpine@sha256:b6f26b36c8ff49624cfdac716b8ea1138d606df02586a77d364bb5536a634f85 AS frontend

WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build -- --outDir /tmp/frontend

FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS runtime

ARG APP_UID=10001
ARG APP_GID=10001

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get upgrade -y \
    && groupadd --gid "${APP_GID}" corpus \
    && useradd --uid "${APP_UID}" --gid corpus --no-create-home \
        --home-dir /app/backend --shell /usr/sbin/nologin corpus \
    && install -d -o corpus -g corpus /app/backend /app/backend/staticfiles /app/data \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

COPY backend/requirements.txt /tmp/requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r /tmp/requirements.txt \
    && python -m pip uninstall --yes pip setuptools

COPY --chown=corpus:corpus backend/ /app/backend
COPY --from=frontend --chown=corpus:corpus /tmp/frontend/ /app/backend/static/frontend/

# Collapse the patched filesystem so scanners do not retain superseded Python
# package metadata from immutable upstream layers.
FROM scratch

COPY --from=runtime / /

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app/backend

USER 10001:10001

EXPOSE 8000

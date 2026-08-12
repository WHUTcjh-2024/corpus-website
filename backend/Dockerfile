FROM golang:1.26-alpine AS corpus_auditor_builder

WORKDIR /src/go/corpus-auditor
COPY go/corpus-auditor/go.mod ./
RUN go mod download
COPY go/corpus-auditor ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/corpus-auditor ./cmd/corpus-auditor

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app/backend

COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r /tmp/requirements.txt

COPY . /app/backend
COPY --from=corpus_auditor_builder /out/corpus-auditor /usr/local/bin/corpus-auditor

EXPOSE 8000

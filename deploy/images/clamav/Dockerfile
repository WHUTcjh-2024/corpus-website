FROM clamav/clamav:1.4.6-debian13-slim@sha256:2463487b937071421c0ddadac6616111bedba45b9bd9312bbb4591b57450f8f4

USER root
RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

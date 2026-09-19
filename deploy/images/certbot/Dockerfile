FROM certbot/certbot:v5.8.0@sha256:f70ad0adbb7e117f0fe42a63c553f28ea451edabc0148757b6efcd9735acaa20 AS patched

USER root
RUN apk upgrade --no-cache \
    && python -m pip uninstall --yes pip setuptools

# Rebase the merged filesystem into one layer. Trivy would otherwise report
# removed Python metadata from the immutable upstream layer as still installed.
FROM scratch

COPY --from=patched / /

ENV PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ENV PYTHON_VERSION=3.14.7
ENV PYTHON_SHA256=3b48dac8fb59f62eaa67ac83c1eb12bda1b7a08406dd286e252c11a66be27f81

WORKDIR /opt/certbot
VOLUME ["/etc/letsencrypt", "/var/lib/letsencrypt"]
ENTRYPOINT ["certbot"]

FROM golang:1.26.6-alpine@sha256:3889b425f035be855a72fb4755265311293b6d414521f0a519d819df32222d83 AS build

ARG PROMETHEUS_VERSION=3.14.0
ARG PROMETHEUS_REVISION=d7598b7141418fa35be2b5ec5d0fefb634199610
ARG PROMETHEUS_UI_SHA256=be18623c5891d32572998070de0d48522c966b737d9204aa41e0e88d6318e029

RUN apk add --no-cache bash curl git gzip \
    && git init /src \
    && git -C /src remote add origin https://github.com/prometheus/prometheus.git \
    && git -C /src fetch --depth=1 origin \
        "refs/tags/v${PROMETHEUS_VERSION}:refs/tags/v${PROMETHEUS_VERSION}" \
    && git -C /src checkout --detach "v${PROMETHEUS_VERSION}" \
    && test "$(git -C /src rev-parse HEAD)" = "${PROMETHEUS_REVISION}"

WORKDIR /src
RUN curl --fail --location --silent --show-error \
        "https://github.com/prometheus/prometheus/releases/download/v${PROMETHEUS_VERSION}/prometheus-web-ui-${PROMETHEUS_VERSION}.tar.gz" \
        --output /tmp/prometheus-web-ui.tar.gz \
    && echo "${PROMETHEUS_UI_SHA256}  /tmp/prometheus-web-ui.tar.gz" | sha256sum -c \
    && mkdir -p /tmp/prometheus-web-ui \
    && tar --extract --gzip --file /tmp/prometheus-web-ui.tar.gz --directory /tmp/prometheus-web-ui \
    && PREBUILT_ASSETS_STATIC_DIR=/tmp/prometheus-web-ui/static ./scripts/compress_assets.sh \
    && go get golang.org/x/crypto@v0.55.0 google.golang.org/grpc@v1.83.2 \
    && go mod tidy \
    && CGO_ENABLED=0 go build -trimpath -tags netgo,builtinassets \
        -ldflags="-s -w -X github.com/prometheus/common/version.Version=${PROMETHEUS_VERSION} -X github.com/prometheus/common/version.Revision=${PROMETHEUS_REVISION}" \
        -o /out/prometheus ./cmd/prometheus \
    && CGO_ENABLED=0 go build -trimpath -tags netgo,builtinassets \
        -ldflags="-s -w -X github.com/prometheus/common/version.Version=${PROMETHEUS_VERSION} -X github.com/prometheus/common/version.Revision=${PROMETHEUS_REVISION}" \
        -o /out/promtool ./cmd/promtool

FROM alpine:3.22@sha256:14358309a308569c32bdc37e2e0e9694be33a9d99e68afb0f5ff33cc1f695dce

RUN apk upgrade --no-cache \
    && apk add --no-cache ca-certificates tzdata \
    && mkdir -p /etc/prometheus /prometheus \
    && chown 65534:65534 /prometheus

COPY --from=build /out/prometheus /bin/prometheus
COPY --from=build /out/promtool /bin/promtool
COPY --from=build /src/documentation/examples/prometheus.yml /etc/prometheus/prometheus.yml

USER 65534:65534
WORKDIR /prometheus
EXPOSE 9090
VOLUME ["/prometheus"]
ENTRYPOINT ["/bin/prometheus"]

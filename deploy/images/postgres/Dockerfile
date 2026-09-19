FROM golang:1.26.6-alpine@sha256:3889b425f035be855a72fb4755265311293b6d414521f0a519d819df32222d83 AS gosu-build

ARG GOSU_VERSION=v0.0.0-20260606051551-40506998e34a
RUN GOBIN=/out CGO_ENABLED=0 go install github.com/tianon/gosu@${GOSU_VERSION}

FROM postgres:16.15-alpine@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685

COPY --from=gosu-build /out/gosu /usr/local/bin/gosu
RUN apk upgrade --no-cache

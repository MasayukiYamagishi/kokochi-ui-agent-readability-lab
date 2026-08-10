FROM golang:1.25.0-alpine3.22 AS healthcheck-builder

WORKDIR /src
COPY collector-healthcheck.go .
RUN CGO_ENABLED=0 GOOS=linux go build \
    -trimpath \
    -ldflags="-s -w" \
    -o /out/collector-healthcheck \
    collector-healthcheck.go

FROM otel/opentelemetry-collector:0.156.0

COPY --from=healthcheck-builder /out/collector-healthcheck /collector-healthcheck

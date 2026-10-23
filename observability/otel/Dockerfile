FROM golang:1.26.10-alpine AS healthcheck-builder

RUN apk upgrade --no-cache ca-certificates

WORKDIR /src
COPY collector-healthcheck.go .
RUN CGO_ENABLED=0 GOOS=linux go build \
    -trimpath \
    -ldflags="-s -w" \
    -o /out/collector-healthcheck \
    collector-healthcheck.go

FROM otel/opentelemetry-collector:0.163.2

COPY --from=healthcheck-builder /out/collector-healthcheck /collector-healthcheck

COPY --from=healthcheck-builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt

# ---- payment service ----
# Stage 1: compile a static binary.
FROM golang:1.23-alpine AS build
WORKDIR /src
COPY go.mod ./
COPY *.go ./
ARG APP_VERSION=dev
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/payment .

# Stage 2: distroless = no shell, no package manager, tiny attack surface.
# (You can't `kubectl exec` a shell in here -- use `kubectl debug` instead, see docs/labs/12-debugging.md)
FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/payment /payment
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}
USER nonroot:nonroot
EXPOSE 8080
ENTRYPOINT ["/payment"]

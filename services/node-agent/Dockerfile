# ---- node-agent (DaemonSet) ----
# Stage 1: compile a static binary.
FROM golang:1.23-alpine AS build
WORKDIR /src
COPY go.mod ./
COPY *.go ./
ARG APP_VERSION=dev
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/node-agent .

# Stage 2: distroless = no shell, no package manager, tiny attack surface.
FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/node-agent /node-agent
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}
USER nonroot:nonroot
EXPOSE 9100
ENTRYPOINT ["/node-agent"]

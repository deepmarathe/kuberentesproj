# ---- frontend ----
# nginx-unprivileged listens on 8080 and runs as uid 101 (no root needed).
FROM nginxinc/nginx-unprivileged:1.27-alpine
USER root
# Directory for the access log that the "access-log" sidecar tails (an emptyDir is mounted here in Kubernetes).
RUN mkdir -p /var/log/shopfleet && chown 101:101 /var/log/shopfleet
USER 101
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY public/ /usr/share/nginx/html/
EXPOSE 8080

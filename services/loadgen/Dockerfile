# ---- load generator (Locust) ----
FROM locustio/locust:2.31.8
COPY locustfile.py /home/locust/locustfile.py
# Image entrypoint is `locust`; arguments are supplied by the Kubernetes Deployment.

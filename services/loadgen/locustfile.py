"""Simulated shoppers for ShopFleet.

Traffic follows a compressed "day": users ramp between MIN_USERS and MAX_USERS over
PERIOD_MINUTES (default 60 -> one "day" per hour), so autoscaling has real work to do.
Optionally a "flash sale" spike every SPIKE_EVERY_MINUTES.

Open the Locust UI with:  kubectl -n loadgen port-forward svc/loadgen 8089:8089
"""
import math
import os
import random
import uuid

from locust import HttpUser, LoadTestShape, between, task

MIN_USERS = int(os.getenv("MIN_USERS", "3"))
MAX_USERS = int(os.getenv("MAX_USERS", "25"))
PERIOD = int(os.getenv("PERIOD_MINUTES", "60")) * 60
SPIKE_EVERY = int(os.getenv("SPIKE_EVERY_MINUTES", "0")) * 60
SPIKE_USERS = int(os.getenv("SPIKE_USERS", "80"))
SPIKE_SECONDS = int(os.getenv("SPIKE_SECONDS", "120"))
ACCOUNT_RATIO = float(os.getenv("ACCOUNT_RATIO", "0.3"))
REVIEW_TITLES = ["Great value", "Works as described", "Would buy again", "Fast shipping", "Exactly what I needed"]
SEARCH_TERMS = ["kube", "coffee", "tent", "watch", "book", "honey", "game", "light", "zzz-no-match"]


class Shopper(HttpUser):
    wait_time = between(1, 4)

    def on_start(self):
        self.client.headers["X-Session-Id"] = str(uuid.uuid4())
        self.products = []
        self.cart_items = 0
        self.refresh_products()
        # ~1/3 of shoppers create an account (bcrypt makes this CPU-heavy -> auth HPA scales).
        if random.random() < ACCOUNT_RATIO:
            email = f"shopper-{uuid.uuid4().hex[:12]}@loadgen.local"
            r = self.client.post("/api/auth/register", name="/api/auth/register",
                                 json={"email": email, "name": "Load Shopper", "password": "loadgen-password"})
            if r.ok:
                self.client.headers["Authorization"] = f"Bearer {r.json()['token']}"

    def refresh_products(self):
        with self.client.get("/api/catalog/products", name="/api/catalog/products", catch_response=True) as r:
            if r.ok:
                self.products = r.json().get("items", [])
            else:
                r.failure(f"status {r.status_code}")

    @task(10)
    def browse(self):
        self.client.get("/", name="/ (static)")
        self.refresh_products()

    @task(3)
    def browse_category(self):
        cat = random.choice(["Electronics", "Home", "Outdoors", "Books", "Grocery", "Toys"])
        self.client.get(f"/api/catalog/products?category={cat}", name="/api/catalog/products?category")

    @task(4)
    def search(self):
        self.client.get(f"/api/catalog/products?q={random.choice(SEARCH_TERMS)}", name="/api/catalog/products?q")

    @task(5)
    def view_product(self):
        if self.products:
            p = random.choice(self.products)
            self.client.get(f"/api/catalog/products/{p['id']}", name="/api/catalog/products/:id")

    @task(4)
    def add_to_cart(self):
        if self.products:
            p = random.choice(self.products)
            r = self.client.post("/api/cart/items", json={"product_id": p["id"], "qty": random.randint(1, 3)},
                                 name="/api/cart/items")
            if r.ok:
                self.cart_items += 1

    @task(2)
    def view_cart(self):
        self.client.get("/api/cart", name="/api/cart")

    @task(2)
    def checkout(self):
        if not self.cart_items:
            return
        # 402 (declined) and 409 (out of stock) are business outcomes, not errors.
        with self.client.post("/api/orders/checkout", name="/api/orders/checkout", catch_response=True) as r:
            if r.status_code in (201, 402, 409):
                r.success()
                self.cart_items = 0
            else:
                r.failure(f"checkout failed: {r.status_code}")

    @task(1)
    def write_review(self):
        if "Authorization" in self.client.headers and self.products:
            p = random.choice(self.products)
            self.client.post(f"/api/catalog/products/{p['id']}/reviews", name="/api/catalog/products/:id/reviews",
                             json={"rating": random.choice([3, 4, 4, 5, 5, 5]), "title": random.choice(REVIEW_TITLES)})

    @task(1)
    def product_details(self):
        if self.products:
            pid = random.choice(self.products)["id"]
            self.client.get(f"/api/catalog/products/{pid}/reviews", name="/api/catalog/products/:id/reviews")
            self.client.get(f"/api/catalog/products/{pid}/recommendations", name="/api/catalog/products/:id/recommendations")

    @task(1)
    def order_history(self):
        self.client.get("/api/orders", name="/api/orders")


class DailyCycle(LoadTestShape):
    """Smooth day/night curve plus optional flash-sale spikes."""

    def tick(self):
        t = self.get_run_time()
        users = MIN_USERS + (MAX_USERS - MIN_USERS) * (1 - math.cos(2 * math.pi * t / PERIOD)) / 2
        if SPIKE_EVERY and (t % SPIKE_EVERY) < SPIKE_SECONDS:
            users = SPIKE_USERS
        return max(1, round(users)), 5

-- Products are owned by the catalog service.
CREATE TABLE IF NOT EXISTS products (
    id           SERIAL PRIMARY KEY,
    sku          TEXT UNIQUE NOT NULL,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    category     TEXT NOT NULL,
    price_cents  INTEGER NOT NULL CHECK (price_cents > 0),
    emoji        TEXT NOT NULL DEFAULT '📦',
    stock        INTEGER NOT NULL DEFAULT 0 CHECK (stock >= 0),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS products_category_idx ON products (category);

-- Orders are owned by the orders service (worker updates status).
CREATE TABLE IF NOT EXISTS orders (
    id              UUID PRIMARY KEY,
    session_id      TEXT NOT NULL,
    status          TEXT NOT NULL,
    total_cents     INTEGER NOT NULL,
    payment_id      TEXT,
    tracking_number TEXT,
    failure_reason  TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS orders_session_idx ON orders (session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS orders_status_idx  ON orders (status, created_at);

CREATE TABLE IF NOT EXISTS order_items (
    order_id     UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id   INTEGER NOT NULL,
    name         TEXT NOT NULL,
    qty          INTEGER NOT NULL,
    price_cents  INTEGER NOT NULL,
    PRIMARY KEY (order_id, product_id)
);

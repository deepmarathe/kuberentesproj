-- Accounts (owned by the auth service).
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    email         TEXT UNIQUE NOT NULL,
    name          TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'customer' CHECK (role IN ('customer', 'admin')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Orders can now belong to a logged-in user (guest checkout still works).
ALTER TABLE orders ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id);
CREATE INDEX IF NOT EXISTS orders_user_idx ON orders (user_id, created_at DESC);

-- Product reviews (owned by catalog). One review per user per product.
CREATE TABLE IF NOT EXISTS reviews (
    id          SERIAL PRIMARY KEY,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    rating      INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    title       TEXT NOT NULL,
    body        TEXT NOT NULL DEFAULT '',
    verified    BOOLEAN NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (product_id, user_id)
);
CREATE INDEX IF NOT EXISTS reviews_product_idx ON reviews (product_id, created_at DESC);

-- Category-wide discounts. Written ONLY by the FlashSale operator via catalog's internal API.
CREATE TABLE IF NOT EXISTS promotions (
    name        TEXT PRIMARY KEY,          -- "<namespace>-<flashsale name>"
    title       TEXT NOT NULL,
    category    TEXT NOT NULL,
    percent     INTEGER NOT NULL CHECK (percent BETWEEN 1 AND 90),
    ends_at     TIMESTAMPTZ NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

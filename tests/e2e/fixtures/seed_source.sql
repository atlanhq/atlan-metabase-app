-- Postgres source seed for Metabase e2e lineage testing.
--
-- The Metabase server registers this database as a data source. The seed
-- script then creates ~1000 assets (collections / questions / dashboards)
-- where the native-SQL questions reference these tables in many shapes —
-- single-table, joins, projections, where-clauses, group-by, sub-queries.
--
-- Two schemas (analytics + reports) so we can exercise cross-schema
-- references and verify the ARS lineage builder pulls schema-name out of
-- the parsed SQL.

CREATE SCHEMA IF NOT EXISTS analytics;
CREATE SCHEMA IF NOT EXISTS reports;

-- ── analytics: transactional tables ────────────────────────────────────────

CREATE TABLE IF NOT EXISTS analytics.customers (
    customer_id   SERIAL PRIMARY KEY,
    customer_name TEXT NOT NULL,
    email         TEXT,
    country       TEXT NOT NULL,
    segment       TEXT,
    signed_up_at  TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS analytics.products (
    product_id   SERIAL PRIMARY KEY,
    product_name TEXT NOT NULL,
    category     TEXT NOT NULL,
    sku          TEXT UNIQUE,
    unit_price   NUMERIC(10, 2) NOT NULL,
    cost_price   NUMERIC(10, 2)
);

CREATE TABLE IF NOT EXISTS analytics.orders (
    order_id      SERIAL PRIMARY KEY,
    customer_id   INTEGER REFERENCES analytics.customers(customer_id),
    order_total   NUMERIC(10, 2) NOT NULL,
    currency      TEXT DEFAULT 'USD',
    status        TEXT,
    placed_at     TIMESTAMP DEFAULT now(),
    shipped_at    TIMESTAMP
);

CREATE TABLE IF NOT EXISTS analytics.order_items (
    order_item_id SERIAL PRIMARY KEY,
    order_id      INTEGER REFERENCES analytics.orders(order_id),
    product_id    INTEGER REFERENCES analytics.products(product_id),
    quantity      INTEGER NOT NULL,
    unit_price    NUMERIC(10, 2) NOT NULL
);

CREATE TABLE IF NOT EXISTS analytics.invoices (
    invoice_id    SERIAL PRIMARY KEY,
    order_id      INTEGER REFERENCES analytics.orders(order_id),
    amount        NUMERIC(10, 2) NOT NULL,
    issued_at     TIMESTAMP DEFAULT now(),
    paid_at       TIMESTAMP
);

CREATE TABLE IF NOT EXISTS analytics.payments (
    payment_id    SERIAL PRIMARY KEY,
    invoice_id    INTEGER REFERENCES analytics.invoices(invoice_id),
    method        TEXT NOT NULL,
    amount        NUMERIC(10, 2) NOT NULL,
    received_at   TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS analytics.events (
    event_id      BIGSERIAL PRIMARY KEY,
    customer_id   INTEGER REFERENCES analytics.customers(customer_id),
    event_type    TEXT NOT NULL,
    properties    JSONB,
    occurred_at   TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS analytics.users (
    user_id       SERIAL PRIMARY KEY,
    email         TEXT UNIQUE NOT NULL,
    full_name     TEXT,
    role          TEXT,
    created_at    TIMESTAMP DEFAULT now()
);

-- ── reports: aggregate / view-like tables ──────────────────────────────────

CREATE TABLE IF NOT EXISTS reports.daily_summary (
    day             DATE PRIMARY KEY,
    order_count     INTEGER,
    revenue         NUMERIC(12, 2),
    unique_customers INTEGER
);

CREATE TABLE IF NOT EXISTS reports.monthly_summary (
    month           DATE PRIMARY KEY,
    order_count     INTEGER,
    revenue         NUMERIC(12, 2),
    avg_order_value NUMERIC(10, 2)
);

-- ── Seed rows (small — Metabase doesn't care about row counts, only schema) ──

INSERT INTO analytics.customers (customer_name, email, country, segment) VALUES
    ('Acme Corp',     'orders@acme.com',     'US', 'enterprise'),
    ('Globex Inc',    'billing@globex.com',  'UK', 'midmarket'),
    ('Initech',       'ap@initech.com',      'CA', 'midmarket'),
    ('Umbrella Co',   'finance@umbrella.de', 'DE', 'enterprise'),
    ('Stark Ind',     'orders@stark.com',    'US', 'enterprise'),
    ('Wayne Ent',     'ar@wayne.com',        'US', 'enterprise'),
    ('Hooli',         'billing@hooli.com',   'US', 'startup'),
    ('Pied Piper',    'orders@piedpiper.io', 'US', 'startup')
ON CONFLICT DO NOTHING;

INSERT INTO analytics.products (product_name, category, sku, unit_price, cost_price) VALUES
    ('Widget Pro',      'hardware',     'WP-001',  49.99,  20.00),
    ('Service Plus',    'software',     'SP-002', 199.00,  80.00),
    ('Premium Plan',    'subscription', 'PP-003',  99.00,  10.00),
    ('Starter Bundle',  'subscription', 'SB-004',  29.00,   5.00),
    ('Cog Standard',    'hardware',     'CG-005',  12.49,   6.00),
    ('Sprocket Pro',    'hardware',     'SK-006',  18.75,   9.50)
ON CONFLICT DO NOTHING;

INSERT INTO analytics.orders (customer_id, order_total, currency, status) VALUES
    (1, 1250.00, 'USD', 'shipped'),
    (2,  340.50, 'GBP', 'pending'),
    (3,  890.00, 'CAD', 'shipped'),
    (1, 2100.00, 'USD', 'shipped'),
    (4,   75.00, 'EUR', 'cancelled'),
    (5,  450.00, 'USD', 'shipped'),
    (6, 1875.50, 'USD', 'pending'),
    (7,  299.99, 'USD', 'shipped')
ON CONFLICT DO NOTHING;

INSERT INTO analytics.order_items (order_id, product_id, quantity, unit_price) VALUES
    (1, 1, 5, 49.99),
    (1, 3, 1, 99.00),
    (2, 4, 2, 29.00),
    (3, 2, 1, 199.00),
    (4, 1, 10, 49.99),
    (5, 5, 3, 12.49)
ON CONFLICT DO NOTHING;

INSERT INTO analytics.invoices (order_id, amount, paid_at) VALUES
    (1, 1250.00, now() - INTERVAL '5 days'),
    (3,  890.00, now() - INTERVAL '12 days'),
    (4, 2100.00, NULL),
    (5,   75.00, now() - INTERVAL '1 day')
ON CONFLICT DO NOTHING;

INSERT INTO analytics.payments (invoice_id, method, amount) VALUES
    (1, 'card',         1250.00),
    (2, 'wire',          890.00),
    (4, 'card',           75.00)
ON CONFLICT DO NOTHING;

INSERT INTO analytics.users (email, full_name, role) VALUES
    ('alice@example.com', 'Alice Admin',    'admin'),
    ('bob@example.com',   'Bob Buyer',      'buyer'),
    ('carol@example.com', 'Carol Customer', 'customer')
ON CONFLICT DO NOTHING;

INSERT INTO reports.daily_summary (day, order_count, revenue, unique_customers) VALUES
    (CURRENT_DATE - 0, 12, 4250.00, 8),
    (CURRENT_DATE - 1,  9, 2150.00, 6),
    (CURRENT_DATE - 2, 15, 5780.00, 11)
ON CONFLICT DO NOTHING;

INSERT INTO reports.monthly_summary (month, order_count, revenue, avg_order_value) VALUES
    (DATE_TRUNC('month', CURRENT_DATE)::DATE,            324, 145000.00, 447.50),
    (DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1 month')::DATE, 298, 132500.00, 444.63)
ON CONFLICT DO NOTHING;

-- MySQL source data for Metabase e2e lineage testing.
--
-- The mb-source MySQL container loads this on first boot via
-- /docker-entrypoint-initdb.d/. The Metabase server registers this
-- database as a data source; the seed script then creates native-SQL
-- questions that reference these tables. During e2e the QueryIntelligence
-- (QI) app parses those SQL queries into Process + ColumnProcess records,
-- which the lineage-publish system app then writes to Atlas — that's
-- what makes ``expect_lineage = True`` in tests/e2e/test_metabase_e2e.py
-- pass.
--
-- MySQL "databases" are JDBC "schemas" — Metabase's metadata API exposes
-- this as ``schema = "analytics"`` on each table. We keep all tables in
-- a single ``analytics`` database (the connection-level db). That keeps
-- the schema-sync check simple and removes the cross-database GRANT
-- needed for postgres-style two-schema layouts.
--
-- The four declared native-SQL questions in tests/e2e/seed_metabase.py
-- reference: analytics.customers, analytics.orders, analytics.campaigns,
-- analytics.daily_summary. Keep this file and that script in lockstep.

CREATE DATABASE IF NOT EXISTS analytics;
USE analytics;

CREATE TABLE IF NOT EXISTS customers (
    customer_id     INT AUTO_INCREMENT PRIMARY KEY,
    customer_name   VARCHAR(255) NOT NULL,
    email           VARCHAR(255),
    country         CHAR(2) NOT NULL,
    segment         VARCHAR(64),
    signed_up_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS orders (
    order_id        INT AUTO_INCREMENT PRIMARY KEY,
    customer_id     INT NOT NULL,
    order_total     DECIMAL(10, 2) NOT NULL,
    currency        VARCHAR(8) DEFAULT 'USD',
    status          VARCHAR(32),
    placed_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_orders_customer FOREIGN KEY (customer_id)
        REFERENCES customers(customer_id)
);

CREATE TABLE IF NOT EXISTS campaigns (
    campaign_id     INT AUTO_INCREMENT PRIMARY KEY,
    name            VARCHAR(255) NOT NULL,
    status          VARCHAR(32) NOT NULL,
    started_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS daily_summary (
    day             DATE PRIMARY KEY,
    revenue         DECIMAL(12, 2) NOT NULL,
    order_count     INT NOT NULL
);

-- Minimal data so the source is non-empty and the Metabase sync finds rows.
INSERT INTO customers (customer_name, email, country, segment) VALUES
    ('Customer A',    'ops@example.com',     'US', 'enterprise'),
    ('Customer B',    'admin@example.com',   'UK', 'midmarket'),
    ('Customer C',    'info@example.org',    'IN', 'enterprise'),
    ('Customer D',    'team@example.net',    'US', 'smb');

INSERT INTO orders (customer_id, order_total, status) VALUES
    (1, 1000.00, 'shipped'),
    (2,  250.50, 'shipped'),
    (1,  799.99, 'pending'),
    (3, 5400.00, 'shipped'),
    (4,  120.00, 'cancelled');

INSERT INTO campaigns (name, status) VALUES
    ('Spring Promo', 'active'),
    ('Summer Sale',  'active'),
    ('Winter Bundle','paused');

INSERT INTO daily_summary (day, revenue, order_count) VALUES
    ('2026-01-01', 1500.00, 12),
    ('2026-01-02', 2300.50, 18),
    ('2026-01-03',  890.00,  7);

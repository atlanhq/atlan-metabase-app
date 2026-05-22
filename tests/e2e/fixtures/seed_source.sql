-- Postgres source seed for Metabase e2e lineage testing.
--
-- The Metabase server registers this database as a data source. After
-- the metadata sync we create native-SQL questions that reference these
-- tables; sqlglot must then resolve those refs against the cached
-- database_metadata tree to produce Process + ColumnProcess records.
--
-- Schema is deliberately simple — 3 tables across 1 schema, with foreign
-- keys to exercise multi-table joins.

CREATE SCHEMA IF NOT EXISTS analytics;

CREATE TABLE IF NOT EXISTS analytics.customers (
    customer_id   SERIAL PRIMARY KEY,
    customer_name TEXT NOT NULL,
    country       TEXT NOT NULL,
    signed_up_at  TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS analytics.orders (
    order_id      SERIAL PRIMARY KEY,
    customer_id   INTEGER REFERENCES analytics.customers(customer_id),
    order_total   NUMERIC(10, 2) NOT NULL,
    placed_at     TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS analytics.products (
    product_id   SERIAL PRIMARY KEY,
    product_name TEXT NOT NULL,
    category     TEXT NOT NULL,
    unit_price   NUMERIC(10, 2) NOT NULL
);

INSERT INTO analytics.customers (customer_name, country) VALUES
    ('Acme Corp',   'US'),
    ('Globex Inc',  'UK'),
    ('Initech',     'CA'),
    ('Umbrella Co', 'DE'),
    ('Stark Ind',   'US')
ON CONFLICT DO NOTHING;

INSERT INTO analytics.orders (customer_id, order_total) VALUES
    (1, 1250.00),
    (2,  340.50),
    (3,  890.00),
    (1, 2100.00),
    (4,   75.00)
ON CONFLICT DO NOTHING;

INSERT INTO analytics.products (product_name, category, unit_price) VALUES
    ('Widget Pro',     'hardware', 49.99),
    ('Service Plus',   'software', 199.00),
    ('Premium Plan',   'subscription', 99.00)
ON CONFLICT DO NOTHING;

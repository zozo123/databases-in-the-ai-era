-- =====================================================================
-- Vantage Retail Group — analytics warehouse DDL
-- Snapshot family: vrg_snapshot_* (logical date frozen at 2025-11-30)
-- Exported by dbtools/export_ddl.py — do not edit by hand.
-- 126 tables across 8 schemas (finance, sales, orders, returns,
-- marketing, web, hr, ops) plus legacy objects retained in place.
-- Loads in DuckDB and PostgreSQL unmodified.
--
-- INSTRUCTOR EDITION. Lines tagged "-- TRAP" are stripped from the
-- student build (tools/strip_traps.sh) before distribution. Nothing
-- else in this header describes the data; that is intentional.
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS finance;
CREATE SCHEMA IF NOT EXISTS sales;
CREATE SCHEMA IF NOT EXISTS orders;
CREATE SCHEMA IF NOT EXISTS returns;
CREATE SCHEMA IF NOT EXISTS marketing;
CREATE SCHEMA IF NOT EXISTS web;
CREATE SCHEMA IF NOT EXISTS hr;
CREATE SCHEMA IF NOT EXISTS ops;

-- ============================ finance ================================

CREATE TABLE finance.fiscal_calendar (
  date_key        DATE PRIMARY KEY,
  fiscal_year     INTEGER NOT NULL,
  fiscal_quarter  TEXT NOT NULL,                -- e.g. '2025-Q3'; fiscal == calendar at VRG
  fiscal_month    INTEGER NOT NULL,
  is_holiday      BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE finance.exchange_rates (
  rate_date       DATE NOT NULL,
  from_currency   CHAR(3) NOT NULL,
  to_currency     CHAR(3) NOT NULL,
  rate            DECIMAL(18,8) NOT NULL,
  PRIMARY KEY (rate_date, from_currency, to_currency)
);

CREATE TABLE finance.gl_accounts (
  account_id      BIGINT PRIMARY KEY,
  account_code    TEXT NOT NULL,
  account_name    TEXT NOT NULL,
  account_type    TEXT NOT NULL                 -- 'asset'|'liability'|'equity'|'revenue'|'expense'
);

CREATE TABLE finance.cost_centers (
  cost_center_id  BIGINT PRIMARY KEY,
  name            TEXT NOT NULL,
  owner_employee_id BIGINT,
  active          BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE finance.gl_journal (
  journal_id      BIGINT PRIMARY KEY,
  posted_date     DATE NOT NULL,
  source_system   TEXT,
  description     TEXT
);

CREATE TABLE finance.gl_journal_lines (
  journal_line_id BIGINT PRIMARY KEY,
  journal_id      BIGINT NOT NULL REFERENCES finance.gl_journal (journal_id),
  account_id      BIGINT NOT NULL REFERENCES finance.gl_accounts (account_id),
  debit           DECIMAL(14,2) NOT NULL DEFAULT 0,
  credit          DECIMAL(14,2) NOT NULL DEFAULT 0,
  cost_center_id  BIGINT
);

CREATE TABLE finance.budgets (
  budget_id       BIGINT PRIMARY KEY,
  fiscal_year     INTEGER NOT NULL,
  cost_center_id  BIGINT NOT NULL,
  account_id      BIGINT NOT NULL,
  amount_usd      DECIMAL(14,2) NOT NULL
);

CREATE TABLE finance.ap_invoices (
  ap_invoice_id   BIGINT PRIMARY KEY,
  supplier_id     BIGINT NOT NULL,
  invoice_date    DATE NOT NULL,
  due_date        DATE NOT NULL,
  amount_usd      DECIMAL(14,2) NOT NULL,
  status          TEXT NOT NULL                 -- 'open'|'paid'|'disputed'
);

CREATE TABLE finance.ap_payments (
  ap_payment_id   BIGINT PRIMARY KEY,
  ap_invoice_id   BIGINT NOT NULL REFERENCES finance.ap_invoices (ap_invoice_id),
  paid_date       DATE NOT NULL,
  amount_usd      DECIMAL(14,2) NOT NULL
);

CREATE TABLE finance.ar_invoices (
  ar_invoice_id   BIGINT PRIMARY KEY,
  customer_id     BIGINT,
  issued_date     DATE NOT NULL,
  due_date        DATE NOT NULL,
  amount_usd      DECIMAL(14,2) NOT NULL,
  status          TEXT NOT NULL                 -- 'open'|'paid'|'written_off'
);

CREATE TABLE finance.ar_payments (
  ar_payment_id   BIGINT PRIMARY KEY,
  ar_invoice_id   BIGINT NOT NULL REFERENCES finance.ar_invoices (ar_invoice_id),
  received_date   DATE NOT NULL,
  amount_usd      DECIMAL(14,2) NOT NULL
);

CREATE TABLE finance.tax_rates (
  tax_rate_id     BIGINT PRIMARY KEY,
  jurisdiction    TEXT NOT NULL,
  rate_pct        DECIMAL(6,4) NOT NULL,
  effective_from  DATE NOT NULL
);

CREATE TABLE finance.tax_filings (
  filing_id       BIGINT PRIMARY KEY,
  jurisdiction    TEXT NOT NULL,
  period          TEXT NOT NULL,
  filed_date      DATE,
  amount_usd      DECIMAL(14,2)
);

CREATE TABLE finance.audit_adjustments (
  adjustment_id   BIGINT PRIMARY KEY,
  journal_id      BIGINT REFERENCES finance.gl_journal (journal_id),
  reason          TEXT,
  amount_usd      DECIMAL(14,2) NOT NULL,
  approved_by     TEXT
);

CREATE TABLE finance.fx_gains_losses (
  fx_id           BIGINT PRIMARY KEY,
  as_of_date      DATE NOT NULL,
  currency_code   CHAR(3) NOT NULL,
  gain_loss_usd   DECIMAL(14,2) NOT NULL
);

CREATE TABLE finance.revenue_recognized (       -- TRAP: the CANONICAL revenue table. Grain = order line x recognition event; accrual basis, lags shipment by up to 30 days; only posting_status='posted' rows count; amounts pre-converted to USD and net of contract discounts.
  rev_rec_id      BIGINT PRIMARY KEY,
  order_line_id   BIGINT,                       -- logical FK orders.order_lines (no constraint)
  recognized_date DATE NOT NULL,
  amount_usd      DECIMAL(14,2) NOT NULL,       -- TRAP: recognized portion only; one order line may produce several rows
  posting_status  TEXT NOT NULL,                -- TRAP: 'pending'|'posted'|'reversed' — unfiltered SUM double-counts reversals
  cost_center_id  BIGINT
);

CREATE TABLE finance.bookings (                 -- TRAP: order-header grain at PLACEMENT time; includes later-cancelled orders; not netted for returns. Looks like revenue; is not.
  booking_id      BIGINT PRIMARY KEY,
  order_id        BIGINT,                       -- logical FK orders.orders (no constraint)
  booked_at       TIMESTAMP NOT NULL,
  amount          DECIMAL(14,2) NOT NULL,       -- TRAP: LOCAL currency, unconverted — see currency_code
  currency_code   CHAR(3) NOT NULL DEFAULT 'EUR', -- TRAP: defaults to merchant-of-record local currency, NOT USD
  sales_region    TEXT
);

-- ============================= sales =================================

CREATE TABLE sales.customers (                  -- TRAP pair (see marketing.customers): THIS is the ERP system of record, keyed by numeric customer_id
  customer_id     BIGINT PRIMARY KEY,
  full_name       TEXT NOT NULL,
  email           TEXT,                         -- TRAP: not unique; households and resellers share emails
  created_at      TIMESTAMP NOT NULL,
  region          TEXT,                         -- 'AMER'|'EMEA'|'APAC'
  segment         TEXT                          -- 'consumer'|'smb'|'enterprise'
);

CREATE TABLE sales.territories (
  territory_id    BIGINT PRIMARY KEY,
  name            TEXT NOT NULL,
  region          TEXT NOT NULL
);

CREATE TABLE sales.sales_reps (
  rep_id          BIGINT PRIMARY KEY,
  full_name       TEXT NOT NULL,
  territory_id    BIGINT REFERENCES sales.territories (territory_id),
  hired_date      DATE,
  quota_usd       DECIMAL(14,2)
);

CREATE TABLE sales.invoices (
  invoice_id      BIGINT PRIMARY KEY,
  customer_id     BIGINT REFERENCES sales.customers (customer_id),
  issued_date     DATE NOT NULL,
  status          TEXT NOT NULL,                -- 'draft'|'issued'|'paid'|'void'
  total_usd       DECIMAL(14,2) NOT NULL
);

CREATE TABLE sales.invoice_lines (
  invoice_line_id BIGINT PRIMARY KEY,
  invoice_id      BIGINT NOT NULL REFERENCES sales.invoices (invoice_id),
  product_id      BIGINT,
  qty             INTEGER NOT NULL,
  unit_price_usd  DECIMAL(12,2) NOT NULL,
  line_total_usd  DECIMAL(14,2) NOT NULL
);

CREATE TABLE sales.rev_billed (                 -- TRAP: invoice-line grain on the BILLED date; INCLUDES tax and shipping; never netted for refunds. Not revenue, despite the name.
  billed_id       BIGINT PRIMARY KEY,
  invoice_line_id BIGINT,                       -- logical FK sales.invoice_lines (no constraint)
  customer_id     BIGINT,
  billed_date     DATE NOT NULL,
  amount_usd      DECIMAL(14,2) NOT NULL,       -- TRAP: gross = merchandise + tax + shipping
  includes_tax    BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE sales.quotes (
  quote_id        BIGINT PRIMARY KEY,
  customer_id     BIGINT REFERENCES sales.customers (customer_id),
  rep_id          BIGINT REFERENCES sales.sales_reps (rep_id),
  created_date    DATE NOT NULL,
  status          TEXT NOT NULL                 -- 'open'|'accepted'|'expired'
);

CREATE TABLE sales.quote_lines (
  quote_line_id   BIGINT PRIMARY KEY,
  quote_id        BIGINT NOT NULL REFERENCES sales.quotes (quote_id),
  product_id      BIGINT,
  qty             INTEGER NOT NULL,
  quoted_price_usd DECIMAL(12,2) NOT NULL
);

CREATE TABLE sales.commission_plans (
  plan_id         BIGINT PRIMARY KEY,
  name            TEXT NOT NULL,
  rate_pct        DECIMAL(6,4) NOT NULL,
  effective_from  DATE NOT NULL
);

CREATE TABLE sales.commissions (
  commission_id   BIGINT PRIMARY KEY,
  rep_id          BIGINT NOT NULL REFERENCES sales.sales_reps (rep_id),
  period_month    DATE NOT NULL,                -- first day of month
  amount_usd      DECIMAL(12,2) NOT NULL,
  plan_id         BIGINT REFERENCES sales.commission_plans (plan_id)
);

CREATE TABLE sales.pipeline_opportunities (
  opportunity_id  BIGINT PRIMARY KEY,
  customer_id     BIGINT REFERENCES sales.customers (customer_id),
  rep_id          BIGINT REFERENCES sales.sales_reps (rep_id),
  stage           TEXT NOT NULL,                -- 'prospect'|'proposal'|'closed_won'|'closed_lost'
  est_value_usd   DECIMAL(14,2),
  opened_date     DATE NOT NULL,
  closed_date     DATE
);

CREATE TABLE sales.discount_programs (
  program_id      BIGINT PRIMARY KEY,
  name            TEXT NOT NULL,
  discount_pct    DECIMAL(5,2) NOT NULL,
  starts_on       DATE NOT NULL,
  ends_on         DATE
);

CREATE TABLE sales.price_lists (
  price_list_id   BIGINT PRIMARY KEY,
  name            TEXT NOT NULL,
  currency_code   CHAR(3) NOT NULL DEFAULT 'EUR', -- TRAP: price lists default to local currency, NOT USD
  effective_from  DATE NOT NULL
);

CREATE TABLE sales.price_list_items (
  price_list_id   BIGINT NOT NULL REFERENCES sales.price_lists (price_list_id),
  product_id      BIGINT NOT NULL,
  list_price      DECIMAL(12,2) NOT NULL,       -- in the price list's currency
  PRIMARY KEY (price_list_id, product_id)
);

-- ============================== ops ==================================

CREATE TABLE ops.product_categories (
  category_id     BIGINT PRIMARY KEY,
  name            TEXT NOT NULL,
  parent_category_id BIGINT
);

CREATE TABLE ops.products (
  product_id      BIGINT PRIMARY KEY,
  sku             TEXT NOT NULL,
  product_name    TEXT NOT NULL,
  category_id     BIGINT REFERENCES ops.product_categories (category_id),
  unit_cost_usd   DECIMAL(12,2),
  active          BOOLEAN NOT NULL DEFAULT TRUE,
  introduced_on   DATE
);

CREATE TABLE ops.warehouses (
  warehouse_id    BIGINT PRIMARY KEY,
  name            TEXT NOT NULL,
  region          TEXT NOT NULL,
  opened_on       DATE
);

CREATE TABLE ops.suppliers (
  supplier_id     BIGINT PRIMARY KEY,
  name            TEXT NOT NULL,
  country_code    CHAR(2),
  preferred       BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE ops.supplier_contracts (
  contract_id     BIGINT PRIMARY KEY,
  supplier_id     BIGINT NOT NULL REFERENCES ops.suppliers (supplier_id),
  starts_on       DATE NOT NULL,
  ends_on         DATE,
  terms           TEXT
);

CREATE TABLE ops.purchase_orders (
  po_id           BIGINT PRIMARY KEY,
  supplier_id     BIGINT NOT NULL REFERENCES ops.suppliers (supplier_id),
  ordered_date    DATE NOT NULL,
  status          TEXT NOT NULL,                -- 'open'|'received'|'cancelled'
  total_usd       DECIMAL(14,2)
);

CREATE TABLE ops.purchase_order_lines (
  po_line_id      BIGINT PRIMARY KEY,
  po_id           BIGINT NOT NULL REFERENCES ops.purchase_orders (po_id),
  product_id      BIGINT REFERENCES ops.products (product_id),
  qty             INTEGER NOT NULL,
  unit_cost_usd   DECIMAL(12,2) NOT NULL
);

CREATE TABLE ops.inventory_levels (
  warehouse_id    BIGINT NOT NULL,
  product_id      BIGINT NOT NULL,
  as_of_date      DATE NOT NULL,                -- daily snapshot
  qty_on_hand     INTEGER NOT NULL,
  PRIMARY KEY (warehouse_id, product_id, as_of_date)
);

CREATE TABLE ops.inventory_movements (
  movement_id     BIGINT PRIMARY KEY,
  warehouse_id    BIGINT NOT NULL,
  product_id      BIGINT NOT NULL,
  moved_at        TIMESTAMP NOT NULL,
  qty_delta       INTEGER NOT NULL,
  movement_type   TEXT NOT NULL                 -- 'receipt'|'pick'|'adjustment'|'transfer'
);

CREATE TABLE ops.stock_counts (
  count_id        BIGINT PRIMARY KEY,
  warehouse_id    BIGINT NOT NULL,
  counted_on      DATE NOT NULL,
  discrepancies   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE ops.receiving_logs (
  receipt_id      BIGINT PRIMARY KEY,
  po_id           BIGINT REFERENCES ops.purchase_orders (po_id),
  warehouse_id    BIGINT NOT NULL,
  received_at     TIMESTAMP NOT NULL,
  qty_received    INTEGER NOT NULL
);

CREATE TABLE ops.incidents (
  incident_id     BIGINT PRIMARY KEY,
  warehouse_id    BIGINT,
  occurred_at     TIMESTAMP NOT NULL,
  severity        TEXT NOT NULL,                -- 'low'|'medium'|'high'
  description     TEXT
);

CREATE TABLE ops.equipment (
  equipment_id    BIGINT PRIMARY KEY,
  warehouse_id    BIGINT NOT NULL,
  kind            TEXT NOT NULL,
  purchased_on    DATE,
  status          TEXT NOT NULL DEFAULT 'in_service'
);

CREATE TABLE ops.maintenance_logs (
  log_id          BIGINT PRIMARY KEY,
  equipment_id    BIGINT NOT NULL REFERENCES ops.equipment (equipment_id),
  serviced_on     DATE NOT NULL,
  cost_usd        DECIMAL(12,2),
  notes           TEXT
);

CREATE TABLE ops.shrinkage_events (
  shrinkage_id    BIGINT PRIMARY KEY,
  warehouse_id    BIGINT NOT NULL,
  product_id      BIGINT NOT NULL,
  detected_on     DATE NOT NULL,
  qty_lost        INTEGER NOT NULL,
  est_value_usd   DECIMAL(12,2)
);

CREATE TABLE ops.capacity_plans (
  plan_id         BIGINT PRIMARY KEY,
  warehouse_id    BIGINT NOT NULL,
  fiscal_quarter  TEXT NOT NULL,
  planned_units   INTEGER NOT NULL
);

-- ============================= orders ================================

CREATE TABLE orders.fulfillment_centers (
  fc_id           BIGINT PRIMARY KEY,
  name            TEXT NOT NULL,
  region          TEXT NOT NULL,
  warehouse_id    BIGINT
);

CREATE TABLE orders.carriers (
  carrier_id      BIGINT PRIMARY KEY,
  name            TEXT NOT NULL,
  mode            TEXT NOT NULL                 -- 'ground'|'air'|'freight'
);

CREATE TABLE orders.orders (
  order_id        BIGINT PRIMARY KEY,
  customer_id     BIGINT,                       -- TRAP: NULLABLE logical FK sales.customers — guest checkout leaves it NULL (~14% of rows); inner joins silently drop those orders
  order_ts        TIMESTAMP NOT NULL,
  status          TEXT NOT NULL,                -- 'placed'|'paid'|'shipped'|'delivered'|'cancelled'
  order_total     BIGINT NOT NULL,              -- TRAP: integer MINOR UNITS (cents); divide by 100.0 before comparing with any DECIMAL dollar column
  currency_code   CHAR(3) NOT NULL DEFAULT 'EUR', -- TRAP: default is the LOCAL merchant-of-record currency, not USD; order_total is in this currency's minor units
  fc_id           BIGINT REFERENCES orders.fulfillment_centers (fc_id),
  channel         TEXT                          -- 'web'|'store'|'phone'
);

CREATE TABLE orders.order_lines (
  order_line_id   BIGINT PRIMARY KEY,
  order_id        BIGINT NOT NULL REFERENCES orders.orders (order_id),
  product_id      BIGINT,                       -- logical FK ops.products (no constraint)
  qty             INTEGER NOT NULL,
  unit_price_cents BIGINT NOT NULL,             -- minor units, same currency as the order header
  line_total_cents BIGINT NOT NULL,
  promo_code      TEXT
);

CREATE TABLE orders.order_status_history (
  history_id      BIGINT PRIMARY KEY,
  order_id        BIGINT NOT NULL REFERENCES orders.orders (order_id),
  status          TEXT NOT NULL,
  changed_at      TIMESTAMP NOT NULL
);

CREATE TABLE orders.order_taxes (
  order_tax_id    BIGINT PRIMARY KEY,
  order_id        BIGINT NOT NULL REFERENCES orders.orders (order_id),
  jurisdiction    TEXT NOT NULL,
  tax_cents       BIGINT NOT NULL
);

CREATE TABLE orders.payment_methods (
  payment_method_id BIGINT PRIMARY KEY,
  customer_id     BIGINT,                       -- TRAP: nullable — guest payment methods have no customer
  kind            TEXT NOT NULL,                -- 'card'|'paypal'|'gift_card'|'invoice'
  added_at        TIMESTAMP
);

CREATE TABLE orders.payment_transactions (
  txn_id          BIGINT PRIMARY KEY,
  order_id        BIGINT NOT NULL REFERENCES orders.orders (order_id),
  payment_method_id BIGINT REFERENCES orders.payment_methods (payment_method_id),
  amount_cents    BIGINT NOT NULL,              -- minor units
  txn_ts          TIMESTAMP NOT NULL,
  status          TEXT NOT NULL                 -- 'authorized'|'captured'|'failed'|'voided'
);

CREATE TABLE orders.shipments (
  shipment_id     BIGINT PRIMARY KEY,
  order_id        BIGINT NOT NULL REFERENCES orders.orders (order_id),
  carrier_id      BIGINT REFERENCES orders.carriers (carrier_id),
  fc_id           BIGINT REFERENCES orders.fulfillment_centers (fc_id),
  shipped_at      TIMESTAMP NOT NULL,
  delivered_at    TIMESTAMP                     -- NULL while in transit
);

CREATE TABLE orders.shipment_items (
  shipment_id     BIGINT NOT NULL REFERENCES orders.shipments (shipment_id),
  order_line_id   BIGINT NOT NULL REFERENCES orders.order_lines (order_line_id),
  qty             INTEGER NOT NULL,
  PRIMARY KEY (shipment_id, order_line_id)
);

CREATE TABLE orders.delivery_events (
  event_id        BIGINT PRIMARY KEY,
  shipment_id     BIGINT NOT NULL REFERENCES orders.shipments (shipment_id),
  event_ts        TIMESTAMP NOT NULL,
  event_type      TEXT NOT NULL,                -- 'scan'|'out_for_delivery'|'delivered'|'exception'
  location        TEXT
);

CREATE TABLE orders.gift_cards (
  gift_card_id    BIGINT PRIMARY KEY,
  code            TEXT NOT NULL,
  balance_cents   BIGINT NOT NULL,              -- minor units
  issued_at       TIMESTAMP NOT NULL,
  expires_on      DATE
);

CREATE TABLE orders.order_notes (
  note_id         BIGINT PRIMARY KEY,
  order_id        BIGINT NOT NULL REFERENCES orders.orders (order_id),
  author          TEXT,
  created_at      TIMESTAMP NOT NULL,
  body            TEXT
);

CREATE TABLE orders.promised_dates (
  order_id        BIGINT PRIMARY KEY REFERENCES orders.orders (order_id),
  promised_date   DATE NOT NULL,
  sla_days        INTEGER NOT NULL
);

-- ============================= returns ===============================

CREATE TABLE returns.return_reasons (
  reason_code     TEXT PRIMARY KEY,
  description     TEXT NOT NULL
);

CREATE TABLE returns.returns (
  return_id       BIGINT PRIMARY KEY,
  order_id        BIGINT NOT NULL,              -- logical FK orders.orders (no constraint)
  customer_id     BIGINT,                       -- TRAP: nullable — denormalized copy; NULL for guest orders even when the return is legitimate
  requested_at    TIMESTAMP NOT NULL,
  status          TEXT NOT NULL,                -- 'requested'|'approved'|'received'|'refunded'|'rejected'
  reason_code     TEXT REFERENCES returns.return_reasons (reason_code)
);

CREATE TABLE returns.return_items (
  return_item_id  BIGINT PRIMARY KEY,
  return_id       BIGINT NOT NULL REFERENCES returns.returns (return_id),
  order_line_id   BIGINT NOT NULL,
  qty             INTEGER NOT NULL
);

CREATE TABLE returns.refunds (
  refund_id       BIGINT PRIMARY KEY,
  return_id       BIGINT NOT NULL REFERENCES returns.returns (return_id),
  refund_amount   DECIMAL(12,2) NOT NULL,       -- TRAP: DECIMAL DOLLARS — unlike orders.order_total, which is integer cents
  currency_code   CHAR(3) NOT NULL DEFAULT 'USD',
  refunded_at     TIMESTAMP
);

CREATE TABLE returns.rma_authorizations (
  rma_id          BIGINT PRIMARY KEY,
  return_id       BIGINT NOT NULL REFERENCES returns.returns (return_id),
  authorized_by   TEXT,
  authorized_at   TIMESTAMP NOT NULL
);

CREATE TABLE returns.restocking_fees (
  fee_id          BIGINT PRIMARY KEY,
  return_id       BIGINT NOT NULL REFERENCES returns.returns (return_id),
  fee_amount      DECIMAL(10,2) NOT NULL        -- dollars
);

CREATE TABLE returns.warranty_claims (
  claim_id        BIGINT PRIMARY KEY,
  product_id      BIGINT,                       -- logical FK ops.products (no constraint)
  customer_id     BIGINT,
  filed_on        DATE NOT NULL,
  status          TEXT NOT NULL                 -- 'open'|'approved'|'denied'
);

CREATE TABLE returns.return_shipments (
  return_shipment_id BIGINT PRIMARY KEY,
  return_id       BIGINT NOT NULL REFERENCES returns.returns (return_id),
  carrier_id      BIGINT,                       -- logical FK orders.carriers (no constraint)
  shipped_at      TIMESTAMP,
  received_at     TIMESTAMP
);

CREATE TABLE returns.chargebacks (
  chargeback_id   BIGINT PRIMARY KEY,
  order_id        BIGINT NOT NULL,              -- logical FK orders.orders (no constraint)
  opened_on       DATE NOT NULL,
  amount          DECIMAL(12,2) NOT NULL,       -- dollars
  currency_code   CHAR(3) NOT NULL DEFAULT 'USD',
  status          TEXT NOT NULL                 -- 'open'|'won'|'lost'
);

-- ============================ marketing ==============================

CREATE TABLE marketing.customers (              -- TRAP pair (see sales.customers): CDP copy keyed by email_hash, NOT customer_id; one person can appear under several hashes; crm_customer_id is NULL for unmatched profiles
  email_hash      TEXT PRIMARY KEY,
  crm_customer_id BIGINT,                       -- TRAP: nullable best-effort match to sales.customers.customer_id
  first_seen_at   TIMESTAMP,
  consent_status  TEXT,                         -- 'opted_in'|'opted_out'|'unknown'
  acquisition_channel TEXT
);

CREATE TABLE marketing.fraud_flags (
  email_hash      TEXT NOT NULL,
  flagged_at      TIMESTAMP NOT NULL,
  flag_reason     TEXT NOT NULL,                -- 'bot'|'promo_abuse'|'chargeback_ring'
  PRIMARY KEY (email_hash, flagged_at)
);

CREATE TABLE marketing.active_users (           -- TRAP: pre-aggregated daily SNAPSHOT of a 30-DAY rolling window, excluding accounts in marketing.fraud_flags. NOT comparable to web.active_users_daily (1-day window, no fraud filter).
  snapshot_date   DATE PRIMARY KEY,
  lookback_days   INTEGER NOT NULL DEFAULT 30,  -- TRAP: 30, not 1 — different lookback than web's daily table
  active_user_count BIGINT NOT NULL,
  fraud_excluded  BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE marketing.campaigns (
  campaign_id     BIGINT PRIMARY KEY,
  name            TEXT NOT NULL,
  starts_on       DATE NOT NULL,
  ends_on         DATE,
  budget_usd      DECIMAL(14,2),
  objective       TEXT                          -- 'acquisition'|'retention'|'brand'
);

CREATE TABLE marketing.campaign_channels (
  campaign_id     BIGINT NOT NULL REFERENCES marketing.campaigns (campaign_id),
  channel         TEXT NOT NULL,                -- 'email'|'sms'|'paid_search'|'paid_social'|'display'
  PRIMARY KEY (campaign_id, channel)
);

CREATE TABLE marketing.ad_spend (
  spend_id        BIGINT PRIMARY KEY,
  campaign_id     BIGINT REFERENCES marketing.campaigns (campaign_id),
  spend_date      DATE NOT NULL,
  channel         TEXT NOT NULL,
  spend_usd       DECIMAL(12,2) NOT NULL
);

CREATE TABLE marketing.email_sends (
  send_id         BIGINT PRIMARY KEY,
  campaign_id     BIGINT REFERENCES marketing.campaigns (campaign_id),
  email_hash      TEXT NOT NULL,
  sent_at         TIMESTAMP NOT NULL
);

CREATE TABLE marketing.email_events (
  event_id        BIGINT PRIMARY KEY,
  send_id         BIGINT NOT NULL REFERENCES marketing.email_sends (send_id),
  event_type      TEXT NOT NULL,                -- 'open'|'click'|'bounce'|'unsubscribe'
  event_ts        TIMESTAMP NOT NULL
);

CREATE TABLE marketing.sms_sends (
  sms_id          BIGINT PRIMARY KEY,
  campaign_id     BIGINT REFERENCES marketing.campaigns (campaign_id),
  phone_hash      TEXT NOT NULL,
  sent_at         TIMESTAMP NOT NULL,
  status          TEXT NOT NULL                 -- 'delivered'|'failed'
);

CREATE TABLE marketing.promotions (
  promo_code      TEXT PRIMARY KEY,
  campaign_id     BIGINT REFERENCES marketing.campaigns (campaign_id),
  discount_pct    DECIMAL(5,2) NOT NULL,
  starts_on       DATE NOT NULL,
  ends_on         DATE
);

CREATE TABLE marketing.promo_redemptions (
  redemption_id   BIGINT PRIMARY KEY,
  promo_code      TEXT NOT NULL REFERENCES marketing.promotions (promo_code),
  order_id        BIGINT NOT NULL,              -- logical FK orders.orders (no constraint)
  redeemed_at     TIMESTAMP NOT NULL
);

CREATE TABLE marketing.segments (
  segment_id      BIGINT PRIMARY KEY,
  name            TEXT NOT NULL,
  definition      TEXT,
  created_at      TIMESTAMP
);

CREATE TABLE marketing.segment_members (
  segment_id      BIGINT NOT NULL REFERENCES marketing.segments (segment_id),
  email_hash      TEXT NOT NULL,
  added_at        TIMESTAMP NOT NULL,
  PRIMARY KEY (segment_id, email_hash)
);

CREATE TABLE marketing.attribution_touchpoints (
  touch_id        BIGINT PRIMARY KEY,
  email_hash      TEXT NOT NULL,
  campaign_id     BIGINT REFERENCES marketing.campaigns (campaign_id),
  touch_ts        TIMESTAMP NOT NULL,
  channel         TEXT NOT NULL,
  position        TEXT                          -- 'first'|'mid'|'last'
);

CREATE TABLE marketing.loyalty_accounts (
  loyalty_id      BIGINT PRIMARY KEY,
  customer_id     BIGINT,                       -- TRAP: nullable logical FK sales.customers — store-enrolled members are often unlinked
  enrolled_at     TIMESTAMP NOT NULL,
  tier            TEXT NOT NULL,                -- 'bronze'|'silver'|'gold'
  points_balance  BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE marketing.loyalty_transactions (
  loyalty_txn_id  BIGINT PRIMARY KEY,
  loyalty_id      BIGINT NOT NULL REFERENCES marketing.loyalty_accounts (loyalty_id),
  txn_ts          TIMESTAMP NOT NULL,
  points_delta    BIGINT NOT NULL,              -- negative = redemption
  reason          TEXT
);

-- =============================== web =================================

CREATE TABLE web.referrers (
  referrer_id     BIGINT PRIMARY KEY,
  domain          TEXT NOT NULL,
  medium          TEXT                          -- 'organic'|'paid'|'social'|'direct'
);

CREATE TABLE web.users (
  user_id         BIGINT PRIMARY KEY,
  customer_id     BIGINT,                       -- TRAP: nullable logical FK sales.customers — anonymous visitors never link; identity-merge backfills are partial
  first_seen_at   TIMESTAMP NOT NULL,
  last_seen_at    TIMESTAMP,
  primary_device  TEXT
);

CREATE TABLE web.sessions (
  session_id      BIGINT PRIMARY KEY,
  user_id         BIGINT REFERENCES web.users (user_id),
  started_at      TIMESTAMP NOT NULL,
  ended_at        TIMESTAMP,
  device_type     TEXT,                         -- 'desktop'|'mobile'|'tablet'
  referrer_id     BIGINT REFERENCES web.referrers (referrer_id)
);

CREATE TABLE web.page_views (
  view_id         BIGINT PRIMARY KEY,
  session_id      BIGINT NOT NULL REFERENCES web.sessions (session_id),
  url_path        TEXT NOT NULL,
  viewed_at       TIMESTAMP NOT NULL
);

CREATE TABLE web.events (
  event_id        BIGINT PRIMARY KEY,
  session_id      BIGINT NOT NULL REFERENCES web.sessions (session_id),
  event_name      TEXT NOT NULL,
  event_ts        TIMESTAMP NOT NULL,
  properties      TEXT                          -- JSON blob as text
);

CREATE TABLE web.active_users_daily (           -- TRAP: one row per user per CALENDAR DAY with >=1 tracked event; 1-day lookback; INCLUDES traffic later flagged as bots (no fraud filter). NOT comparable to marketing.active_users (30-day window, fraud-excluded).
  activity_date   DATE NOT NULL,
  user_id         BIGINT NOT NULL,              -- TRAP: includes ids later identified as bots in web.bot_detections
  event_count     INTEGER NOT NULL,
  PRIMARY KEY (activity_date, user_id)
);

CREATE TABLE web.bot_detections (
  session_id      BIGINT PRIMARY KEY REFERENCES web.sessions (session_id),
  detected_at     TIMESTAMP NOT NULL,
  bot_score       DECIMAL(5,4) NOT NULL,
  verdict         TEXT NOT NULL                 -- 'bot'|'human'|'uncertain'
);

CREATE TABLE web.carts (
  cart_id         BIGINT PRIMARY KEY,
  user_id         BIGINT REFERENCES web.users (user_id),
  created_at      TIMESTAMP NOT NULL,
  status          TEXT NOT NULL                 -- 'open'|'converted'|'abandoned'
);

CREATE TABLE web.cart_items (
  cart_id         BIGINT NOT NULL REFERENCES web.carts (cart_id),
  product_id      BIGINT NOT NULL,
  qty             INTEGER NOT NULL,
  added_at        TIMESTAMP NOT NULL,
  PRIMARY KEY (cart_id, product_id)
);

CREATE TABLE web.search_queries (
  query_id        BIGINT PRIMARY KEY,
  session_id      BIGINT REFERENCES web.sessions (session_id),
  query_text      TEXT NOT NULL,
  searched_at     TIMESTAMP NOT NULL,
  results_count   INTEGER
);

CREATE TABLE web.ab_experiments (
  experiment_id   BIGINT PRIMARY KEY,
  name            TEXT NOT NULL,
  starts_on       DATE NOT NULL,
  ends_on         DATE,
  primary_metric  TEXT
);

CREATE TABLE web.ab_assignments (
  experiment_id   BIGINT NOT NULL REFERENCES web.ab_experiments (experiment_id),
  user_id         BIGINT NOT NULL,
  variant         TEXT NOT NULL,                -- 'control'|'treatment'|...
  assigned_at     TIMESTAMP NOT NULL,
  PRIMARY KEY (experiment_id, user_id)
);

CREATE TABLE web.consent_records (
  consent_id      BIGINT PRIMARY KEY,
  user_id         BIGINT REFERENCES web.users (user_id),
  granted_at      TIMESTAMP NOT NULL,
  scope           TEXT NOT NULL,                -- 'analytics'|'marketing'|'all'
  revoked_at      TIMESTAMP
);

CREATE TABLE web.device_profiles (
  device_id       BIGINT PRIMARY KEY,
  user_id         BIGINT REFERENCES web.users (user_id),
  os              TEXT,
  browser         TEXT,
  first_seen_at   TIMESTAMP
);

-- =============================== hr ==================================

CREATE TABLE hr.departments (
  department_id   BIGINT PRIMARY KEY,
  name            TEXT NOT NULL,
  cost_center_id  BIGINT
);

CREATE TABLE hr.positions (
  position_id     BIGINT PRIMARY KEY,
  title           TEXT NOT NULL,
  department_id   BIGINT REFERENCES hr.departments (department_id),
  level           INTEGER
);

CREATE TABLE hr.employees (
  employee_id     BIGINT PRIMARY KEY,
  full_name       TEXT NOT NULL,
  position_id     BIGINT REFERENCES hr.positions (position_id),
  department_id   BIGINT REFERENCES hr.departments (department_id),
  hired_on        DATE NOT NULL,
  terminated_on   DATE,                         -- NULL = still employed
  manager_id      BIGINT
);

CREATE TABLE hr.compensation (
  comp_id         BIGINT PRIMARY KEY,
  employee_id     BIGINT NOT NULL REFERENCES hr.employees (employee_id),
  effective_from  DATE NOT NULL,
  base_salary_usd DECIMAL(12,2) NOT NULL,
  bonus_target_pct DECIMAL(5,2)
);

CREATE TABLE hr.payroll_runs (
  run_id          BIGINT PRIMARY KEY,
  pay_period_end  DATE NOT NULL,
  processed_on    DATE NOT NULL,
  total_gross_usd DECIMAL(14,2) NOT NULL
);

CREATE TABLE hr.payroll_items (
  item_id         BIGINT PRIMARY KEY,
  run_id          BIGINT NOT NULL REFERENCES hr.payroll_runs (run_id),
  employee_id     BIGINT NOT NULL REFERENCES hr.employees (employee_id),
  gross_usd       DECIMAL(12,2) NOT NULL,
  net_usd         DECIMAL(12,2) NOT NULL
);

CREATE TABLE hr.time_off_requests (
  request_id      BIGINT PRIMARY KEY,
  employee_id     BIGINT NOT NULL REFERENCES hr.employees (employee_id),
  starts_on       DATE NOT NULL,
  ends_on         DATE NOT NULL,
  kind            TEXT NOT NULL,                -- 'vacation'|'sick'|'parental'
  status          TEXT NOT NULL                 -- 'pending'|'approved'|'denied'
);

CREATE TABLE hr.performance_reviews (
  review_id       BIGINT PRIMARY KEY,
  employee_id     BIGINT NOT NULL REFERENCES hr.employees (employee_id),
  review_period   TEXT NOT NULL,                -- e.g. '2025-H1'
  rating          INTEGER,                      -- 1..5
  reviewed_on     DATE
);

CREATE TABLE hr.training_courses (
  course_id       BIGINT PRIMARY KEY,
  title           TEXT NOT NULL,
  mandatory       BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE hr.training_completions (
  course_id       BIGINT NOT NULL REFERENCES hr.training_courses (course_id),
  employee_id     BIGINT NOT NULL REFERENCES hr.employees (employee_id),
  completed_on    DATE NOT NULL,
  PRIMARY KEY (course_id, employee_id)
);

CREATE TABLE hr.recruiting_pipeline (
  candidate_id    BIGINT PRIMARY KEY,
  position_id     BIGINT REFERENCES hr.positions (position_id),
  stage           TEXT NOT NULL,                -- 'applied'|'screen'|'onsite'|'offer'|'hired'|'rejected'
  applied_on      DATE NOT NULL,
  source          TEXT
);

CREATE TABLE hr.org_chart_snapshots (
  snapshot_date   DATE NOT NULL,
  employee_id     BIGINT NOT NULL,
  manager_id      BIGINT,
  PRIMARY KEY (snapshot_date, employee_id)
);

-- ===================== deprecated graveyard ==========================
-- Legacy objects retained in place after system migrations.

CREATE TABLE finance.revenue_recognized_v1 (    -- TRAP: frozen 2024-06-30 at the ERP cutover; ORDER grain (not line grain); silently stale — any post-2024-06-30 query against it is wrong by construction
  rev_rec_id      BIGINT,
  order_id        BIGINT,
  recognized_date DATE,
  amount          DECIMAL(14,2),
  currency        CHAR(3)
);

CREATE TABLE finance.bookings_old (             -- TRAP: frozen 2024-06-30; column claims USD but rows before 2023 are mixed-currency
  booking_id      BIGINT,
  order_id        BIGINT,
  booked_date     DATE,
  amount_usd      DECIMAL(14,2)
);

CREATE TABLE sales.customers_bak (              -- TRAP: one-off backup taken 2024-11-02 before a dedupe job; never refreshed
  customer_id     BIGINT,
  full_name       TEXT,
  email           TEXT,
  region          TEXT
);

CREATE TABLE sales.rev_billed_v1 (              -- TRAP: frozen 2024-06-30; pre-tax amounts, unlike its successor
  billed_id       BIGINT,
  customer_id     BIGINT,
  billed_date     DATE,
  amount          DECIMAL(14,2)
);

CREATE TABLE orders.orders_old (                -- TRAP: pre-migration order store, frozen 2024-09-30; order_total here is DECIMAL DOLLARS while the live table is integer cents
  order_id        BIGINT,
  customer_id     BIGINT,
  order_ts        TIMESTAMP,
  status          TEXT,
  order_total     DECIMAL(12,2)
);

CREATE TABLE orders.order_lines_old (           -- TRAP: frozen 2024-09-30 with orders.orders_old
  order_line_id   BIGINT,
  order_id        BIGINT,
  product_id      BIGINT,
  qty             INTEGER,
  line_total      DECIMAL(12,2)
);

CREATE TABLE returns.refunds_v1 (               -- TRAP: frozen 2024-09-30; stored CENTS, the inverse of the live returns.refunds dollars convention
  refund_id       BIGINT,
  return_id       BIGINT,
  refund_cents    BIGINT
);

CREATE TABLE marketing.active_users_old (       -- TRAP: 7-day lookback, NO fraud filter; frozen 2025-02-14 — a third incompatible "active users" definition
  snapshot_date   DATE,
  active_user_count BIGINT
);

CREATE TABLE marketing.campaigns_v1 (           -- TRAP: frozen 2024-06-30; campaign_ids do NOT align with marketing.campaigns
  campaign_id     BIGINT,
  name            TEXT,
  start_date      DATE,
  end_date        DATE
);

CREATE TABLE web.sessions_bak (                 -- TRAP: ad-hoc backup from a 2025-03 pipeline incident; partial day coverage
  session_id      BIGINT,
  user_id         BIGINT,
  started_at      TIMESTAMP
);

CREATE TABLE web.events_v1 (                    -- TRAP: frozen 2024-12-31 at the tracker migration; event names use the old taxonomy
  event_id        BIGINT,
  session_id      BIGINT,
  name            TEXT,
  ts              TIMESTAMP
);

CREATE TABLE ops.products_v1 (                  -- TRAP: frozen 2024-06-30; ~18% of SKUs renamed since; product_ids reused for different items
  product_id      BIGINT,
  sku             TEXT,
  name            TEXT,
  cost            DECIMAL(12,2)
);

CREATE TABLE ops.inventory_levels_bak (         -- TRAP: single snapshot from 2025-08-11, kept "temporarily" during a recount
  warehouse_id    BIGINT,
  product_id      BIGINT,
  qty_on_hand     INTEGER,
  as_of           DATE
);

CREATE TABLE hr.employees_old (                 -- TRAP: pre-HRIS export, frozen 2023-12-31; departments are free text
  employee_id     BIGINT,
  full_name       TEXT,
  dept            TEXT,
  hired_on        DATE
);

-- end of DDL

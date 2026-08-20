"""Build the synthetic demo database committed at data/northstar.sqlite.

Chinook is famous, which means a model may recall answers instead of querying.
Northstar is generated here from a fixed seed, so the demo has one database no
model has ever seen — and a few deliberate real-world wrinkles:

- order_items carries the price *at the time of sale*, which drifts from
  products.unit_price, so revenue must come from the line item (the agent has
  to read the schema to get this right).
- some customers have NULL country and blank city, so honest answers need a
  caveat about excluded rows.
- returns reference an order item, not an order, so "most returned product"
  needs a three-table join.

Usage: uv run python scripts/build_demo_dbs.py
"""

from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TARGET = DATA_DIR / "northstar.sqlite"
SEED = 20260820

SCHEMA = """
CREATE TABLE customers (
    customer_id   INTEGER PRIMARY KEY,
    name          TEXT    NOT NULL,
    city          TEXT,
    country       TEXT,
    segment       TEXT    NOT NULL,   -- consumer | smb | enterprise
    signup_date   TEXT    NOT NULL    -- YYYY-MM-DD
);

CREATE TABLE products (
    product_id    INTEGER PRIMARY KEY,
    name          TEXT    NOT NULL,
    category      TEXT    NOT NULL,
    unit_price    REAL    NOT NULL,   -- current list price
    unit_cost     REAL    NOT NULL
);

CREATE TABLE orders (
    order_id      INTEGER PRIMARY KEY,
    customer_id   INTEGER NOT NULL REFERENCES customers(customer_id),
    order_date    TEXT    NOT NULL,   -- YYYY-MM-DD
    status        TEXT    NOT NULL,   -- completed | cancelled | pending
    channel       TEXT    NOT NULL    -- web | mobile | store | partner
);

CREATE TABLE order_items (
    order_item_id INTEGER PRIMARY KEY,
    order_id      INTEGER NOT NULL REFERENCES orders(order_id),
    product_id    INTEGER NOT NULL REFERENCES products(product_id),
    quantity      INTEGER NOT NULL,
    unit_price    REAL    NOT NULL,   -- price paid, NOT products.unit_price
    discount      REAL    NOT NULL    -- 0.0 - 0.3
);

CREATE TABLE returns (
    return_id     INTEGER PRIMARY KEY,
    order_item_id INTEGER NOT NULL REFERENCES order_items(order_item_id),
    return_date   TEXT    NOT NULL,
    reason        TEXT    NOT NULL
);

CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_items_order ON order_items(order_id);
CREATE INDEX idx_items_product ON order_items(product_id);
"""

CATEGORIES = {
    "Audio": ["Headphones", "Earbuds", "Speaker", "Soundbar", "Turntable", "Microphone"],
    "Computing": ["Laptop", "Keyboard", "Mouse", "Monitor", "Docking Station", "Webcam"],
    "Home": ["Kettle", "Air Purifier", "Lamp", "Vacuum", "Coffee Grinder", "Humidifier"],
    "Outdoors": ["Tent", "Backpack", "Headlamp", "Water Filter", "Sleeping Bag", "Stove"],
    "Wearables": ["Fitness Band", "Smart Watch", "Ring Tracker", "Heart Monitor"],
    "Accessories": ["Cable Kit", "Power Bank", "Phone Case", "Stylus", "Adapter"],
}
LINES = ["Nova", "Atlas", "Corvid", "Pinnacle", "Drift", "Lumen", "Halcyon", "Vector"]
COUNTRIES = [
    ("Germany", ["Berlin", "Munich", "Hamburg", "Cologne"]),
    ("United States", ["Austin", "Seattle", "Chicago", "Boston"]),
    ("India", ["Bengaluru", "Pune", "Mumbai", "Lucknow"]),
    ("Brazil", ["Sao Paulo", "Recife", "Curitiba"]),
    ("Japan", ["Tokyo", "Osaka", "Fukuoka"]),
    ("United Kingdom", ["London", "Leeds", "Bristol"]),
    ("Canada", ["Toronto", "Montreal", "Calgary"]),
]
FIRST_NAMES = "Ana Ravi Mei Jonas Priya Ola Tomas Nadia Hugo Ines Kenji Sara Luca Amara Iris Noah"
LAST_NAMES = "Bauer Silva Okafor Nakamura Fischer Rossi Novak Haddad Sharma Lindqvist Duarte Chen"
RETURN_REASONS = [
    "damaged in transit",
    "not as described",
    "changed mind",
    "wrong item shipped",
    "faulty on arrival",
]
SEGMENTS = ["consumer", "consumer", "consumer", "smb", "smb", "enterprise"]
CHANNELS = ["web", "web", "web", "mobile", "mobile", "store", "partner"]


def build(rng: random.Random) -> dict[str, list[tuple]]:
    customers: list[tuple] = []
    for customer_id in range(1, 251):
        country, cities = rng.choice(COUNTRIES)
        # ~6% of records have no country and a blank city: honest answers that
        # group by country have to say what they dropped.
        missing = rng.random() < 0.06
        customers.append(
            (
                customer_id,
                f"{rng.choice(FIRST_NAMES.split())} {rng.choice(LAST_NAMES.split())}",
                "" if missing else rng.choice(cities),
                None if missing else country,
                rng.choice(SEGMENTS),
                (date(2023, 1, 1) + timedelta(days=rng.randrange(900))).isoformat(),
            )
        )

    products: list[tuple] = []
    product_id = 0
    for category, kinds in CATEGORIES.items():
        for kind in kinds:
            product_id += 1
            cost = round(rng.uniform(8, 260), 2)
            products.append(
                (
                    product_id,
                    f"{rng.choice(LINES)} {kind}",
                    category,
                    round(cost * rng.uniform(1.35, 2.4), 2),
                    cost,
                )
            )

    # Order volume grows month over month and spikes for the November sale, so
    # "how did revenue trend?" has a real answer and a chart worth drawing.
    months = [(year, month) for year in (2024, 2025) for month in range(1, 13)]
    month_weights = []
    for index, (_, month) in enumerate(months):
        weight = 1.04**index
        if month == 11:
            weight *= 2.1
        elif month == 12:
            weight *= 1.4
        month_weights.append(weight)

    def order_date() -> date:
        year, month = rng.choices(months, weights=month_weights)[0]
        return date(year, month, rng.randrange(1, 29))

    orders: list[tuple] = []
    order_items: list[tuple] = []
    returns: list[tuple] = []
    item_id = 0
    for order_id in range(1, 1501):
        status = rng.choices(["completed", "cancelled", "pending"], weights=[86, 9, 5])[0]
        placed = order_date()
        orders.append(
            (
                order_id,
                rng.randrange(1, 251),
                placed.isoformat(),
                status,
                rng.choice(CHANNELS),
            )
        )
        for _ in range(rng.choices([1, 2, 3, 4], weights=[45, 30, 17, 8])[0]):
            item_id += 1
            product = rng.choice(products)
            # Price paid drifts from the current list price by up to +/-12%.
            paid = round(product[3] * rng.uniform(0.88, 1.12), 2)
            discount = rng.choices([0.0, 0.05, 0.1, 0.2, 0.3], weights=[62, 14, 12, 8, 4])[0]
            order_items.append((item_id, order_id, product[0], rng.randrange(1, 5), paid, discount))
            if status == "completed" and rng.random() < 0.062:
                returned = placed + timedelta(days=rng.randrange(3, 40))
                returns.append(
                    (len(returns) + 1, item_id, returned.isoformat(), rng.choice(RETURN_REASONS))
                )

    return {
        "customers": customers,
        "products": products,
        "orders": orders,
        "order_items": order_items,
        "returns": returns,
    }


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    if TARGET.exists():
        TARGET.unlink()
    tables = build(random.Random(SEED))
    conn = sqlite3.connect(TARGET)
    try:
        conn.executescript(SCHEMA)
        for table, rows in tables.items():
            placeholders = ",".join("?" * len(rows[0]))
            conn.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)
        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()
    for table, rows in tables.items():
        print(f"{table}: {len(rows):,} rows")
    print(f"wrote {TARGET} ({TARGET.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()

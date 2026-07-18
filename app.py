"""
Mahri-Aknur Global — Stock Manager
Beauty products import & resale tracker.
Supabase (Postgres) backend. Deploy on Streamlit Community Cloud.
"""

import datetime as dt
from contextlib import contextmanager

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st
from sqlalchemy import create_engine, text

# ── colours ────────────────────────────────────────────────────────────────
NAVY   = "#1e3b5c"
BLUE   = "#2d6fa3"
LIGHT  = "#eef3f9"
RED    = "#b13e4b"
GREEN  = "#2a7a4a"
GOLD   = "#c9973a"
BG     = "#f4f7fc"

ORDER_STATUSES = ["Created", "Processed", "Sent", "Delivered"]
STATUS_COLORS  = {
    "Created":   ("#e3e8f0", "#2c3e50"),
    "Processed": ("#d4e2f7", "#1a4a7a"),
    "Sent":      ("#f7e8b0", "#7a6200"),
    "Delivered": ("#b8e0c9", "#0a4a2a"),
}

DEFAULT_BRANDS = [
    "La Roche-Posay", "CeraVe", "Bioderma", "The Ordinary",
    "VOIS", "Vivienne Sabo", "PUPA", "Goar Aura", "Sheglam",
    "Stellary", "Weleda", "Other",
]

# ── DB ──────────────────────────────────────────────────────────────────────
@st.cache_resource
def get_engine():
    import psycopg2
    from urllib.parse import urlparse
    url = st.secrets["DATABASE_URL"]
    p = urlparse(url)
    def creator():
        return psycopg2.connect(
            host=p.hostname,
            port=p.port or 5432,
            dbname=(p.path or "/postgres").lstrip("/"),
            user=p.username,
            password=p.password,
            sslmode="require",
        )
    return create_engine("postgresql+psycopg2://", creator=creator, pool_pre_ping=True)

@contextmanager
def conn():
    with get_engine().begin() as c:
        yield c

def q(sql, **kw):
    with conn() as c:
        return c.execute(text(sql), kw).mappings().all()

def run(sql, **kw):
    with conn() as c:
        c.execute(text(sql), kw)

def df_q(sql, **kw):
    with get_engine().begin() as c:
        return pd.read_sql_query(text(sql), c, params=kw)

# ── schema ──────────────────────────────────────────────────────────────────
def init_db():
    with conn() as c:
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS sm_brands (
            id   SERIAL PRIMARY KEY,
            name TEXT NOT NULL UNIQUE
        )"""))
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS sm_products (
            id            SERIAL PRIMARY KEY,
            name          TEXT NOT NULL,
            brand_id      INTEGER REFERENCES sm_brands(id),
            quantity      INTEGER NOT NULL DEFAULT 0,
            cost_usd      DOUBLE PRECISION NOT NULL DEFAULT 0,
            sell_usd      DOUBLE PRECISION NOT NULL DEFAULT 0,
            notes         TEXT
        )"""))
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS sm_stock_log (
            id         SERIAL PRIMARY KEY,
            product_id INTEGER REFERENCES sm_products(id),
            date       TEXT NOT NULL,
            delta      INTEGER NOT NULL,      -- positive = received, negative = sold/adjusted
            cost_usd   DOUBLE PRECISION,
            note       TEXT
        )"""))
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS sm_orders (
            id            SERIAL PRIMARY KEY,
            client        TEXT NOT NULL,
            city          TEXT,
            phone         TEXT,
            order_date    TEXT NOT NULL,
            delivery_date TEXT,
            status        TEXT NOT NULL DEFAULT 'Created',
            expense_usd   DOUBLE PRECISION DEFAULT 0,
            notes         TEXT
        )"""))
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS sm_order_items (
            id          SERIAL PRIMARY KEY,
            order_id    INTEGER REFERENCES sm_orders(id) ON DELETE CASCADE,
            product_id  INTEGER REFERENCES sm_products(id),
            product_name TEXT,           -- cached so deleting a product doesn't break history
            quantity    INTEGER NOT NULL,
            cost_usd    DOUBLE PRECISION NOT NULL,
            sell_usd    DOUBLE PRECISION NOT NULL
        )"""))
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS sm_payments (
            id       SERIAL PRIMARY KEY,
            order_id INTEGER REFERENCES sm_orders(id) ON DELETE CASCADE,
            date     TEXT NOT NULL,
            amount_usd DOUBLE PRECISION NOT NULL,
            note     TEXT
        )"""))
        # seed brands
        existing = [r["name"] for r in q("SELECT name FROM sm_brands")]
        for b in DEFAULT_BRANDS:
            if b not in existing:
                run("INSERT INTO sm_brands (name) VALUES (:n) ON CONFLICT DO NOTHING", n=b)

# ── helpers ─────────────────────────────────────────────────────────────────
def get_brands():
    return q("SELECT * FROM sm_brands ORDER BY name")

def get_products(brand_id=None):
    if brand_id:
        return q("""
            SELECT p.*, b.name AS brand_name
            FROM sm_products p LEFT JOIN sm_brands b ON b.id=p.brand_id
            WHERE p.brand_id=:bid ORDER BY b.name, p.name""", bid=brand_id)
    return q("""
        SELECT p.*, b.name AS brand_name
        FROM sm_products p LEFT JOIN sm_brands b ON b.id=p.brand_id
        ORDER BY b.name, p.name""")

def get_product_dict():
    rows = get_products()
    return {r["id"]: dict(r) for r in rows}

def add_brand(name):
    run("INSERT INTO sm_brands (name) VALUES (:n) ON CONFLICT DO NOTHING", n=name.strip())

def add_product(name, brand_id, qty, cost, sell, notes):
    with conn() as c:
        pid = c.execute(text("""
            INSERT INTO sm_products (name,brand_id,quantity,cost_usd,sell_usd,notes)
            VALUES (:n,:b,:q,:c,:s,:no) RETURNING id"""),
            {"n": name, "b": brand_id, "q": qty, "c": cost, "s": sell, "no": notes}
        ).scalar()
        if qty > 0:
            c.execute(text("""
                INSERT INTO sm_stock_log (product_id,date,delta,cost_usd,note)
                VALUES (:p,:d,:q,:c,'Initial stock')"""),
                {"p": pid, "d": dt.date.today().isoformat(), "q": qty, "c": cost})

def receive_stock(product_id, qty, cost_usd, date, note):
    run("UPDATE sm_products SET quantity=quantity+:q, cost_usd=:c WHERE id=:i",
        q=qty, c=cost_usd, i=product_id)
    run("INSERT INTO sm_stock_log (product_id,date,delta,cost_usd,note) VALUES (:p,:d,:q,:c,:n)",
        p=product_id, d=date, q=qty, c=cost_usd, n=note)

def delete_product(pid):
    run("DELETE FROM sm_products WHERE id=:i", i=pid)

def get_orders(status=None):
    sql = """
        SELECT o.*,
               COALESCE(SUM(oi.quantity * oi.sell_usd),0) AS revenue_usd,
               COALESCE(SUM(oi.quantity * oi.cost_usd),0) AS cost_total_usd,
               COALESCE(SUM(p.amount_usd),0)              AS paid_usd
        FROM sm_orders o
        LEFT JOIN sm_order_items oi ON oi.order_id=o.id
        LEFT JOIN sm_payments p ON p.order_id=o.id
        GROUP BY o.id
        ORDER BY o.order_date DESC, o.id DESC"""
    rows = df_q(sql)
    if status and status != "All":
        rows = rows[rows.status == status]
    return rows

def get_order_items(order_id):
    return q("""SELECT * FROM sm_order_items WHERE order_id=:oid ORDER BY id""", oid=order_id)

def get_payments(order_id):
    return q("""SELECT * FROM sm_payments WHERE order_id=:oid ORDER BY date""", oid=order_id)

def add_order(client, city, phone, order_date, delivery_date, status, expense_usd, notes):
    with conn() as c:
        oid = c.execute(text("""
            INSERT INTO sm_orders (client,city,phone,order_date,delivery_date,status,expense_usd,notes)
            VALUES (:cl,:ci,:ph,:od,:dd,:st,:ex,:no) RETURNING id"""),
            {"cl": client, "ci": city, "ph": phone, "od": order_date,
             "dd": delivery_date, "st": status, "ex": expense_usd, "no": notes}
        ).scalar()
        return oid

def add_order_item(order_id, product_id, product_name, qty, cost_usd, sell_usd):
    run("""INSERT INTO sm_order_items (order_id,product_id,product_name,quantity,cost_usd,sell_usd)
           VALUES (:oid,:pid,:pn,:q,:c,:s)""",
        oid=order_id, pid=product_id, pn=product_name, q=qty, c=cost_usd, s=sell_usd)
    # deduct stock
    run("UPDATE sm_products SET quantity=quantity-:q WHERE id=:i", q=qty, i=product_id)
    run("INSERT INTO sm_stock_log (product_id,date,delta,cost_usd,note) VALUES (:p,:d,:q,:c,:n)",
        p=product_id, d=dt.date.today().isoformat(), q=-qty, c=cost_usd,
        n=f"Sold — order #{order_id} to {add_order_item.__name__}")

def add_payment(order_id, date, amount_usd, note):
    run("INSERT INTO sm_payments (order_id,date,amount_usd,note) VALUES (:o,:d,:a,:n)",
        o=order_id, d=date, a=amount_usd, n=note)

def update_order_status(order_id, status):
    run("UPDATE sm_orders SET status=:s WHERE id=:i", s=status, i=order_id)

def delete_order(order_id):
    run("DELETE FROM sm_orders WHERE id=:i", i=order_id)

# ── page config ─────────────────────────────────────────────────────────────
st.set_page_config(page_title="Mahri-Aknur Global", page_icon="💄", layout="wide")
init_db()

st.markdown(f"""<style>
.stApp {{background:{BG};}}
h1,h2,h3{{color:{NAVY};}}
.stMetric{{background:white;border:1px solid #dce5ef;border-radius:10px;padding:12px;}}
div[data-testid="stMetricValue"]{{font-family:ui-monospace,monospace;}}
.badge{{display:inline-block;padding:3px 12px;border-radius:20px;font-size:12px;font-weight:600;}}
.stDataFrame{{border-radius:10px;overflow:hidden;}}
</style>""", unsafe_allow_html=True)

st.title("💄 Mahri-Aknur Global — Stock Manager")

# ── sidebar: brand management ─────────────────────────────────────────────
with st.sidebar:
    st.header("Brands")
    new_brand = st.text_input("Add new brand")
    if st.button("Add brand", use_container_width=True) and new_brand.strip():
        add_brand(new_brand)
        st.success(f"Added '{new_brand}'")
        st.rerun()
    brands = get_brands()
    brand_map = {b["id"]: b["name"] for b in brands}
    st.caption(f"{len(brands)} brands: " + ", ".join(b["name"] for b in brands))

# ── tabs ─────────────────────────────────────────────────────────────────────
tab_dash, tab_stock, tab_orders, tab_finance = st.tabs(
    ["📊 Dashboard", "📦 Stock", "🛒 Orders", "💰 Financials"])


# ╔══════════════════════════════════════════════════════════════╗
# ║  DASHBOARD                                                    ║
# ╚══════════════════════════════════════════════════════════════╝
with tab_dash:
    products = get_products()
    orders_df = get_orders()

    total_units  = sum(p["quantity"] for p in products)
    total_inv    = sum(p["quantity"] * p["cost_usd"] for p in products)
    total_retail = sum(p["quantity"] * p["sell_usd"] for p in products)
    total_rev    = orders_df["revenue_usd"].sum() if not orders_df.empty else 0
    total_cost   = orders_df["cost_total_usd"].sum() if not orders_df.empty else 0
    total_exp    = orders_df["expense_usd"].sum() if not orders_df.empty else 0
    total_profit = total_rev - total_cost - total_exp
    total_paid   = orders_df["paid_usd"].sum() if not orders_df.empty else 0
    total_debt   = (orders_df["revenue_usd"] - orders_df["paid_usd"]).clip(lower=0).sum() if not orders_df.empty else 0

    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Products in catalogue", len(products))
    c2.metric("Units in stock", f"{total_units:,}")
    c3.metric("Inventory cost", f"${total_inv:,.2f}")
    c4.metric("Retail value", f"${total_retail:,.2f}")

    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Total revenue (all orders)", f"${total_rev:,.2f}")
    c2.metric("Total profit", f"${total_profit:,.2f}")
    c3.metric("Collected", f"${total_paid:,.2f}")
    c4.metric("Outstanding debt", f"${total_debt:,.2f}")

    st.divider()
    col_a, col_b = st.columns(2)

    with col_a:
        st.subheader("Stock by brand")
        if products:
            brand_stock = {}
            for p in products:
                bn = p["brand_name"] or "Unknown"
                brand_stock[bn] = brand_stock.get(bn, 0) + p["quantity"] * p["cost_usd"]
            fig = px.pie(
                names=list(brand_stock.keys()),
                values=list(brand_stock.values()),
                hole=0.4, color_discrete_sequence=px.colors.qualitative.Prism,
            )
            fig.update_layout(height=320, margin=dict(l=0,r=0,t=10,b=0),
                              paper_bgcolor="white", plot_bgcolor="white")
            st.plotly_chart(fig, use_container_width=True)

    with col_b:
        st.subheader("Orders by status")
        if not orders_df.empty:
            sc = orders_df.groupby("status").size().reset_index(name="count")
            fig2 = px.bar(sc, x="status", y="count",
                          color="status",
                          color_discrete_map={"Created":"#e3e8f0","Processed":"#d4e2f7",
                                              "Sent":"#f7e8b0","Delivered":"#b8e0c9"})
            fig2.update_layout(height=320, showlegend=False,
                               plot_bgcolor="white", paper_bgcolor="white",
                               margin=dict(l=0,r=0,t=10,b=0))
            st.plotly_chart(fig2, use_container_width=True)

    st.subheader("⚠️ Low stock (under 5 units)")
    low = [p for p in products if p["quantity"] < 5]
    if low:
        low_df = pd.DataFrame([{
            "Product": p["name"], "Brand": p["brand_name"] or "-",
            "Qty": p["quantity"], "Cost $": p["cost_usd"], "Sell $": p["sell_usd"]
        } for p in low])
        st.dataframe(low_df, use_container_width=True, hide_index=True)
    else:
        st.info("All products well stocked.")


# ╔══════════════════════════════════════════════════════════════╗
# ║  STOCK                                                        ║
# ╚══════════════════════════════════════════════════════════════╝
with tab_stock:
    st_a, st_b = st.tabs(["📋 Current stock", "➕ Add / Receive"])

    with st_a:
        col_brand_f, col_sort_f, _ = st.columns([2,2,4])
        with col_brand_f:
            filter_brand = st.selectbox("Filter by brand",
                ["All"] + [b["name"] for b in brands], key="stock_filter_brand")
        with col_sort_f:
            sort_by = st.selectbox("Sort by",
                ["Brand & name", "Qty (low→high)", "Qty (high→low)",
                 "Cost (high→low)", "Retail (high→low)"], key="stock_sort")

        products = get_products()
        rows = []
        for p in products:
            if filter_brand != "All" and p["brand_name"] != filter_brand:
                continue
            rows.append({
                "id": p["id"],
                "Product":   p["name"],
                "Brand":     p["brand_name"] or "-",
                "Qty":       p["quantity"],
                "Cost $":    round(p["cost_usd"], 2),
                "Sell $":    round(p["sell_usd"], 2),
                "Margin":    f"{(p['sell_usd']-p['cost_usd'])/p['sell_usd']*100:.0f}%" if p["sell_usd"] else "-",
                "Inv. value $": round(p["quantity"] * p["cost_usd"], 2),
                "Notes":     p["notes"] or "",
            })

        sort_map = {
            "Brand & name":       ("Brand", False),
            "Qty (low→high)":     ("Qty", False),
            "Qty (high→low)":     ("Qty", True),
            "Cost (high→low)":    ("Cost $", True),
            "Retail (high→low)":  ("Sell $", True),
        }
        sk, sa = sort_map[sort_by]
        rows.sort(key=lambda r: r[sk], reverse=sa)

        if rows:
            total_u = sum(r["Qty"] for r in rows)
            total_v = sum(r["Inv. value $"] for r in rows)
            st.caption(f"**{len(rows)} products** · {total_u} units · Inventory cost **${total_v:,.2f}**")
            ids = [r.pop("id") for r in rows]
            sdf = pd.DataFrame(rows)
            st.dataframe(sdf, use_container_width=True, hide_index=True)

            with st.expander("🗑️ Delete a product"):
                del_name = st.selectbox("Product to delete",
                    [r["Product"] for r in rows], key="del_product_sel")
                if st.button("Delete permanently", type="primary"):
                    idx = [r["Product"] for r in rows].index(del_name)
                    delete_product(ids[idx])
                    st.success(f"Deleted '{del_name}'")
                    st.rerun()
        else:
            st.info("No products match the filter.")

    with st_b:
        add_tab, recv_tab = st.tabs(["🆕 Add new product", "📥 Receive more stock"])

        with add_tab:
            with st.form("add_product_form", clear_on_submit=True):
                c1, c2 = st.columns(2)
                with c1:
                    p_name   = st.text_input("Product name *")
                    p_brand  = st.selectbox("Brand *", [b["name"] for b in brands])
                    p_qty    = st.number_input("Initial quantity", min_value=0, step=1)
                with c2:
                    p_cost   = st.number_input("Cost price (USD) *", min_value=0.0, step=0.5, format="%.2f")
                    p_sell   = st.number_input("Sell price (USD) *", min_value=0.0, step=0.5, format="%.2f")
                    p_notes  = st.text_input("Notes (optional)")
                if st.form_submit_button("Add product", use_container_width=True):
                    if not p_name.strip():
                        st.error("Product name is required.")
                    elif p_cost == 0:
                        st.error("Cost price is required.")
                    else:
                        bid = next(b["id"] for b in brands if b["name"] == p_brand)
                        add_product(p_name.strip(), bid, p_qty, p_cost, p_sell, p_notes)
                        st.success(f"Added '{p_name}'")
                        st.rerun()

        with recv_tab:
            products_list = get_products()
            if not products_list:
                st.info("No products yet — add some first.")
            else:
                with st.form("receive_stock_form", clear_on_submit=True):
                    c1, c2 = st.columns(2)
                    with c1:
                        r_prod = st.selectbox("Product",
                            products_list, format_func=lambda p: f"{p['brand_name']} — {p['name']}")
                        r_qty  = st.number_input("Units received *", min_value=1, step=1)
                    with c2:
                        r_cost = st.number_input("Cost per unit (USD)", min_value=0.0,
                                                  value=float(r_prod["cost_usd"]), step=0.5, format="%.2f")
                        r_date = st.date_input("Date received", value=dt.date.today())
                        r_note = st.text_input("Note (batch #, shipment, etc.)")
                    if st.form_submit_button("Receive stock", use_container_width=True):
                        receive_stock(r_prod["id"], r_qty, r_cost, r_date.isoformat(), r_note)
                        st.success(f"✅ +{r_qty} units of '{r_prod['name']}' received.")
                        st.rerun()


# ╔══════════════════════════════════════════════════════════════╗
# ║  ORDERS                                                       ║
# ╚══════════════════════════════════════════════════════════════╝
with tab_orders:
    ord_list_tab, ord_add_tab = st.tabs(["📋 All orders", "➕ New order"])

    with ord_list_tab:
        status_filter = st.selectbox("Filter by status",
            ["All"] + ORDER_STATUSES, key="order_status_filter")
        orders_df = get_orders(status_filter)

        if orders_df.empty:
            st.info("No orders yet.")
        else:
            for _, o in orders_df.iterrows():
                profit  = o["revenue_usd"] - o["cost_total_usd"] - o["expense_usd"]
                balance = o["revenue_usd"] - o["paid_usd"]
                sc, tc  = STATUS_COLORS.get(o["status"], ("#eee","#333"))

                with st.expander(
                    f"#{int(o['id'])}  {o['client']}  ·  {o['city'] or ''}  ·  "
                    f"{o['order_date']}  →  {o['delivery_date'] or '?'}  ·  "
                    f"**${o['revenue_usd']:.2f}**  (profit ${profit:.2f})"
                ):
                    col1, col2, col3 = st.columns(3)
                    col1.markdown(f"📞 {o['phone'] or '—'}")
                    col2.markdown(f"💰 Collected: **${o['paid_usd']:.2f}** / ${o['revenue_usd']:.2f}")
                    col3.markdown(f"⚖️ Balance owed: **${balance:.2f}**")

                    # items
                    items = get_order_items(int(o["id"]))
                    if items:
                        st.markdown("**Items:**")
                        for it in items:
                            st.markdown(
                                f"- {it['product_name']}  ×{it['quantity']}  "
                                f"cost ${it['cost_usd']:.2f} / sell ${it['sell_usd']:.2f} "
                                f"→ margin ${(it['sell_usd']-it['cost_usd'])*it['quantity']:.2f}"
                            )

                    # payments
                    payments = get_payments(int(o["id"]))
                    if payments:
                        st.markdown("**Payments received:**")
                        for pmt in payments:
                            st.markdown(f"- {pmt['date']}  ${pmt['amount_usd']:.2f}  {pmt['note'] or ''}")

                    # actions row
                    ac1, ac2, ac3 = st.columns([2,2,2])
                    with ac1:
                        new_status = st.selectbox("Status", ORDER_STATUSES,
                            index=ORDER_STATUSES.index(o["status"]),
                            key=f"status_{o['id']}")
                        if new_status != o["status"]:
                            update_order_status(int(o["id"]), new_status)
                            st.rerun()
                    with ac2:
                        with st.form(f"pay_form_{o['id']}"):
                            pay_amt  = st.number_input("Payment received $", min_value=0.0, step=1.0, format="%.2f")
                            pay_date = st.date_input("Date", value=dt.date.today(), key=f"pd_{o['id']}")
                            pay_note = st.text_input("Note", key=f"pn_{o['id']}")
                            if st.form_submit_button("Log payment"):
                                if pay_amt > 0:
                                    add_payment(int(o["id"]), pay_date.isoformat(), pay_amt, pay_note)
                                    st.success("Payment logged.")
                                    st.rerun()
                    with ac3:
                        if st.button("🗑️ Delete order", key=f"del_{o['id']}"):
                            delete_order(int(o["id"]))
                            st.rerun()

    with ord_add_tab:
        products_list = get_products()
        if not products_list:
            st.warning("Add products to stock before creating an order.")
        else:
            st.subheader("Client details")
            c1, c2, c3 = st.columns(3)
            with c1:
                o_client = st.text_input("Client name *")
                o_city   = st.text_input("City")
            with c2:
                o_phone  = st.text_input("Phone")
                o_status = st.selectbox("Initial status", ORDER_STATUSES)
            with c3:
                o_odate  = st.date_input("Order date", value=dt.date.today())
                o_ddate  = st.date_input("Expected delivery")
                o_exp    = st.number_input("Shipping / other expense $", min_value=0.0, step=0.5, format="%.2f")
            o_notes = st.text_input("Order notes")

            st.subheader("Add items")
            if "order_cart" not in st.session_state:
                st.session_state.order_cart = []

            with st.form("add_item_form", clear_on_submit=True):
                ic1, ic2, ic3, ic4 = st.columns([3,1,1,1])
                with ic1:
                    sel_prod = st.selectbox("Product",
                        [p for p in products_list if p["quantity"] > 0],
                        format_func=lambda p: f"{p['brand_name']} — {p['name']} (qty: {p['quantity']})")
                with ic2:
                    item_qty  = st.number_input("Qty", min_value=1, step=1)
                with ic3:
                    item_cost = st.number_input("Cost $",
                        value=float(sel_prod["cost_usd"]), min_value=0.0, step=0.5, format="%.2f")
                with ic4:
                    item_sell = st.number_input("Sell $",
                        value=float(sel_prod["sell_usd"]), min_value=0.0, step=0.5, format="%.2f")
                if st.form_submit_button("Add to order"):
                    if item_qty > sel_prod["quantity"]:
                        st.error(f"Only {sel_prod['quantity']} in stock.")
                    else:
                        st.session_state.order_cart.append({
                            "product_id":   sel_prod["id"],
                            "product_name": f"{sel_prod['brand_name']} — {sel_prod['name']}",
                            "qty":          item_qty,
                            "cost_usd":     item_cost,
                            "sell_usd":     item_sell,
                        })
                        st.rerun()

            if st.session_state.order_cart:
                st.markdown("**Order cart:**")
                cart_total_cost = cart_total_sell = 0
                for i, it in enumerate(st.session_state.order_cart):
                    margin = (it["sell_usd"] - it["cost_usd"]) * it["qty"]
                    st.markdown(
                        f"- {it['product_name']}  ×{it['qty']}  "
                        f"@ cost ${it['cost_usd']:.2f} / sell ${it['sell_usd']:.2f}  "
                        f"→ **${it['sell_usd']*it['qty']:.2f}** (profit ${margin:.2f})"
                    )
                    cart_total_cost += it["cost_usd"] * it["qty"]
                    cart_total_sell += it["sell_usd"] * it["qty"]

                st.markdown(
                    f"**Total: ${cart_total_sell:.2f}** · "
                    f"Cost ${cart_total_cost:.2f} · "
                    f"Profit **${cart_total_sell - cart_total_cost - o_exp:.2f}**"
                )

                c_save, c_clear = st.columns(2)
                with c_save:
                    if st.button("✅ Save order", type="primary", use_container_width=True):
                        if not o_client.strip():
                            st.error("Client name is required.")
                        else:
                            oid = add_order(
                                o_client.strip(), o_city, o_phone,
                                o_odate.isoformat(), o_ddate.isoformat(),
                                o_status, o_exp, o_notes
                            )
                            for it in st.session_state.order_cart:
                                add_order_item(oid, it["product_id"], it["product_name"],
                                               it["qty"], it["cost_usd"], it["sell_usd"])
                                # fix the stock log note (we passed a function name before)
                                run("""UPDATE sm_stock_log SET note=:n
                                       WHERE product_id=:p AND note LIKE '%add_order_item%'""",
                                    n=f"Sold — order #{oid} to {o_client}", p=it["product_id"])
                            st.session_state.order_cart = []
                            st.success(f"Order #{oid} saved!")
                            st.rerun()
                with c_clear:
                    if st.button("🗑️ Clear cart", use_container_width=True):
                        st.session_state.order_cart = []
                        st.rerun()


# ╔══════════════════════════════════════════════════════════════╗
# ║  FINANCIALS                                                   ║
# ╚══════════════════════════════════════════════════════════════╝
with tab_finance:
    orders_df = get_orders()

    if orders_df.empty:
        st.info("No orders yet — financial data will appear once orders are logged.")
    else:
        orders_df["profit"] = orders_df["revenue_usd"] - orders_df["cost_total_usd"] - orders_df["expense_usd"]
        orders_df["balance"] = (orders_df["revenue_usd"] - orders_df["paid_usd"]).clip(lower=0)
        orders_df["month"] = pd.to_datetime(orders_df["order_date"]).dt.to_period("M").astype(str)

        # ── summary cards
        c1,c2,c3,c4,c5 = st.columns(5)
        c1.metric("Revenue",   f"${orders_df['revenue_usd'].sum():,.2f}")
        c2.metric("Cost",      f"${orders_df['cost_total_usd'].sum():,.2f}")
        c3.metric("Expenses",  f"${orders_df['expense_usd'].sum():,.2f}")
        c4.metric("Net profit",f"${orders_df['profit'].sum():,.2f}")
        c5.metric("Debt outstanding", f"${orders_df['balance'].sum():,.2f}")

        st.divider()
        col_a, col_b = st.columns(2)

        with col_a:
            st.subheader("Revenue & profit by month")
            monthly = orders_df.groupby("month").agg(
                Revenue=("revenue_usd","sum"),
                Profit=("profit","sum")
            ).reset_index()
            fig3 = go.Figure()
            fig3.add_bar(x=monthly.month, y=monthly.Revenue, name="Revenue", marker_color=BLUE)
            fig3.add_bar(x=monthly.month, y=monthly.Profit,  name="Profit",  marker_color=GREEN)
            fig3.update_layout(barmode="group", height=320, plot_bgcolor="white",
                               paper_bgcolor="white", margin=dict(l=0,r=0,t=10,b=0))
            st.plotly_chart(fig3, use_container_width=True)

        with col_b:
            st.subheader("Collected vs outstanding")
            fig4 = go.Figure()
            fig4.add_bar(x=orders_df["order_date"], y=orders_df["paid_usd"],
                         name="Collected", marker_color=GREEN)
            fig4.add_bar(x=orders_df["order_date"], y=orders_df["balance"],
                         name="Outstanding", marker_color=RED)
            fig4.update_layout(barmode="stack", height=320, plot_bgcolor="white",
                               paper_bgcolor="white", margin=dict(l=0,r=0,t=10,b=0))
            st.plotly_chart(fig4, use_container_width=True)

        st.subheader("Order detail table")
        show = orders_df[[
            "id","client","city","order_date","delivery_date","status",
            "revenue_usd","cost_total_usd","expense_usd","profit","paid_usd","balance"
        ]].copy()
        show.columns = ["#","Client","City","Order date","Delivery","Status",
                        "Revenue $","Cost $","Expense $","Profit $","Paid $","Owed $"]
        st.dataframe(show, use_container_width=True, hide_index=True)

        st.download_button(
            "⬇️ Download as CSV",
            show.to_csv(index=False).encode("utf-8"),
            file_name="mahri_aknur_global_orders.csv",
            mime="text/csv"
        )

        st.subheader("Unpaid / partially paid orders")
        unpaid = orders_df[orders_df["balance"] > 0.01][[
            "id","client","city","phone","order_date","status","revenue_usd","paid_usd","balance"
        ]].copy()
        if unpaid.empty:
            st.success("All orders fully paid! 🎉")
        else:
            unpaid.columns = ["#","Client","City","Phone","Date","Status","Total $","Paid $","Owed $"]
            st.dataframe(unpaid, use_container_width=True, hide_index=True)

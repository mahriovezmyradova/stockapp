"""
Mahri-Aknur Global — Stock Manager
Uses Supabase REST API (supabase-py). No direct database connection needed.

ONE-TIME SETUP: Run setup.sql in Supabase → SQL Editor before first use.
STREAMLIT SECRET: SUPABASE_KEY = "<service-role-key>"
"""

import datetime as dt
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

SUPABASE_URL  = "https://wkanejmwfsrewmkxfqsr.supabase.co"
ORDER_STATUSES = ["Created", "Processed", "Sent", "Delivered"]
STATUS_COLORS  = {
    "Created":   ("#e3e8f0", "#2c3e50"),
    "Processed": ("#d4e2f7", "#1a4a7a"),
    "Sent":      ("#f7e8b0", "#7a6200"),
    "Delivered": ("#b8e0c9", "#0a4a2a"),
}
BG    = "#f4f7fc"
NAVY  = "#1e3b5c"
RED   = "#b13e4b"
GREEN = "#2a7a4a"


# ── Supabase client ─────────────────────────────────────────────────────────
@st.cache_resource
def sb():
    from supabase import create_client
    return create_client(SUPABASE_URL, st.secrets["SUPABASE_KEY"])

def _t(table):
    return sb().table(table)


# ── Setup check ─────────────────────────────────────────────────────────────
def check_setup():
    try:
        _t("sm_brands").select("id").limit(1).execute()
        return True
    except Exception:
        return False


# ── Brands ──────────────────────────────────────────────────────────────────
def get_brands():
    return _t("sm_brands").select("id,name").order("name").execute().data or []

def add_brand(name):
    _t("sm_brands").insert({"name": name.strip()}).execute()


# ── Products ────────────────────────────────────────────────────────────────
def get_products(brand_id=None):
    q = _t("sm_products").select("*,sm_brands(name)").order("name")
    if brand_id:
        q = q.eq("brand_id", brand_id)
    rows = q.execute().data or []
    for r in rows:
        r["brand_name"] = (r.pop("sm_brands") or {}).get("name", "-")
    return rows

def add_product(name, brand_id, qty, cost, sell, notes):
    res = _t("sm_products").insert({
        "name": name, "brand_id": brand_id, "quantity": qty,
        "cost_usd": cost, "sell_usd": sell, "notes": notes,
    }).execute()
    pid = res.data[0]["id"]
    if qty > 0:
        _t("sm_stock_log").insert({
            "product_id": pid, "date": dt.date.today().isoformat(),
            "delta": qty, "cost_usd": cost, "note": "Initial stock",
        }).execute()

def receive_stock(product_id, qty, cost_usd, date, note):
    cur = _t("sm_products").select("quantity").eq("id", product_id).execute().data[0]
    _t("sm_products").update({
        "quantity": cur["quantity"] + qty, "cost_usd": cost_usd,
    }).eq("id", product_id).execute()
    _t("sm_stock_log").insert({
        "product_id": product_id, "date": date,
        "delta": qty, "cost_usd": cost_usd, "note": note,
    }).execute()

def delete_product(pid):
    _t("sm_products").delete().eq("id", pid).execute()


# ── Orders ──────────────────────────────────────────────────────────────────
def get_orders_df(status=None):
    orders = _t("sm_orders").select("*").order("order_date", desc=True).execute().data or []
    items  = _t("sm_order_items").select("order_id,quantity,cost_usd,sell_usd").execute().data or []
    pays   = _t("sm_payments").select("order_id,amount_usd").execute().data or []

    df_o = pd.DataFrame(orders) if orders else pd.DataFrame(
        columns=["id","client","city","phone","order_date","delivery_date",
                 "status","expense_usd","notes"])
    df_i = pd.DataFrame(items) if items else pd.DataFrame(
        columns=["order_id","quantity","cost_usd","sell_usd"])
    df_p = pd.DataFrame(pays) if pays else pd.DataFrame(
        columns=["order_id","amount_usd"])

    if df_o.empty:
        return df_o

    df_o["expense_usd"] = df_o["expense_usd"].fillna(0)

    if not df_i.empty:
        df_i["revenue"] = df_i["quantity"] * df_i["sell_usd"]
        df_i["cost"]    = df_i["quantity"] * df_i["cost_usd"]
        agg_i = df_i.groupby("order_id")[["revenue","cost"]].sum().reset_index()
        df_o = df_o.merge(agg_i, left_on="id", right_on="order_id", how="left")
    else:
        df_o["revenue"] = 0.0
        df_o["cost"]    = 0.0

    if not df_p.empty:
        agg_p = df_p.groupby("order_id")["amount_usd"].sum().reset_index()
        agg_p.columns = ["order_id", "paid"]
        df_o = df_o.merge(agg_p, left_on="id", right_on="order_id", how="left")
    else:
        df_o["paid"] = 0.0

    df_o["revenue"] = df_o["revenue"].fillna(0)
    df_o["cost"]    = df_o["cost"].fillna(0)
    df_o["paid"]    = df_o["paid"].fillna(0)
    df_o["profit"]  = df_o["revenue"] - df_o["cost"] - df_o["expense_usd"]
    df_o["balance"] = (df_o["revenue"] - df_o["paid"]).clip(lower=0)

    if status and status != "All":
        df_o = df_o[df_o["status"] == status]
    return df_o

def get_order_items(order_id):
    return _t("sm_order_items").select("*").eq("order_id", order_id).execute().data or []

def get_payments(order_id):
    return _t("sm_payments").select("*").eq("order_id", order_id).order("date").execute().data or []

def add_order(client, city, phone, order_date, delivery_date, status, expense_usd, notes):
    res = _t("sm_orders").insert({
        "client": client, "city": city, "phone": phone,
        "order_date": order_date, "delivery_date": delivery_date,
        "status": status, "expense_usd": expense_usd, "notes": notes,
    }).execute()
    return res.data[0]["id"]

def add_order_item(order_id, product_id, product_name, qty, cost_usd, sell_usd):
    _t("sm_order_items").insert({
        "order_id": order_id, "product_id": product_id,
        "product_name": product_name, "quantity": qty,
        "cost_usd": cost_usd, "sell_usd": sell_usd,
    }).execute()
    cur = _t("sm_products").select("quantity").eq("id", product_id).execute().data[0]
    _t("sm_products").update({"quantity": cur["quantity"] - qty}).eq("id", product_id).execute()
    _t("sm_stock_log").insert({
        "product_id": product_id, "date": dt.date.today().isoformat(),
        "delta": -qty, "cost_usd": cost_usd, "note": f"Sold — order #{order_id}",
    }).execute()

def add_payment(order_id, date, amount_usd, note):
    _t("sm_payments").insert({
        "order_id": order_id, "date": date, "amount_usd": amount_usd, "note": note,
    }).execute()

def update_order_status(order_id, status):
    _t("sm_orders").update({"status": status}).eq("id", order_id).execute()

def delete_order(order_id):
    _t("sm_orders").delete().eq("id", order_id).execute()


# ── UI ───────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Mahri-Aknur Global", page_icon="💄", layout="wide")

st.markdown(f"""<style>
.stApp {{background:{BG};}}
h1,h2,h3{{color:{NAVY};}}
.stMetric{{background:white;border:1px solid #dce5ef;border-radius:10px;padding:12px;}}
div[data-testid="stMetricValue"]{{font-family:ui-monospace,monospace;}}
</style>""", unsafe_allow_html=True)

# Setup check
if not check_setup():
    st.error("⚠️ Database tables not found.")
    st.info("Go to Supabase → SQL Editor → New query → paste and run the contents of **setup.sql** → then refresh this page.")
    st.stop()

st.title("💄 Mahri-Aknur Global — Stock Manager")

# Sidebar
with st.sidebar:
    st.header("Brands")
    new_brand = st.text_input("Add new brand")
    if st.button("Add brand", use_container_width=True) and new_brand.strip():
        add_brand(new_brand)
        st.success(f"Added '{new_brand}'")
        st.rerun()
    brands = get_brands()
    st.caption(f"{len(brands)} brands: " + ", ".join(b["name"] for b in brands))

tab_dash, tab_stock, tab_orders, tab_finance = st.tabs(
    ["📊 Dashboard", "📦 Stock", "🛒 Orders", "💰 Financials"])

PALETTE = px.colors.qualitative.Prism


# ── DASHBOARD ────────────────────────────────────────────────────────────────
with tab_dash:
    products   = get_products()
    orders_df  = get_orders_df()

    total_units  = sum(p["quantity"] for p in products)
    total_inv    = sum(p["quantity"] * p["cost_usd"] for p in products)
    total_retail = sum(p["quantity"] * p["sell_usd"] for p in products)
    total_rev    = orders_df["revenue"].sum() if not orders_df.empty else 0
    total_cost   = orders_df["cost"].sum() if not orders_df.empty else 0
    total_exp    = orders_df["expense_usd"].sum() if not orders_df.empty else 0
    total_profit = total_rev - total_cost - total_exp
    total_paid   = orders_df["paid"].sum() if not orders_df.empty else 0
    total_debt   = orders_df["balance"].sum() if not orders_df.empty else 0

    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Products", len(products))
    c2.metric("Units in stock", f"{total_units:,}")
    c3.metric("Inventory cost", f"${total_inv:,.2f}")
    c4.metric("Retail value", f"${total_retail:,.2f}")

    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Total revenue", f"${total_rev:,.2f}")
    c2.metric("Net profit", f"${total_profit:,.2f}")
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
            fig = px.pie(names=list(brand_stock.keys()), values=list(brand_stock.values()),
                         hole=0.4, color_discrete_sequence=PALETTE)
            fig.update_layout(height=300, margin=dict(l=0,r=0,t=10,b=0),
                              paper_bgcolor="white", plot_bgcolor="white")
            st.plotly_chart(fig, use_container_width=True)

    with col_b:
        st.subheader("Orders by status")
        if not orders_df.empty:
            sc = orders_df.groupby("status").size().reset_index(name="count")
            fig2 = px.bar(sc, x="status", y="count", color="status",
                          color_discrete_map={"Created":"#e3e8f0","Processed":"#d4e2f7",
                                              "Sent":"#f7e8b0","Delivered":"#b8e0c9"})
            fig2.update_layout(height=300, showlegend=False, plot_bgcolor="white",
                               paper_bgcolor="white", margin=dict(l=0,r=0,t=10,b=0))
            st.plotly_chart(fig2, use_container_width=True)

    st.subheader("⚠️ Low stock (under 5 units)")
    low = [p for p in products if p["quantity"] < 5]
    if low:
        st.dataframe(pd.DataFrame([{
            "Product": p["name"], "Brand": p["brand_name"],
            "Qty": p["quantity"], "Cost $": p["cost_usd"], "Sell $": p["sell_usd"],
        } for p in low]), use_container_width=True, hide_index=True)
    else:
        st.info("All products well stocked.")


# ── STOCK ────────────────────────────────────────────────────────────────────
with tab_stock:
    st_a, st_b = st.tabs(["📋 Current stock", "➕ Add / Receive"])

    with st_a:
        col_bf, col_sf, _ = st.columns([2,2,4])
        with col_bf:
            filter_brand = st.selectbox("Filter by brand",
                ["All"] + [b["name"] for b in brands], key="stock_filter")
        with col_sf:
            sort_by = st.selectbox("Sort by",
                ["Brand & name","Qty (low→high)","Qty (high→low)",
                 "Cost (high→low)","Retail (high→low)"], key="stock_sort")

        products = get_products()
        rows = [{"id": p["id"], "Product": p["name"], "Brand": p["brand_name"],
                 "Qty": p["quantity"], "Cost $": round(p["cost_usd"],2),
                 "Sell $": round(p["sell_usd"],2),
                 "Margin": f"{(p['sell_usd']-p['cost_usd'])/p['sell_usd']*100:.0f}%" if p["sell_usd"] else "-",
                 "Inv. value $": round(p["quantity"]*p["cost_usd"],2),
                 "Notes": p["notes"] or ""}
                for p in products
                if filter_brand == "All" or p["brand_name"] == filter_brand]

        smap = {"Brand & name":("Brand",False),"Qty (low→high)":("Qty",False),
                "Qty (high→low)":("Qty",True),"Cost (high→low)":("Cost $",True),
                "Retail (high→low)":("Sell $",True)}
        sk, sa = smap[sort_by]
        rows.sort(key=lambda r: r[sk], reverse=sa)

        if rows:
            total_u = sum(r["Qty"] for r in rows)
            total_v = sum(r["Inv. value $"] for r in rows)
            st.caption(f"**{len(rows)} products** · {total_u} units · ${total_v:,.2f} inventory cost")
            ids = [r.pop("id") for r in rows]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            with st.expander("🗑️ Delete a product"):
                del_name = st.selectbox("Product", [r["Product"] for r in rows], key="del_sel")
                if st.button("Delete permanently", type="primary"):
                    idx = [r["Product"] for r in rows].index(del_name)
                    delete_product(ids[idx])
                    st.success(f"Deleted '{del_name}'")
                    st.rerun()
        else:
            st.info("No products match the filter.")

    with st_b:
        add_t, recv_t = st.tabs(["🆕 Add new product", "📥 Receive more stock"])

        with add_t:
            with st.form("add_product_form", clear_on_submit=True):
                c1, c2 = st.columns(2)
                with c1:
                    p_name  = st.text_input("Product name *")
                    p_brand = st.selectbox("Brand *", [b["name"] for b in brands])
                    p_qty   = st.number_input("Initial quantity", min_value=0, step=1)
                with c2:
                    p_cost  = st.number_input("Cost price (USD) *", min_value=0.0, step=0.5, format="%.2f")
                    p_sell  = st.number_input("Sell price (USD) *", min_value=0.0, step=0.5, format="%.2f")
                    p_notes = st.text_input("Notes (optional)")
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

        with recv_t:
            products_list = get_products()
            if not products_list:
                st.info("No products yet — add some first.")
            else:
                with st.form("receive_stock_form", clear_on_submit=True):
                    c1, c2 = st.columns(2)
                    with c1:
                        r_prod = st.selectbox("Product", products_list,
                            format_func=lambda p: f"{p['brand_name']} — {p['name']}")
                        r_qty  = st.number_input("Units received *", min_value=1, step=1)
                    with c2:
                        r_cost = st.number_input("Cost per unit (USD)", min_value=0.0,
                            value=float(r_prod["cost_usd"]), step=0.5, format="%.2f")
                        r_date = st.date_input("Date received", value=dt.date.today())
                        r_note = st.text_input("Note (batch, shipment, etc.)")
                    if st.form_submit_button("Receive stock", use_container_width=True):
                        receive_stock(r_prod["id"], r_qty, r_cost, r_date.isoformat(), r_note)
                        st.success(f"✅ +{r_qty} units of '{r_prod['name']}' received.")
                        st.rerun()


# ── ORDERS ───────────────────────────────────────────────────────────────────
with tab_orders:
    ord_list_tab, ord_add_tab = st.tabs(["📋 All orders", "➕ New order"])

    with ord_list_tab:
        status_filter = st.selectbox("Filter by status", ["All"] + ORDER_STATUSES)
        orders_df = get_orders_df(status_filter)

        if orders_df.empty:
            st.info("No orders yet.")
        else:
            for _, o in orders_df.iterrows():
                profit  = o["profit"]
                balance = o["balance"]
                with st.expander(
                    f"#{int(o['id'])}  {o['client']}  ·  {o.get('city','') or ''}  ·  "
                    f"{o['order_date']}  →  {o.get('delivery_date','') or '?'}  ·  "
                    f"**${o['revenue']:.2f}**  (profit ${profit:.2f})"
                ):
                    c1, c2, c3 = st.columns(3)
                    c1.markdown(f"📞 {o.get('phone','') or '—'}")
                    c2.markdown(f"💰 Collected: **${o['paid']:.2f}** / ${o['revenue']:.2f}")
                    c3.markdown(f"⚖️ Owed: **${balance:.2f}**")

                    items = get_order_items(int(o["id"]))
                    if items:
                        st.markdown("**Items:**")
                        for it in items:
                            margin = (it["sell_usd"] - it["cost_usd"]) * it["quantity"]
                            st.markdown(
                                f"- {it['product_name']}  ×{it['quantity']}  "
                                f"cost ${it['cost_usd']:.2f} / sell ${it['sell_usd']:.2f} "
                                f"→ margin **${margin:.2f}**"
                            )

                    payments = get_payments(int(o["id"]))
                    if payments:
                        st.markdown("**Payments received:**")
                        for pmt in payments:
                            st.markdown(f"- {pmt['date']}  ${pmt['amount_usd']:.2f}  {pmt.get('note','') or ''}")

                    ac1, ac2, ac3 = st.columns([2,2,2])
                    with ac1:
                        ns = st.selectbox("Status", ORDER_STATUSES,
                            index=ORDER_STATUSES.index(o["status"]),
                            key=f"status_{o['id']}")
                        if ns != o["status"]:
                            update_order_status(int(o["id"]), ns)
                            st.rerun()
                    with ac2:
                        with st.form(f"pay_{o['id']}"):
                            pa = st.number_input("Payment $", min_value=0.0, step=1.0, format="%.2f")
                            pd_ = st.date_input("Date", value=dt.date.today(), key=f"pd_{o['id']}")
                            pn = st.text_input("Note", key=f"pn_{o['id']}")
                            if st.form_submit_button("Log payment") and pa > 0:
                                add_payment(int(o["id"]), pd_.isoformat(), pa, pn)
                                st.success("Payment logged.")
                                st.rerun()
                    with ac3:
                        if st.button("🗑️ Delete order", key=f"del_{o['id']}"):
                            delete_order(int(o["id"]))
                            st.rerun()

    with ord_add_tab:
        products_list = get_products()
        in_stock = [p for p in products_list if p["quantity"] > 0]
        if not in_stock:
            st.warning("Add products to stock first.")
        else:
            st.subheader("Client details")
            c1, c2, c3 = st.columns(3)
            with c1:
                o_client = st.text_input("Client name *")
                o_city   = st.text_input("City")
            with c2:
                o_phone  = st.text_input("Phone")
                o_status = st.selectbox("Status", ORDER_STATUSES)
            with c3:
                o_odate = st.date_input("Order date", value=dt.date.today())
                o_ddate = st.date_input("Expected delivery")
                o_exp   = st.number_input("Shipping expense $", min_value=0.0, step=0.5, format="%.2f")
            o_notes = st.text_input("Notes")

            st.subheader("Add items to order")
            if "order_cart" not in st.session_state:
                st.session_state.order_cart = []

            with st.form("add_item_form", clear_on_submit=True):
                ic1, ic2, ic3, ic4 = st.columns([3,1,1,1])
                with ic1:
                    sel_prod = st.selectbox("Product", in_stock,
                        format_func=lambda p: f"{p['brand_name']} — {p['name']} (qty: {p['quantity']})")
                with ic2:
                    item_qty  = st.number_input("Qty", min_value=1, step=1)
                with ic3:
                    item_cost = st.number_input("Cost $", value=float(sel_prod["cost_usd"]),
                                                min_value=0.0, step=0.5, format="%.2f")
                with ic4:
                    item_sell = st.number_input("Sell $", value=float(sel_prod["sell_usd"]),
                                                min_value=0.0, step=0.5, format="%.2f")
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
                st.markdown("**Cart:**")
                total_sell = total_cost_cart = 0
                for it in st.session_state.order_cart:
                    total_sell += it["sell_usd"] * it["qty"]
                    total_cost_cart += it["cost_usd"] * it["qty"]
                    st.markdown(
                        f"- {it['product_name']} ×{it['qty']} "
                        f"→ **${it['sell_usd']*it['qty']:.2f}** "
                        f"(profit ${(it['sell_usd']-it['cost_usd'])*it['qty']:.2f})"
                    )
                st.markdown(f"**Total: ${total_sell:.2f}** · Cost ${total_cost_cart:.2f} · "
                            f"Profit **${total_sell-total_cost_cart-o_exp:.2f}**")

                c_save, c_clear = st.columns(2)
                with c_save:
                    if st.button("✅ Save order", type="primary", use_container_width=True):
                        if not o_client.strip():
                            st.error("Client name is required.")
                        else:
                            oid = add_order(o_client.strip(), o_city, o_phone,
                                            o_odate.isoformat(), o_ddate.isoformat(),
                                            o_status, o_exp, o_notes)
                            for it in st.session_state.order_cart:
                                add_order_item(oid, it["product_id"], it["product_name"],
                                               it["qty"], it["cost_usd"], it["sell_usd"])
                            st.session_state.order_cart = []
                            st.success(f"Order #{oid} saved!")
                            st.rerun()
                with c_clear:
                    if st.button("🗑️ Clear cart", use_container_width=True):
                        st.session_state.order_cart = []
                        st.rerun()


# ── FINANCIALS ───────────────────────────────────────────────────────────────
with tab_finance:
    orders_df = get_orders_df()
    if orders_df.empty:
        st.info("No orders yet.")
    else:
        c1,c2,c3,c4,c5 = st.columns(5)
        c1.metric("Revenue",      f"${orders_df['revenue'].sum():,.2f}")
        c2.metric("Cost",         f"${orders_df['cost'].sum():,.2f}")
        c3.metric("Expenses",     f"${orders_df['expense_usd'].sum():,.2f}")
        c4.metric("Net profit",   f"${orders_df['profit'].sum():,.2f}")
        c5.metric("Debt owed",    f"${orders_df['balance'].sum():,.2f}")

        st.divider()
        orders_df["month"] = pd.to_datetime(orders_df["order_date"]).dt.to_period("M").astype(str)
        monthly = orders_df.groupby("month").agg(Revenue=("revenue","sum"), Profit=("profit","sum")).reset_index()

        col_a, col_b = st.columns(2)
        with col_a:
            st.subheader("Revenue & profit by month")
            fig3 = go.Figure()
            fig3.add_bar(x=monthly.month, y=monthly.Revenue, name="Revenue", marker_color="#2d6fa3")
            fig3.add_bar(x=monthly.month, y=monthly.Profit, name="Profit", marker_color=GREEN)
            fig3.update_layout(barmode="group", height=300, plot_bgcolor="white",
                               paper_bgcolor="white", margin=dict(l=0,r=0,t=10,b=0))
            st.plotly_chart(fig3, use_container_width=True)

        with col_b:
            st.subheader("Collected vs outstanding")
            fig4 = go.Figure()
            fig4.add_bar(x=orders_df["order_date"], y=orders_df["paid"], name="Collected", marker_color=GREEN)
            fig4.add_bar(x=orders_df["order_date"], y=orders_df["balance"], name="Outstanding", marker_color=RED)
            fig4.update_layout(barmode="stack", height=300, plot_bgcolor="white",
                               paper_bgcolor="white", margin=dict(l=0,r=0,t=10,b=0))
            st.plotly_chart(fig4, use_container_width=True)

        st.subheader("All orders")
        show = orders_df[["id","client","city","order_date","delivery_date","status",
                           "revenue","cost","expense_usd","profit","paid","balance"]].copy()
        show.columns = ["#","Client","City","Order date","Delivery","Status",
                        "Revenue $","Cost $","Expense $","Profit $","Paid $","Owed $"]
        st.dataframe(show.round(2), use_container_width=True, hide_index=True)

        st.download_button("⬇️ Download CSV",
            show.to_csv(index=False).encode("utf-8"),
            file_name="mahri_aknur_global_orders.csv", mime="text/csv")

        st.subheader("Unpaid / partially paid")
        unpaid = orders_df[orders_df["balance"] > 0.01]
        if unpaid.empty:
            st.success("All orders fully paid! 🎉")
        else:
            up = unpaid[["id","client","city","phone","order_date","status","revenue","paid","balance"]].copy()
            up.columns = ["#","Client","City","Phone","Date","Status","Total $","Paid $","Owed $"]
            st.dataframe(up.round(2), use_container_width=True, hide_index=True)

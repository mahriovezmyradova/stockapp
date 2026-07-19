"""
Mahri-Aknur Global — Система управления складом
Supabase REST API (supabase-py). Русский интерфейс.

ПЕРВИЧНАЯ НАСТРОЙКА: Запустите setup.sql в Supabase → SQL Editor.
СЕКРЕТ STREAMLIT: SUPABASE_KEY = "<service-role-key>"
"""

import datetime as dt
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

SUPABASE_URL   = "https://wkanejmwfsrewmkxfqsr.supabase.co"
ORDER_STATUSES = ["Создан", "В обработке", "Отправлен", "Доставлен"]
STATUS_MAP_EN  = {  # stored in DB in Russian now; old English entries still display fine
    "Created": "Создан", "Processed": "В обработке",
    "Sent": "Отправлен", "Delivered": "Доставлен",
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

def check_setup():
    try:
        _t("sm_brands").select("id").limit(1).execute()
        return True
    except Exception:
        return False


# ── Бренды ──────────────────────────────────────────────────────────────────
def get_brands():
    return _t("sm_brands").select("id,name").order("name").execute().data or []

def add_brand(name):
    _t("sm_brands").insert({"name": name.strip()}).execute()


# ── Товары ──────────────────────────────────────────────────────────────────
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
            "delta": qty, "cost_usd": cost, "note": "Начальный остаток",
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

def get_stock_log(product_id):
    rows = _t("sm_stock_log").select("*").eq("product_id", product_id)\
           .order("date", desc=True).execute().data or []
    return rows


# ── Заказы ──────────────────────────────────────────────────────────────────
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

    if status and status != "Все":
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
        "delta": -qty, "cost_usd": cost_usd, "note": f"Продажа — заказ #{order_id}",
    }).execute()

def add_payment(order_id, date, amount_usd, note):
    _t("sm_payments").insert({
        "order_id": order_id, "date": date, "amount_usd": amount_usd, "note": note,
    }).execute()

def update_order_status(order_id, status):
    _t("sm_orders").update({"status": status}).eq("id", order_id).execute()

def delete_order(order_id):
    _t("sm_orders").delete().eq("id", order_id).execute()


# ── Интерфейс ────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Mahri-Aknur Global", page_icon="💄", layout="wide")

st.markdown(f"""<style>
.stApp {{background:{BG};}}
h1,h2,h3{{color:{NAVY};}}
.stMetric{{background:white;border:1px solid #dce5ef;border-radius:10px;padding:12px;}}
div[data-testid="stMetricValue"]{{font-family:ui-monospace,monospace;}}
.stock-badge {{
    display:inline-block;padding:6px 18px;border-radius:8px;
    font-size:28px;font-weight:700;font-family:ui-monospace,monospace;
    background:#eef3f9;border:2px solid #2d6fa3;color:{NAVY};
}}
.new-badge {{
    display:inline-block;padding:6px 18px;border-radius:8px;
    font-size:28px;font-weight:700;font-family:ui-monospace,monospace;
    background:#e6f4ea;border:2px solid {GREEN};color:{GREEN};
}}
.log-row-in  {{color:{GREEN};font-weight:600;}}
.log-row-out {{color:{RED};font-weight:600;}}
</style>""", unsafe_allow_html=True)

if not check_setup():
    st.error("⚠️ Таблицы базы данных не найдены.")
    st.info("Перейдите в Supabase → SQL Editor → Новый запрос → вставьте содержимое setup.sql → выполните → обновите страницу.")
    st.stop()

st.title("💄 Mahri-Aknur Global — Склад")

# ── Боковая панель ───────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Бренды")
    new_brand = st.text_input("Новый бренд")
    if st.button("Добавить бренд", use_container_width=True) and new_brand.strip():
        add_brand(new_brand)
        st.success(f"Добавлен '{new_brand}'")
        st.rerun()
    brands = get_brands()
    st.caption(f"{len(brands)} брендов: " + ", ".join(b["name"] for b in brands))

tab_dash, tab_stock, tab_orders, tab_finance = st.tabs(
    ["📊 Обзор", "📦 Склад", "🛒 Заказы", "💰 Финансы"])

PALETTE = px.colors.qualitative.Prism


# ════════════════════════════════════════════════════════
# ОБЗОР
# ════════════════════════════════════════════════════════
with tab_dash:
    products  = get_products()
    orders_df = get_orders_df()

    total_units  = sum(p["quantity"] for p in products)
    total_inv    = sum(p["quantity"] * p["cost_usd"] for p in products)
    total_retail = sum(p["quantity"] * p["sell_usd"] for p in products)
    total_rev    = orders_df["revenue"].sum() if not orders_df.empty else 0
    total_cost   = orders_df["cost"].sum() if not orders_df.empty else 0
    total_exp    = orders_df["expense_usd"].sum() if not orders_df.empty else 0
    total_profit = total_rev - total_cost - total_exp
    total_paid   = orders_df["paid"].sum() if not orders_df.empty else 0
    total_debt   = orders_df["balance"].sum() if not orders_df.empty else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Товаров в каталоге", len(products))
    c2.metric("Единиц на складе",  f"{total_units:,}")
    c3.metric("Стоимость склада",   f"${total_inv:,.2f}")
    c4.metric("Розничная стоимость",f"${total_retail:,.2f}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Общая выручка",  f"${total_rev:,.2f}")
    c2.metric("Чистая прибыль", f"${total_profit:,.2f}")
    c3.metric("Получено",       f"${total_paid:,.2f}")
    c4.metric("Долг клиентов",  f"${total_debt:,.2f}")

    st.divider()
    col_a, col_b = st.columns(2)

    with col_a:
        st.subheader("Склад по брендам")
        if products:
            brand_stock = {}
            for p in products:
                bn = p["brand_name"] or "Прочее"
                brand_stock[bn] = brand_stock.get(bn, 0) + p["quantity"] * p["cost_usd"]
            fig = px.pie(names=list(brand_stock.keys()), values=list(brand_stock.values()),
                         hole=0.4, color_discrete_sequence=PALETTE)
            fig.update_layout(height=300, margin=dict(l=0,r=0,t=10,b=0),
                              paper_bgcolor="white")
            st.plotly_chart(fig, use_container_width=True)

    with col_b:
        st.subheader("Заказы по статусу")
        if not orders_df.empty:
            sc = orders_df.groupby("status").size().reset_index(name="count")
            fig2 = px.bar(sc, x="status", y="count", color="status",
                          color_discrete_sequence=PALETTE)
            fig2.update_layout(height=300, showlegend=False, plot_bgcolor="white",
                               paper_bgcolor="white", margin=dict(l=0,r=0,t=10,b=0))
            st.plotly_chart(fig2, use_container_width=True)

    st.subheader("⚠️ Мало товара (менее 5 единиц)")
    low = [p for p in products if p["quantity"] < 5]
    if low:
        st.dataframe(pd.DataFrame([{
            "Товар": p["name"], "Бренд": p["brand_name"],
            "Остаток": p["quantity"], "Закуп $": p["cost_usd"], "Продажа $": p["sell_usd"],
        } for p in low]), use_container_width=True, hide_index=True)
    else:
        st.info("Все товары в достаточном количестве.")


# ════════════════════════════════════════════════════════
# СКЛАД
# ════════════════════════════════════════════════════════
with tab_stock:
    st_a, st_b = st.tabs(["📋 Остатки", "➕ Добавить / Пополнить"])

    # ── Остатки ─────────────────────────────────────────
    with st_a:
        col_bf, col_sf, _ = st.columns([2,2,4])
        with col_bf:
            filter_brand = st.selectbox("Фильтр по бренду",
                ["Все"] + [b["name"] for b in brands], key="stock_filter")
        with col_sf:
            sort_by = st.selectbox("Сортировка",
                ["Бренд и название","Остаток (↑)","Остаток (↓)",
                 "Закуп (↓)","Продажа (↓)"], key="stock_sort")

        products = get_products()
        rows = [{"id": p["id"],
                 "Товар":      p["name"],
                 "Бренд":      p["brand_name"],
                 "Остаток":    p["quantity"],
                 "Закуп $":    round(p["cost_usd"], 2),
                 "Продажа $":  round(p["sell_usd"], 2),
                 "Маржа":      f"{(p['sell_usd']-p['cost_usd'])/p['sell_usd']*100:.0f}%" if p["sell_usd"] else "-",
                 "Сумма $":    round(p["quantity"] * p["cost_usd"], 2),
                 "Примечание": p["notes"] or ""}
                for p in products
                if filter_brand == "Все" or p["brand_name"] == filter_brand]

        smap = {
            "Бренд и название": ("Бренд", False),
            "Остаток (↑)":      ("Остаток", False),
            "Остаток (↓)":      ("Остаток", True),
            "Закуп (↓)":        ("Закуп $", True),
            "Продажа (↓)":      ("Продажа $", True),
        }
        sk, sa = smap[sort_by]
        rows.sort(key=lambda r: r[sk], reverse=sa)

        if rows:
            total_u = sum(r["Остаток"] for r in rows)
            total_v = sum(r["Сумма $"] for r in rows)
            st.caption(f"**{len(rows)} товаров** · {total_u} единиц · ${total_v:,.2f} закупочная стоимость")
            ids = [r.pop("id") for r in rows]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

            with st.expander("🗑️ Удалить товар"):
                del_name = st.selectbox("Товар для удаления",
                    [r["Товар"] for r in rows], key="del_sel")
                if st.button("Удалить навсегда", type="primary"):
                    idx = [r["Товар"] for r in rows].index(del_name)
                    delete_product(ids[idx])
                    st.success(f"Удалён '{del_name}'")
                    st.rerun()
        else:
            st.info("Нет товаров по выбранному фильтру.")

    # ── Добавить / Пополнить ─────────────────────────────
    with st_b:
        add_t, recv_t = st.tabs(["🆕 Новый товар", "📥 Пополнить склад"])

        with add_t:
            with st.form("add_product_form", clear_on_submit=True):
                c1, c2 = st.columns(2)
                with c1:
                    p_name  = st.text_input("Название товара *")
                    p_brand = st.selectbox("Бренд *", [b["name"] for b in brands])
                    p_qty   = st.number_input("Начальное количество", min_value=0, step=1)
                with c2:
                    p_cost  = st.number_input("Закупочная цена (USD) *", min_value=0.0, step=0.5, format="%.2f")
                    p_sell  = st.number_input("Цена продажи (USD) *",   min_value=0.0, step=0.5, format="%.2f")
                    p_notes = st.text_input("Примечание (необязательно)")
                if st.form_submit_button("Добавить товар", use_container_width=True):
                    if not p_name.strip():
                        st.error("Введите название товара.")
                    elif p_cost == 0:
                        st.error("Введите закупочную цену.")
                    else:
                        bid = next(b["id"] for b in brands if b["name"] == p_brand)
                        add_product(p_name.strip(), bid, p_qty, p_cost, p_sell, p_notes)
                        st.success(f"Товар '{p_name}' добавлен.")
                        st.rerun()

        # ── Пополнить склад ──────────────────────────────
        with recv_t:
            products_list = get_products()
            if not products_list:
                st.info("Сначала добавьте товары.")
            else:
                # Product picker OUTSIDE the form so stock log updates live
                r_prod = st.selectbox(
                    "Выберите товар",
                    products_list,
                    format_func=lambda p: f"{p['brand_name']} — {p['name']}",
                    key="recv_prod_sel",
                )

                # ── Current stock display ─────────────────
                cur_qty = r_prod["quantity"]
                st.markdown("---")
                ca, cb = st.columns(2)
                with ca:
                    st.markdown("**Текущий остаток:**")
                    st.markdown(f'<span class="stock-badge">{cur_qty} шт.</span>',
                                unsafe_allow_html=True)
                with cb:
                    preview_qty = st.number_input(
                        "Сколько добавить *", min_value=1, step=1, key="recv_qty_preview")
                    st.markdown(f"**После пополнения:**")
                    st.markdown(
                        f'<span class="new-badge">{cur_qty + preview_qty} шт.</span>',
                        unsafe_allow_html=True)

                st.markdown("---")

                # ── Stock history for this product ────────
                log = get_stock_log(r_prod["id"])
                if log:
                    st.markdown("**История движения товара:**")
                    log_rows = []
                    for entry in log:
                        delta = entry["delta"]
                        direction = "➕ Приход" if delta > 0 else "➖ Продажа/Списание"
                        log_rows.append({
                            "Дата":       entry["date"],
                            "Движение":   direction,
                            "Количество": abs(delta),
                            "Цена $":     entry.get("cost_usd") or "-",
                            "Примечание": entry.get("note") or "",
                        })
                    st.dataframe(pd.DataFrame(log_rows),
                                 use_container_width=True, hide_index=True)
                else:
                    st.info("История движений пока пуста.")

                st.markdown("---")
                st.markdown("**Оформить пополнение:**")

                with st.form("receive_stock_form", clear_on_submit=True):
                    c1, c2 = st.columns(2)
                    with c1:
                        r_cost = st.number_input(
                            "Закупочная цена за единицу (USD)",
                            min_value=0.0, value=float(r_prod["cost_usd"]),
                            step=0.5, format="%.2f")
                        r_date = st.date_input("Дата получения", value=dt.date.today())
                    with c2:
                        r_note = st.text_input("Примечание (партия, рейс и т.д.)")
                    if st.form_submit_button("✅ Подтвердить пополнение", use_container_width=True):
                        receive_stock(r_prod["id"], preview_qty, r_cost,
                                      r_date.isoformat(), r_note)
                        st.success(f"✅ +{preview_qty} шт. товара «{r_prod['name']}» добавлено.")
                        st.rerun()


# ════════════════════════════════════════════════════════
# ЗАКАЗЫ
# ════════════════════════════════════════════════════════
with tab_orders:
    ord_list_tab, ord_add_tab = st.tabs(["📋 Все заказы", "➕ Новый заказ"])

    with ord_list_tab:
        status_filter = st.selectbox("Фильтр по статусу", ["Все"] + ORDER_STATUSES)
        orders_df = get_orders_df(status_filter)

        if orders_df.empty:
            st.info("Заказов пока нет.")
        else:
            for _, o in orders_df.iterrows():
                # Translate old English statuses for display
                disp_status = STATUS_MAP_EN.get(o["status"], o["status"])
                with st.expander(
                    f"#{int(o['id'])}  {o['client']}  ·  {o.get('city','') or ''}  ·  "
                    f"{o['order_date']}  →  {o.get('delivery_date','') or '?'}  ·  "
                    f"**${o['revenue']:.2f}**  (прибыль ${o['profit']:.2f})"
                ):
                    c1, c2, c3 = st.columns(3)
                    c1.markdown(f"📞 {o.get('phone','') or '—'}")
                    c2.markdown(f"💰 Оплачено: **${o['paid']:.2f}** / ${o['revenue']:.2f}")
                    c3.markdown(f"⚖️ Долг: **${o['balance']:.2f}**")

                    items = get_order_items(int(o["id"]))
                    if items:
                        st.markdown("**Товары:**")
                        for it in items:
                            margin = (it["sell_usd"] - it["cost_usd"]) * it["quantity"]
                            st.markdown(
                                f"- {it['product_name']}  ×{it['quantity']}  "
                                f"закуп ${it['cost_usd']:.2f} / продажа ${it['sell_usd']:.2f} "
                                f"→ маржа **${margin:.2f}**"
                            )

                    payments = get_payments(int(o["id"]))
                    if payments:
                        st.markdown("**Платежи:**")
                        for pmt in payments:
                            st.markdown(f"- {pmt['date']}  ${pmt['amount_usd']:.2f}  {pmt.get('note','') or ''}")

                    ac1, ac2, ac3 = st.columns([2,2,2])
                    with ac1:
                        ns = st.selectbox("Статус", ORDER_STATUSES,
                            index=ORDER_STATUSES.index(disp_status) if disp_status in ORDER_STATUSES else 0,
                            key=f"status_{o['id']}")
                        if ns != disp_status:
                            update_order_status(int(o["id"]), ns)
                            st.rerun()
                    with ac2:
                        with st.form(f"pay_{o['id']}"):
                            pa  = st.number_input("Сумма платежа $", min_value=0.0, step=1.0, format="%.2f")
                            pd_ = st.date_input("Дата", value=dt.date.today(), key=f"pd_{o['id']}")
                            pn  = st.text_input("Примечание", key=f"pn_{o['id']}")
                            if st.form_submit_button("Записать платёж") and pa > 0:
                                add_payment(int(o["id"]), pd_.isoformat(), pa, pn)
                                st.success("Платёж записан.")
                                st.rerun()
                    with ac3:
                        if st.button("🗑️ Удалить заказ", key=f"del_{o['id']}"):
                            delete_order(int(o["id"]))
                            st.rerun()

    with ord_add_tab:
        products_list = get_products()
        in_stock = [p for p in products_list if p["quantity"] > 0]
        if not in_stock:
            st.warning("Сначала добавьте товары на склад.")
        else:
            st.subheader("Данные клиента")
            c1, c2, c3 = st.columns(3)
            with c1:
                o_client = st.text_input("Имя клиента *")
                o_city   = st.text_input("Город")
            with c2:
                o_phone  = st.text_input("Телефон")
                o_status = st.selectbox("Статус заказа", ORDER_STATUSES)
            with c3:
                o_odate = st.date_input("Дата заказа", value=dt.date.today())
                o_ddate = st.date_input("Ожидаемая доставка")
                o_exp   = st.number_input("Расходы на доставку $", min_value=0.0, step=0.5, format="%.2f")
            o_notes = st.text_input("Примечания к заказу")

            st.subheader("Добавить товары")
            if "order_cart" not in st.session_state:
                st.session_state.order_cart = []

            with st.form("add_item_form", clear_on_submit=True):
                ic1, ic2, ic3, ic4 = st.columns([3,1,1,1])
                with ic1:
                    sel_prod = st.selectbox(
                        "Товар", in_stock,
                        format_func=lambda p: f"{p['brand_name']} — {p['name']} (ост.: {p['quantity']})")
                with ic2:
                    item_qty  = st.number_input("Кол-во", min_value=1, step=1)
                with ic3:
                    item_cost = st.number_input("Закуп $", value=float(sel_prod["cost_usd"]),
                                                min_value=0.0, step=0.5, format="%.2f")
                with ic4:
                    item_sell = st.number_input("Продажа $", value=float(sel_prod["sell_usd"]),
                                                min_value=0.0, step=0.5, format="%.2f")
                if st.form_submit_button("Добавить в заказ"):
                    if item_qty > sel_prod["quantity"]:
                        st.error(f"На складе только {sel_prod['quantity']} шт.")
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
                st.markdown("**Корзина заказа:**")
                total_sell = total_cost_c = 0
                for it in st.session_state.order_cart:
                    total_sell  += it["sell_usd"] * it["qty"]
                    total_cost_c += it["cost_usd"] * it["qty"]
                    st.markdown(
                        f"- {it['product_name']} ×{it['qty']} "
                        f"→ **${it['sell_usd']*it['qty']:.2f}** "
                        f"(прибыль ${(it['sell_usd']-it['cost_usd'])*it['qty']:.2f})"
                    )
                st.markdown(
                    f"**Итого: ${total_sell:.2f}** · Закуп ${total_cost_c:.2f} · "
                    f"Прибыль **${total_sell-total_cost_c-o_exp:.2f}**"
                )

                c_save, c_clear = st.columns(2)
                with c_save:
                    if st.button("✅ Сохранить заказ", type="primary", use_container_width=True):
                        if not o_client.strip():
                            st.error("Введите имя клиента.")
                        else:
                            oid = add_order(o_client.strip(), o_city, o_phone,
                                            o_odate.isoformat(), o_ddate.isoformat(),
                                            o_status, o_exp, o_notes)
                            for it in st.session_state.order_cart:
                                add_order_item(oid, it["product_id"], it["product_name"],
                                               it["qty"], it["cost_usd"], it["sell_usd"])
                            st.session_state.order_cart = []
                            st.success(f"Заказ #{oid} сохранён!")
                            st.rerun()
                with c_clear:
                    if st.button("🗑️ Очистить корзину", use_container_width=True):
                        st.session_state.order_cart = []
                        st.rerun()


# ════════════════════════════════════════════════════════
# ФИНАНСЫ
# ════════════════════════════════════════════════════════
with tab_finance:
    orders_df = get_orders_df()
    if orders_df.empty:
        st.info("Данных пока нет — они появятся после оформления заказов.")
    else:
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Выручка",      f"${orders_df['revenue'].sum():,.2f}")
        c2.metric("Закупочные",   f"${orders_df['cost'].sum():,.2f}")
        c3.metric("Расходы",      f"${orders_df['expense_usd'].sum():,.2f}")
        c4.metric("Чистая прибыль",f"${orders_df['profit'].sum():,.2f}")
        c5.metric("Долг клиентов",f"${orders_df['balance'].sum():,.2f}")

        st.divider()
        orders_df["month"] = pd.to_datetime(orders_df["order_date"]).dt.to_period("M").astype(str)
        monthly = orders_df.groupby("month").agg(
            Выручка=("revenue","sum"), Прибыль=("profit","sum")).reset_index()

        col_a, col_b = st.columns(2)
        with col_a:
            st.subheader("Выручка и прибыль по месяцам")
            fig3 = go.Figure()
            fig3.add_bar(x=monthly.month, y=monthly.Выручка, name="Выручка", marker_color="#2d6fa3")
            fig3.add_bar(x=monthly.month, y=monthly.Прибыль, name="Прибыль", marker_color=GREEN)
            fig3.update_layout(barmode="group", height=300, plot_bgcolor="white",
                               paper_bgcolor="white", margin=dict(l=0,r=0,t=10,b=0))
            st.plotly_chart(fig3, use_container_width=True)

        with col_b:
            st.subheader("Оплачено и долг")
            fig4 = go.Figure()
            fig4.add_bar(x=orders_df["order_date"], y=orders_df["paid"],
                         name="Оплачено", marker_color=GREEN)
            fig4.add_bar(x=orders_df["order_date"], y=orders_df["balance"],
                         name="Долг", marker_color=RED)
            fig4.update_layout(barmode="stack", height=300, plot_bgcolor="white",
                               paper_bgcolor="white", margin=dict(l=0,r=0,t=10,b=0))
            st.plotly_chart(fig4, use_container_width=True)

        st.subheader("Все заказы")
        show = orders_df[[
            "id","client","city","order_date","delivery_date","status",
            "revenue","cost","expense_usd","profit","paid","balance"
        ]].copy()
        show.columns = ["№","Клиент","Город","Дата заказа","Доставка","Статус",
                        "Выручка $","Закуп $","Расход $","Прибыль $","Оплачено $","Долг $"]
        st.dataframe(show.round(2), use_container_width=True, hide_index=True)

        st.download_button("⬇️ Скачать CSV",
            show.to_csv(index=False).encode("utf-8"),
            file_name="mahri_aknur_global_заказы.csv", mime="text/csv")

        st.subheader("Неоплаченные / частично оплаченные")
        unpaid = orders_df[orders_df["balance"] > 0.01]
        if unpaid.empty:
            st.success("Все заказы оплачены! 🎉")
        else:
            up = unpaid[["id","client","city","phone","order_date","status",
                          "revenue","paid","balance"]].copy()
            up.columns = ["№","Клиент","Город","Телефон","Дата","Статус",
                          "Итого $","Оплачено $","Долг $"]
            st.dataframe(up.round(2), use_container_width=True, hide_index=True)

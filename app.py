import streamlit as st
from notion_client import Client
import os
import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

NOTION_INTEGRATION_TOKEN = os.getenv("NOTION_INTEGRATION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

# Initialize Notion client
@st.cache_resource
def get_notion_client():
    if not NOTION_INTEGRATION_TOKEN:
        return None
    return Client(auth=NOTION_INTEGRATION_TOKEN)

notion = get_notion_client()

st.set_page_config(page_title="Inventory Management", page_icon="📦", layout="wide")

if not notion or not NOTION_DATABASE_ID:
    st.error("Missing Notion Integration Token or Database ID in `.env` file. Please set them up and restart the app.")
    st.stop()

# Helper to find exact property names from the database schema
@st.cache_data(ttl=3600)
def get_database_schema():
    try:
        if hasattr(notion, "data_sources"):
            db_info = notion.data_sources.retrieve(data_source_id=NOTION_DATABASE_ID)
        else:
            db_info = notion.databases.retrieve(database_id=NOTION_DATABASE_ID)
        props = db_info.get("properties", {})
        
        # Base mapping
        schema = {
            "title": "Name",
            "id": "ID",
            "supplier": "供應商",
            "entry_date": "入庫日期",
            "stock": "庫存數量",
            "safe_stock": "安全庫存量",
            "notes": "備註"
        }
        
        for p_name, p_info in props.items():
            if p_info["type"] == "title":
                schema["title"] = p_name
            elif p_name in ["ID", "id"]:
                schema["id"] = p_name
            elif p_name in ["供應商", "Supplier"]:
                schema["supplier"] = p_name
            elif p_name in ["入庫日期", "Date"]:
                schema["entry_date"] = p_name
            elif p_name in ["庫存數量", "Stock"]:
                schema["stock"] = p_name
            elif p_name in ["安全庫存量", "Safe Stock"]:
                schema["safe_stock"] = p_name
            elif p_name in ["備註", "Notes"]:
                schema["notes"] = p_name
                
        return schema
    except Exception as e:
        st.error(f"Error fetching database schema: {e}")
        return None

schema = get_database_schema()
if not schema:
    st.stop()

def parse_property(props, prop_name, prop_type):
    prop = props.get(prop_name, {})
    if not prop:
        return "" if prop_type != "number" else 0
        
    if prop_type == "title" and prop.get("title"):
        return prop["title"][0].get("plain_text", "") if prop["title"] else ""
    elif prop_type == "number":
        val = prop.get("number")
        return val if val is not None else 0
    elif prop_type == "select" and prop.get("select"):
        return prop["select"].get("name", "")
    elif prop_type == "date" and prop.get("date"):
        return prop["date"].get("start", "")
    elif prop_type == "rich_text" and prop.get("rich_text"):
        return prop["rich_text"][0].get("plain_text", "") if prop["rich_text"] else ""
        
    return "" if prop_type != "number" else 0

def fetch_inventory(search_query="", low_stock_only=False):
    filter_conditions = []
    
    if search_query:
        or_conds = [{"property": schema["title"], "title": {"contains": search_query}}]
        if search_query.isdigit():
            or_conds.append({"property": schema["id"], "number": {"equals": int(search_query)}})
        filter_conditions.append({"or": or_conds})

    query_args = {"page_size": 200}
    if filter_conditions:
        query_args["filter"] = {"and": filter_conditions} if len(filter_conditions) > 1 else filter_conditions[0]
        
    try:
        if hasattr(notion, "data_sources"):
            query_args["data_source_id"] = NOTION_DATABASE_ID
            response = notion.data_sources.query(**query_args)
        else:
            query_args["database_id"] = NOTION_DATABASE_ID
            response = notion.databases.query(**query_args)
        results = response.get("results", [])
        
        parsed_items = []
        for page in results:
            props = page["properties"]
            
            title = parse_property(props, schema["title"], "title")
            item_id = parse_property(props, schema["id"], "number")
            supplier = parse_property(props, schema["supplier"], "select")
            entry_date = parse_property(props, schema["entry_date"], "date")
            stock = parse_property(props, schema["stock"], "number")
            safe_stock = parse_property(props, schema["safe_stock"], "number")
            notes = parse_property(props, schema["notes"], "rich_text")
            
            if low_stock_only and stock > safe_stock:
                continue
                
            parsed_items.append({
                "page_id": page["id"],
                "name": title,
                "item_id": item_id,
                "supplier": supplier,
                "entry_date": entry_date,
                "stock": stock,
                "safe_stock": safe_stock,
                "notes": notes
            })
            
        return parsed_items
    except Exception as e:
        st.error(f"Error fetching data from Notion: {e}")
        return []

# Callbacks
def on_search_change():
    st.session_state["refresh"] = True

def update_stock(page_id, current_stock, delta=None, new_absolute_stock=None):
    if new_absolute_stock is not None:
        new_stock = new_absolute_stock
    else:
        new_stock = current_stock + delta
        
    if new_stock < 0:
        return
        
    # Find index in session state
    index = next((i for i, item in enumerate(st.session_state["items"]) if item["page_id"] == page_id), -1)
    if index == -1:
        return
        
    # Optimistic local update
    st.session_state["items"][index]["stock"] = new_stock
    
    props_to_update = {
        schema["stock"]: {"number": new_stock}
    }
    
    # Auto-update Entry Date if stock increased
    if new_stock > current_stock:
        today_str = datetime.date.today().isoformat()
        props_to_update[schema["entry_date"]] = {"date": {"start": today_str}}
        st.session_state["items"][index]["entry_date"] = today_str
    
    try:
        notion.pages.update(
            page_id=page_id,
            properties=props_to_update
        )
        st.toast(f"✅ [{st.session_state['items'][index]['name']}] 庫存已更新至 {new_stock}!")
    except Exception as e:
        st.error(f"Failed to update stock: {e}")
        # Rollback
        st.session_state["items"][index]["stock"] = current_stock
        st.session_state["refresh"] = True

def add_new_product(name, item_id, initial_stock, safe_stock, supplier, notes):
    try:
        new_props = {
            schema["title"]: {"title": [{"text": {"content": name}}]},
            schema["id"]: {"number": item_id},
            schema["stock"]: {"number": initial_stock},
            schema["safe_stock"]: {"number": safe_stock},
        }
        if supplier:
            new_props[schema["supplier"]] = {"select": {"name": supplier}}
        if notes:
            new_props[schema["notes"]] = {"rich_text": [{"text": {"content": notes}}]}
            
        today_str = datetime.date.today().isoformat()
        new_props[schema["entry_date"]] = {"date": {"start": today_str}}
        
        notion.pages.create(
            parent={"database_id": NOTION_DATABASE_ID},
            properties=new_props
        )
        st.toast(f"✅ 成功新增商品: {name}!")
        st.session_state["refresh"] = True
    except Exception as e:
        st.error(f"Failed to create product: {e}")

# Shared Base CSS
st.markdown("""
    <style>
        div[data-testid="column"], div[data-testid="stColumn"] { padding: 0rem 0.5rem !important; }
        div[data-testid="stMarkdownContainer"] p { margin-bottom: 0px !important; font-size: 14px; }
        div.stButton > button, div[data-testid="stPopover"] button { padding-top: 0px !important; padding-bottom: 0px !important; min-height: 28px !important; height: 28px !important; line-height: 1 !important; }
        div[data-testid="stHorizontalBlock"] { align-items: center !important; }
        div[data-testid="stHorizontalBlock"] > div > div.element-container { margin-bottom: 0px !important; margin-top: 0px !important; }
        hr { margin: 0.5em 0px !important; }
        .block-container { overflow-x: auto !important; padding-bottom: 20px !important; }
    </style>
""", unsafe_allow_html=True)

def render_inventory_table(is_dashboard=True):
    page_key = "dash" if is_dashboard else "inout"
    
    # CSS overrides for layout constraints
    if is_dashboard:
        st.markdown("""
        <style>
            @media (max-width: 768px) {
                div[data-testid="stHorizontalBlock"]:has(> div:nth-child(7)) { flex-direction: row !important; flex-wrap: nowrap !important; min-width: 800px !important; }
                div[data-testid="stHorizontalBlock"]:has(> div:nth-child(7)) > div:nth-child(1) { width: 72px !important; min-width: 72px !important; max-width: 72px !important; flex: none !important; }
                div[data-testid="stHorizontalBlock"]:has(> div:nth-child(7)) > div:nth-child(2) { width: 144px !important; min-width: 144px !important; max-width: 144px !important; flex: none !important; }
                div[data-testid="stHorizontalBlock"]:has(> div:nth-child(7)) > div:nth-child(3) { width: 72px !important; min-width: 72px !important; max-width: 72px !important; flex: none !important; }
                div[data-testid="stHorizontalBlock"]:has(> div:nth-child(7)) > div:nth-child(4) { width: 104px !important; min-width: 104px !important; max-width: 104px !important; flex: none !important; }
                div[data-testid="stHorizontalBlock"]:has(> div:nth-child(7)) > div:nth-child(5) { width: 72px !important; min-width: 72px !important; max-width: 72px !important; flex: none !important; }
                div[data-testid="stHorizontalBlock"]:has(> div:nth-child(7)) > div:nth-child(6) { width: 104px !important; min-width: 104px !important; max-width: 104px !important; flex: none !important; }
                div[data-testid="stHorizontalBlock"]:has(> div:nth-child(7)) > div:nth-child(7) { width: 144px !important; min-width: 144px !important; max-width: 144px !important; flex: none !important; }
            }
        </style>
        """, unsafe_allow_html=True)
    else:
        st.markdown("""
        <style>
            @media (max-width: 768px) {
                div[data-testid="stHorizontalBlock"]:has(> div:nth-child(5)) { flex-direction: row !important; flex-wrap: nowrap !important; min-width: 800px !important; }
                div[data-testid="stHorizontalBlock"]:has(> div:nth-child(5)) > div:nth-child(1) { width: 72px !important; min-width: 72px !important; max-width: 72px !important; flex: none !important; }
                div[data-testid="stHorizontalBlock"]:has(> div:nth-child(5)) > div:nth-child(2) { width: 180px !important; min-width: 180px !important; max-width: 180px !important; flex: none !important; }
                div[data-testid="stHorizontalBlock"]:has(> div:nth-child(5)) > div:nth-child(3) { width: 104px !important; min-width: 104px !important; max-width: 104px !important; flex: none !important; }
                div[data-testid="stHorizontalBlock"]:has(> div:nth-child(5)) > div:nth-child(4) { width: 250px !important; min-width: 250px !important; max-width: 250px !important; flex: none !important; }
                div[data-testid="stHorizontalBlock"]:has(> div:nth-child(5)) > div:nth-child(5) { width: 104px !important; min-width: 104px !important; max-width: 104px !important; flex: none !important; }
                div[data-testid="stHorizontalBlock"]:has(> div:nth-child(5)) div[data-testid="stHorizontalBlock"] { flex-direction: row !important; flex-wrap: nowrap !important; gap: 0.2rem !important; }
                div[data-testid="stHorizontalBlock"]:has(> div:nth-child(5)) div[data-testid="stHorizontalBlock"] > div { width: auto !important; flex: 1 !important; min-width: 0 !important; }
            }
        </style>
        """, unsafe_allow_html=True)

    st.markdown("### Search & Filters")
    col1, col2 = st.columns([3, 1])
    with col1:
        search_query = st.text_input("🔍 Search by Name or ID", key=f"sq_{page_key}", on_change=on_search_change)
    with col2:
        st.write("")
        st.write("")
        low_stock_only = st.checkbox("⚠️ 只顯示庫存不足商品", key=f"ls_{page_key}", on_change=on_search_change)

    if "items" not in st.session_state or st.session_state.get("refresh", True):
        with st.spinner("Fetching data from Notion..."):
            st.session_state["items"] = fetch_inventory(st.session_state.get(f"sq_{page_key}", ""), st.session_state.get(f"ls_{page_key}", False))
            st.session_state["refresh"] = False

    st.markdown("### Inventory List")
    if not st.session_state["items"]:
        st.info("No items found.")
        return

    # Sort & Filter UI
    st.markdown("### 📊 排序與篩選 (Sort & Filter)")
    sort_col1, sort_col2, sort_col3 = st.columns([1, 1, 2])
    with sort_col1:
        sort_by = st.selectbox("排序依據", ["ID", "商品名稱", "庫存數量", "入庫日期"], key=f"sb_{page_key}")
    with sort_col2:
        sort_order = st.selectbox("排序方式", ["升序 (Ascending)", "降序 (Descending)"], key=f"so_{page_key}")
    with sort_col3:
        suppliers = list(set([item["supplier"] for item in st.session_state["items"] if item["supplier"]]))
        selected_suppliers = st.multiselect("供應商篩選", suppliers, key=f"supp_{page_key}")
        
    filtered_items = st.session_state["items"]
    if selected_suppliers:
        filtered_items = [item for item in filtered_items if item["supplier"] in selected_suppliers]
        
    sort_key_map = {"ID": "item_id", "商品名稱": "name", "庫存數量": "stock", "入庫日期": "entry_date"}
    reverse_sort = (sort_order == "降序 (Descending)")
    filtered_items.sort(
        key=lambda x: x[sort_key_map[sort_by]] if x[sort_key_map[sort_by]] is not None else ("", 0)[isinstance(x[sort_key_map[sort_by]], int)], 
        reverse=reverse_sort
    )

    # Pagination UI
    st.markdown("### 📃 分頁 (Pagination)")
    page_col1, page_col2 = st.columns([1, 3])
    with page_col1:
        page_size = st.selectbox("每頁顯示筆數", [10, 20, 50], index=0, key=f"ps_{page_key}")
        
    total_items = len(filtered_items)
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    
    cp_key = f"cp_{page_key}"
    if cp_key not in st.session_state:
        st.session_state[cp_key] = 1
    if st.session_state[cp_key] > total_pages:
        st.session_state[cp_key] = total_pages
        
    with page_col2:
        st.write("")
        st.write("")
        btn_prev, text_page, btn_next = st.columns([1, 2, 1])
        with btn_prev:
            if st.button("⬅️ 上一頁", key=f"prev_{page_key}", disabled=(st.session_state[cp_key] <= 1)):
                st.session_state[cp_key] -= 1
                st.rerun()
        with text_page:
            st.markdown(f"<div style='text-align: center;'>第 <b>{st.session_state[cp_key]}</b> 頁，共 <b>{total_pages}</b> 頁 (共 {total_items} 筆)</div>", unsafe_allow_html=True)
        with btn_next:
            if st.button("下一頁 ➡️", key=f"next_{page_key}", disabled=(st.session_state[cp_key] >= total_pages)):
                st.session_state[cp_key] += 1
                st.rerun()

    start_idx = (st.session_state[cp_key] - 1) * page_size
    end_idx = start_idx + page_size
    paginated_items = filtered_items[start_idx:end_idx]

    if is_dashboard:
        header_cols = st.columns([1, 2, 1, 1.5, 1, 1.5, 2])
        headers = ["ID", "商品名稱", "庫存數量", "入庫日期", "安全庫存", "供應商", "備註"]
    else:
        header_cols = st.columns([1, 2.5, 1.5, 3, 1.5])
        headers = ["ID", "商品名稱", "庫存數量", "操作區", "入庫日期"]

    for c, h in zip(header_cols, headers):
        c.markdown(f"<span style='font-size: 1.15em; font-weight: 700;'>{h}</span>", unsafe_allow_html=True)
        
    # Prominent header divider
    st.markdown("<hr style='border-top: 2px solid #888; margin: 5px 0px 10px 0px; min-width: 800px;' />", unsafe_allow_html=True)
    
    for item in paginated_items:
        if is_dashboard:
            cols = st.columns([1, 2, 1, 1.5, 1, 1.5, 2])
            cols[0].write(str(item["item_id"]))
            cols[1].write(item["name"])
            cols[2].write(f"🔴 **{item['stock']}**" if item["stock"] <= item["safe_stock"] else str(item["stock"]))
            cols[3].write(item["entry_date"])
            cols[4].write(str(item["safe_stock"]))
            cols[5].write(item["supplier"])
            cols[6].write(item["notes"])
        else:
            cols = st.columns([1, 2.5, 1.5, 3, 1.5])
            cols[0].write(str(item["item_id"]))
            cols[1].write(item["name"])
            cols[2].write(f"🔴 **{item['stock']}**" if item["stock"] <= item["safe_stock"] else str(item["stock"]))
            
            with cols[3]:
                action_cols = st.columns([1, 1, 1.5])
                with action_cols[0]:
                    st.button("➕", key=f"add_{item['page_id']}", on_click=update_stock, args=(item['page_id'], item["stock"], 1))
                with action_cols[1]:
                    st.button("➖", key=f"sub_{item['page_id']}", on_click=update_stock, args=(item['page_id'], item["stock"], -1), disabled=(item["stock"] <= 0))
                with action_cols[2]:
                    with st.popover("📝"):
                        # Use a unique key for the manual stock input
                        new_val = st.number_input("設定數量", min_value=0, value=item["stock"], step=1, key=f"num_{item['page_id']}")
                        if st.button("更新", key=f"update_{item['page_id']}"):
                            update_stock(item['page_id'], item["stock"], new_absolute_stock=new_val)
                            st.rerun()
            
            cols[4].write(item["entry_date"])
        
        # Add compact divider between rows
        st.markdown("<hr style='margin: 8px 0px !important; border-top: 1px solid rgba(128,128,128,0.2); min-width: 800px;' />", unsafe_allow_html=True)

# Sidebar Navigation
page = st.sidebar.radio("導覽列", ["📊 總覽 (Dashboard)", "📦 進出貨管理 (In/Outbound)"])

if page == "📊 總覽 (Dashboard)":
    st.title("📊 總覽 (Dashboard)")
    st.markdown("檢視目前庫存狀態。")
    render_inventory_table(is_dashboard=True)
else:
    st.title("📦 進出貨管理 (Inbound/Outbound)")
    st.markdown("進階管理：更新庫存數量、新增商品紀錄。")
    
    with st.expander("➕ 新增商品 (Add New Product)"):
        with st.form("new_product_form"):
            new_id = st.number_input("ID (不可重複)", min_value=1, step=1)
            new_name = st.text_input("商品名稱")
            new_supplier = st.text_input("供應商")
            new_stock = st.number_input("初始庫存", min_value=0, step=1)
            new_safe_stock = st.number_input("安全庫存", min_value=0, step=1)
            new_notes = st.text_area("備註")
            
            if st.form_submit_button("新增送出"):
                if new_name:
                    add_new_product(new_name, int(new_id), int(new_stock), int(new_safe_stock), new_supplier, new_notes)
                else:
                    st.error("商品名稱不得為空！")
                    
    st.divider()
    render_inventory_table(is_dashboard=False)

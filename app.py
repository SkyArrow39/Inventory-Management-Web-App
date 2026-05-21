import streamlit as st
from notion_client import Client
import os
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

# UI Header
st.title("📦 Inventory Management Dashboard")
st.markdown("Manage your Notion database inventory seamlessly.")

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

def update_stock(page_id, current_stock, delta):
    new_stock = current_stock + delta
    if new_stock < 0:
        return
        
    # Find index in session state
    index = next((i for i, item in enumerate(st.session_state["items"]) if item["page_id"] == page_id), -1)
    if index == -1:
        return
        
    # Optimistic local update
    st.session_state["items"][index]["stock"] = new_stock
    
    try:
        notion.pages.update(
            page_id=page_id,
            properties={
                schema["stock"]: {"number": new_stock}
            }
        )
        st.toast(f"✅ [{st.session_state['items'][index]['name']}] Stock updated to {new_stock}!")
    except Exception as e:
        st.error(f"Failed to update stock: {e}")
        # Rollback
        st.session_state["items"][index]["stock"] = current_stock

# UI Layout
st.markdown("### Search & Filters")
col1, col2 = st.columns([3, 1])
with col1:
    search_query = st.text_input("🔍 Search by Name or ID", key="search_query", on_change=on_search_change)
with col2:
    st.write("") # Padding
    st.write("")
    low_stock_only = st.checkbox("⚠️ 只顯示庫存不足商品 (Low Stock Only)", key="low_stock", on_change=on_search_change)

if "items" not in st.session_state or st.session_state.get("refresh", True):
    with st.spinner("Fetching data from Notion..."):
        st.session_state["items"] = fetch_inventory(st.session_state.get("search_query", ""), st.session_state.get("low_stock", False))
        st.session_state["refresh"] = False

# Custom CSS for tighter rows
st.markdown("""
    <style>
        /* Reduce padding around columns */
        div[data-testid="column"] {
            padding: 0rem 0.5rem !important;
        }
        /* Reduce text margin and size slightly */
        div[data-testid="stMarkdownContainer"] p {
            margin-bottom: 0px !important;
            font-size: 14px;
        }
        /* Make buttons smaller */
        div.stButton > button {
            padding-top: 0px !important;
            padding-bottom: 0px !important;
            min-height: 0px !important;
            height: 28px !important;
        }
        /* Reduce space around divider */
        hr {
            margin: 0.5em 0px !important;
        }
        
        /* Mobile responsive adjustments */
        @media (max-width: 768px) {
            /* Target table rows (8 columns) to prevent them from stacking */
            div[data-testid="stHorizontalBlock"]:has(> div:nth-child(8)) {
                flex-direction: row !important;
                flex-wrap: nowrap !important;
                min-width: 800px !important;
            }
            
            /* Override Streamlit's default 100% width on mobile to fix super-wide layout */
            div[data-testid="stHorizontalBlock"]:has(> div:nth-child(8)) > div:nth-child(1) { width: 72px !important; min-width: 72px !important; max-width: 72px !important; flex: none !important; }
            div[data-testid="stHorizontalBlock"]:has(> div:nth-child(8)) > div:nth-child(2) { width: 144px !important; min-width: 144px !important; max-width: 144px !important; flex: none !important; }
            div[data-testid="stHorizontalBlock"]:has(> div:nth-child(8)) > div:nth-child(3) { width: 72px !important; min-width: 72px !important; max-width: 72px !important; flex: none !important; }
            div[data-testid="stHorizontalBlock"]:has(> div:nth-child(8)) > div:nth-child(4) { width: 104px !important; min-width: 104px !important; max-width: 104px !important; flex: none !important; }
            div[data-testid="stHorizontalBlock"]:has(> div:nth-child(8)) > div:nth-child(5) { width: 88px !important; min-width: 88px !important; max-width: 88px !important; flex: none !important; }
            div[data-testid="stHorizontalBlock"]:has(> div:nth-child(8)) > div:nth-child(6) { width: 72px !important; min-width: 72px !important; max-width: 72px !important; flex: none !important; }
            div[data-testid="stHorizontalBlock"]:has(> div:nth-child(8)) > div:nth-child(7) { width: 104px !important; min-width: 104px !important; max-width: 104px !important; flex: none !important; }
            div[data-testid="stHorizontalBlock"]:has(> div:nth-child(8)) > div:nth-child(8) { width: 144px !important; min-width: 144px !important; max-width: 144px !important; flex: none !important; }

            /* Prevent button columns from stacking */
            div[data-testid="stHorizontalBlock"]:has(> div:nth-child(8)) div[data-testid="stHorizontalBlock"] {
                flex-direction: row !important;
                flex-wrap: nowrap !important;
            }
            div[data-testid="stHorizontalBlock"]:has(> div:nth-child(8)) div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {
                width: 50% !important;
                flex: none !important;
            }
            
            /* Allow main block container to scroll horizontally */
            .block-container {
                overflow-x: auto !important;
                padding-bottom: 20px !important;
            }
        }
    </style>
""", unsafe_allow_html=True)

# Displaying items
st.markdown("### Inventory List")
# Sorting, Filtering & Pagination
if not st.session_state["items"]:
    st.info("No items found.")
else:
    # Sort and Filter UI
    st.markdown("### 📊 排序與篩選 (Sort & Filter)")
    sort_col1, sort_col2, sort_col3 = st.columns([1, 1, 2])
    with sort_col1:
        sort_by = st.selectbox("排序依據", ["ID", "商品名稱", "庫存數量", "入庫日期"])
    with sort_col2:
        sort_order = st.selectbox("排序方式", ["升序 (Ascending)", "降序 (Descending)"])
    with sort_col3:
        suppliers = list(set([item["supplier"] for item in st.session_state["items"] if item["supplier"]]))
        selected_suppliers = st.multiselect("供應商篩選", suppliers)
        
    # Apply filtering
    filtered_items = st.session_state["items"]
    if selected_suppliers:
        filtered_items = [item for item in filtered_items if item["supplier"] in selected_suppliers]
        
    # Apply sorting
    sort_key_map = {
        "ID": "item_id",
        "商品名稱": "name",
        "庫存數量": "stock",
        "入庫日期": "entry_date"
    }
    reverse_sort = (sort_order == "降序 (Descending)")
    filtered_items.sort(
        key=lambda x: x[sort_key_map[sort_by]] if x[sort_key_map[sort_by]] is not None else ("", 0)[isinstance(x[sort_key_map[sort_by]], int)], 
        reverse=reverse_sort
    )

    # Pagination UI
    st.markdown("### 📃 分頁 (Pagination)")
    page_col1, page_col2 = st.columns([1, 3])
    with page_col1:
        page_size = st.selectbox("每頁顯示筆數", [10, 20, 50], index=2)
        
    total_items = len(filtered_items)
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    
    if "current_page" not in st.session_state:
        st.session_state["current_page"] = 1
        
    # Validate current_page
    if st.session_state["current_page"] > total_pages:
        st.session_state["current_page"] = total_pages
        
    with page_col2:
        st.write("")
        st.write("")
        btn_prev, text_page, btn_next = st.columns([1, 2, 1])
        with btn_prev:
            if st.button("⬅️ 上一頁", disabled=(st.session_state["current_page"] == 1)):
                st.session_state["current_page"] -= 1
                st.rerun()
        with text_page:
            st.markdown(f"<div style='text-align: center;'>第 <b>{st.session_state['current_page']}</b> 頁，共 <b>{total_pages}</b> 頁 (共 {total_items} 筆)</div>", unsafe_allow_html=True)
        with btn_next:
            if st.button("下一頁 ➡️", disabled=(st.session_state["current_page"] == total_pages)):
                st.session_state["current_page"] += 1
                st.rerun()

    start_idx = (st.session_state["current_page"] - 1) * page_size
    end_idx = start_idx + page_size
    paginated_items = filtered_items[start_idx:end_idx]

    # Render table header
    header_cols = st.columns([1, 2, 1, 1.5, 1.2, 1, 1.5, 2])
    headers = ["ID", "商品名稱", "庫存數量", "入庫日期", "操作", "安全庫存", "供應商", "備註"]
    for c, h in zip(header_cols, headers):
        c.markdown(f"**{h}**")
        
    st.divider()
    
    # Render rows
    for item in paginated_items:
        cols = st.columns([1, 2, 1, 1.5, 1.2, 1, 1.5, 2])
        cols[0].write(str(item["item_id"]))
        cols[1].write(item["name"])
        
        # Highlight low stock
        stock_display = f"🔴 **{item['stock']}**" if item["stock"] <= item["safe_stock"] else str(item["stock"])
        cols[2].write(stock_display)
        
        cols[3].write(item["entry_date"])
        
        with cols[4]:
            btn_cols = st.columns(2)
            with btn_cols[0]:
                st.button("➕", key=f"add_{item['page_id']}", on_click=update_stock, args=(item['page_id'], item["stock"], 1))
            with btn_cols[1]:
                st.button("➖", key=f"sub_{item['page_id']}", on_click=update_stock, args=(item['page_id'], item["stock"], -1), disabled=(item["stock"] <= 0))
        
        cols[5].write(str(item["safe_stock"]))
        cols[6].write(item["supplier"])
        cols[7].write(item["notes"])

import streamlit as st
import pandas as pd
from pymongo import MongoClient
import shortuuid
from datetime import datetime, timedelta
import plotly.express as px
import io
import hashlib
import certifi

# --- MongoDB Connection ---
# IMPORTANT: Replace with your MongoDB connection string.
MONGO_CONNECTION_STRING = "mongodb://localhost:27017/"

@st.cache_resource
def get_mongo_client():
    """Establishes a connection to MongoDB and returns the client."""
    try:
        # --- FIX: Added tlsCAFile parameter for secure SSL connection ---
        ca = certifi.where()
        client = MongoClient(MONGO_CONNECTION_STRING, tlsCAFile=ca)
        client.admin.command('ismaster')
        return client
    except Exception as e:
        st.error(f"Failed to connect to MongoDB: {e}")
        st.stop()

client = get_mongo_client()
db = client.inventory_billing_system
inventory_collection = db.inventory
bills_collection = db.bills
inventory_log_collection = db.inventory_log
users_collection = db.users

# --- Page Configuration ---
st.set_page_config(
    page_title="Inventory & Billing System",
    page_icon="📦",
    layout="wide",
)

# --- Helper Functions ---
def generate_unique_id(prefix):
    return f"{prefix}-{shortuuid.uuid()}"

def get_inventory_df():
    inventory_items = list(inventory_collection.find({}, {'_id': 0}))
    return pd.DataFrame(inventory_items)

def log_inventory_change(item_id, item_name, quantity_change, purchase_cost_change, reason):
    log_entry = {
        "log_id": generate_unique_id("LOG"), "item_id": item_id, "item_name": item_name,
        "quantity_change": quantity_change, "purchase_cost_change": purchase_cost_change,
        "reason": reason, "timestamp": datetime.now()
    }
    inventory_log_collection.insert_one(log_entry)

def get_business_date(timestamp):
    if timestamp.hour < 6:
        return timestamp.date() - timedelta(days=1)
    else:
        return timestamp.date()

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def check_password_strength(password):
    return len(password) >= 6

def update_user_status(username, status):
    users_collection.update_one(
        {"username": username},
        {"$set": {"status": status, "last_seen": datetime.now()}}
    )

def check_password():
    """Returns `True` if the user has entered the correct password."""
    CORRECT_PASSWORD = "admin"

    def password_entered():
        """Checks whether a password entered by the user is correct."""
        if st.session_state["password"] == CORRECT_PASSWORD:
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.text_input("Password", type="password", on_change=password_entered, key="password")
        return False
    elif not st.session_state["password_correct"]:
        st.text_input("Password", type="password", on_change=password_entered, key="password")
        st.error("😕 Password incorrect")
        return False
    else:
        return True

# --- Page Implementations (Defined before they are called in main_app) ---
def inventory_management_page():
    st.header("Manage Your Inventory")
    
    tab1, tab2 = st.tabs(["Add New Item", "Update Existing Item"])

    with tab1:
        st.subheader("Add a New Item")
        with st.form("add_item_form", clear_on_submit=True):
            item_name = st.text_input("Item Name", placeholder="e.g., Laptop")
            purchase_price = st.number_input("Purchase Price (₹)", min_value=0.0, format="%.2f")
            selling_price = st.number_input("Selling Price (₹)", min_value=0.0, format="%.2f")
            quantity = st.number_input("Quantity", min_value=1, step=1)
            submitted = st.form_submit_button("Add Item to Inventory")

            if submitted:
                if not item_name:
                    st.warning("Please enter an item name.")
                elif selling_price < purchase_price:
                    st.warning("Selling price should not be less than purchase price.")
                else:
                    item_id = generate_unique_id("ITEM")
                    item_data = {
                        "item_id": item_id, "item_name": item_name, "purchase_price": purchase_price,
                        "selling_price": selling_price, "quantity": quantity, "created_at": datetime.now()
                    }
                    inventory_collection.insert_one(item_data)
                    log_inventory_change(item_id, item_name, quantity, purchase_price * quantity, "Initial Stock")
                    st.success(f"✅ Successfully added '{item_name}' to inventory!")

    with tab2:
        st.subheader("Update Stock and/or Prices")
        inventory_list = list(inventory_collection.find({}, {'_id': 0, 'item_name': 1, 'item_id': 1}))
        if not inventory_list:
            st.info("No items in inventory to update.")
        else:
            item_options = {f"{item['item_name']}": item['item_id'] for item in inventory_list}
            selected_item_name = st.selectbox("Select Item to Update", options=item_options.keys())
            
            if selected_item_name:
                item_id = item_options[selected_item_name]
                item_details = inventory_collection.find_one({"item_id": item_id})
                
                with st.form("update_item_form"):
                    st.write(f"**Current Values for {selected_item_name}**")
                    st.write(f"Quantity: `{item_details['quantity']}` | Purchase Price: `₹{item_details['purchase_price']:.2f}` | Selling Price: `₹{item_details['selling_price']:.2f}`")
                    st.divider()

                    st.write("**Update Quantity**")
                    quantity_change = st.number_input("Quantity to Add/Remove (+/-)", value=0, step=1)
                    reason = st.selectbox("Reason for Quantity Change", ["No Change", "Restock", "Correction (e.g., damaged goods, recount)"])
                    
                    st.divider()
                    st.write("**Update Prices**")
                    new_purchase_price = st.number_input("New Purchase Price (optional)", value=float(item_details['purchase_price']), format="%.2f")
                    new_selling_price = st.number_input("New Selling Price (optional)", value=float(item_details['selling_price']), format="%.2f")
                    
                    update_submitted = st.form_submit_button("Update Item")
                    
                    if update_submitted:
                        if new_purchase_price != item_details['purchase_price']:
                            revaluation_cost_change = (new_purchase_price - item_details['purchase_price']) * item_details['quantity']
                            if revaluation_cost_change != 0:
                                log_inventory_change(item_id, selected_item_name, 0, revaluation_cost_change, "Price Revaluation")

                        if quantity_change != 0 and reason != "No Change":
                            cost_change = 0
                            if reason == "Restock" and quantity_change > 0:
                                cost_change = new_purchase_price * quantity_change
                            elif reason == "Correction (e.g., damaged goods, recount)":
                                cost_change = item_details['purchase_price'] * quantity_change
                            if cost_change != 0:
                                log_inventory_change(item_id, selected_item_name, quantity_change, cost_change, reason)

                        update_payload = {}
                        set_payload = {}
                        if new_purchase_price != item_details['purchase_price']: set_payload['purchase_price'] = new_purchase_price
                        if new_selling_price != item_details['selling_price']: set_payload['selling_price'] = new_selling_price
                        if set_payload: update_payload['$set'] = set_payload
                        if quantity_change != 0 and reason != "No Change": update_payload['$inc'] = {'quantity': quantity_change}

                        if update_payload:
                            inventory_collection.update_one({"item_id": item_id}, update_payload)
                            st.success(f"Item '{selected_item_name}' updated successfully!")
                            st.rerun()
                        else:
                            st.warning("No changes were made.")
    st.divider()
    st.subheader("Current Inventory")
    try:
        inventory_df = get_inventory_df()
        if not inventory_df.empty:
            columns_to_show = ["item_id", "item_name", "quantity", "purchase_price", "selling_price", "created_at"]
            existing_columns = [col for col in columns_to_show if col in inventory_df.columns]
            st.dataframe(inventory_df[existing_columns], use_container_width=True)
        else:
            st.info("Your inventory is currently empty.")
    except Exception as e:
        st.error(f"An error occurred while fetching inventory: {e}")

def billing_system_page():
    is_edit_mode = "bill_to_edit" in st.session_state
    st.header("Update Bill" if is_edit_mode else "Create a New Bill")

    inventory_items = list(inventory_collection.find({}, {'_id': 0, 'item_name': 1, 'item_id': 1, 'quantity': 1, 'purchase_price': 1, 'selling_price': 1}))
    
    if is_edit_mode:
        bill_to_edit = st.session_state.bill_to_edit
        for item_in_bill in bill_to_edit['items']:
            for inv_item in inventory_items:
                if inv_item['item_id'] == item_in_bill['item_id']:
                    inv_item['quantity'] += item_in_bill['quantity']
                    break
    
    available_items = [item for item in inventory_items if item['quantity'] > 0]
    if not available_items:
        st.warning("Inventory is empty or all items are out of stock. Please add or restock items.")
        st.stop()

    item_options = {f"{item['item_name']} (Stock: {item['quantity']})": item['item_id'] for item in available_items}
    
    default_selected_items = []
    if is_edit_mode:
        bill_to_edit = st.session_state.bill_to_edit
        for item_in_bill in bill_to_edit['items']:
             for option_name, option_id in item_options.items():
                 if option_id == item_in_bill['item_id']:
                     default_selected_items.append(option_name)
                     break

    st.subheader("1. Select Items")
    selected_item_names = st.multiselect("Select Items for the bill", options=item_options.keys(), default=default_selected_items, label_visibility="collapsed")

    if selected_item_names:
        bill_items_with_qty, suggested_total_sell_price, suggested_total_purchase_price = [], 0, 0
        st.subheader("2. Specify Quantities")
        for name in selected_item_names:
            item_id = item_options[name]
            item_details = next((item for item in available_items if item['item_id'] == item_id), None)
            if item_details:
                max_qty = item_details['quantity']
                default_qty = 1
                if is_edit_mode:
                    item_in_bill = next((i for i in st.session_state.bill_to_edit['items'] if i['item_id'] == item_id), None)
                    if item_in_bill: default_qty = item_in_bill['quantity']

                col1, col2 = st.columns([2, 1])
                with col1: st.text(f"{item_details['item_name']} (Price: ₹{item_details.get('selling_price', 0):.2f})")
                with col2: qty = st.number_input("Quantity", 1, max_qty, default_qty, 1, key=f"qty_{item_id}", label_visibility="collapsed")
                
                bill_items_with_qty.append({
                    "item_id": item_id, "item_name": item_details['item_name'], "quantity": qty,
                    "selling_price": item_details.get('selling_price', 0), "purchase_price": item_details.get('purchase_price', 0)
                })
                suggested_total_sell_price += qty * item_details.get('selling_price', 0)
                suggested_total_purchase_price += qty * item_details.get('purchase_price', 0)
        
        st.subheader("3. Finalize Bill")
        st.metric("Suggested Sell Price", f"₹{suggested_total_sell_price:.2f}")
        sell_at_cost = st.checkbox("Sell at Purchase Price")

        with st.form("create_bill_form"):
            customer_name = ""
            if sell_at_cost:
                default_name = st.session_state.bill_to_edit.get('customer_name', '') if is_edit_mode else ""
                customer_name = st.text_input("Customer Name (for at-cost sale)", value=default_name)

            final_price_value = suggested_total_purchase_price if sell_at_cost else suggested_total_sell_price
            total_sell_price = st.number_input("Final Sell Price (₹)", 0.01, value=float(final_price_value), format="%.2f")
            
            payment_mode_index = ["Cash", "UPI"].index(st.session_state.bill_to_edit['payment_mode']) if is_edit_mode else 1
            payment_status_index = ["Paid", "Unpaid"].index(st.session_state.bill_to_edit.get('payment_status', 'Paid')) if is_edit_mode else 0

            col1, col2 = st.columns(2)
            with col1: payment_mode = st.radio("Mode of Payment", ["Cash", "UPI"], horizontal=True, index=payment_mode_index)
            with col2: payment_status = st.radio("Payment Status", ["Paid", "Unpaid"], horizontal=True, index=payment_status_index)
            
            submit_button_label = "Update Bill" if is_edit_mode else "Generate Bill"
            submit_bill = st.form_submit_button(submit_button_label)

            if submit_bill:
                if not bill_items_with_qty and not is_edit_mode:
                    st.error("Please select at least one item for the bill.")
                else:
                    total_purchase_cost = sum(item['quantity'] * item['purchase_price'] for item in bill_items_with_qty)
                    profit = total_sell_price - total_purchase_cost
                    final_bill_items = [{k: v for k, v in item.items() if k != 'purchase_price'} for item in bill_items_with_qty]

                    if is_edit_mode:
                        original_bill = st.session_state.bill_to_edit
                        inventory_deltas = {}
                        for item in original_bill['items']: inventory_deltas[item['item_id']] = inventory_deltas.get(item['item_id'], 0) + item['quantity']
                        for item in final_bill_items: inventory_deltas[item['item_id']] = inventory_deltas.get(item['item_id'], 0) - item['quantity']
                        for item_id, quantity_change in inventory_deltas.items():
                            if quantity_change != 0: inventory_collection.update_one({"item_id": item_id}, {"$inc": {"quantity": quantity_change}})
                        
                        updated_bill_data = {
                            "items": final_bill_items, "total_purchase_cost": total_purchase_cost, "total_sell_price": total_sell_price,
                            "profit": profit, "payment_mode": payment_mode, "payment_status": payment_status,
                            "customer_name": customer_name if sell_at_cost else "", "timestamp": datetime.now(),
                            "last_edited_by": st.session_state['username']
                        }
                        bills_collection.update_one({"bill_id": original_bill['bill_id']}, {"$set": updated_bill_data})
                        st.success(f"🎉 Bill {original_bill['bill_id']} updated successfully!")
                        del st.session_state.bill_to_edit
                        st.session_state.page = "View Bills"
                        st.rerun()
                    else:
                        bill_data = {
                            "bill_id": generate_unique_id("BILL"), "items": final_bill_items, "total_purchase_cost": total_purchase_cost,
                            "total_sell_price": total_sell_price, "profit": profit, "payment_mode": payment_mode,
                            "payment_status": payment_status, "customer_name": customer_name if sell_at_cost else "", 
                            "created_by": st.session_state['username'], "timestamp": datetime.now()
                        }
                        bills_collection.insert_one(bill_data)
                        for item in final_bill_items:
                            inventory_collection.update_one({"item_id": item['item_id']}, {"$inc": {"quantity": -item['quantity']}})
                        st.success(f"🎉 Bill {bill_data['bill_id']} generated successfully!")
                        st.rerun()

def view_bills_page():
    st.header("Past Bills")
    try:
        all_bills = list(bills_collection.find({}, {'_id': 0}).sort("timestamp", -1))
        if not all_bills:
            st.info("No bills have been generated yet.")
        else:
            for bill in all_bills:
                status = bill.get('payment_status', 'Paid')
                status_emoji = "✅" if status == "Paid" else "❌"
                expander_label = f"{status_emoji} Bill ID: {bill['bill_id']} - {bill['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}"
                
                with st.expander(expander_label):
                    cols = st.columns([2,2,2,1,1,1])
                    cols[0].metric("Total Sell Price", f"₹{bill['total_sell_price']:.2f}")
                    cols[1].metric("Cost of Goods", f"₹{bill.get('total_purchase_cost', 0):.2f}")
                    profit = bill.get('profit', 0)
                    cols[2].metric("Profit/Loss", f"₹{profit:.2f}", delta=f"{profit:.2f}")
                    
                    with cols[3]:
                        if st.button("Edit", key=f"edit_{bill['bill_id']}"):
                            st.session_state.bill_to_edit = bill
                            st.session_state.page = "Billing System"
                            st.rerun()
                    with cols[4]:
                        if st.button("Delete", key=f"delete_{bill['bill_id']}"):
                            for item in bill['items']:
                                inventory_collection.update_one({"item_id": item['item_id']}, {"$inc": {"quantity": item['quantity']}})
                                item_details = inventory_collection.find_one({"item_id": item['item_id']})
                                log_inventory_change(item['item_id'], item['item_name'], item['quantity'], item_details['purchase_price'] * item['quantity'], f"Bill Deletion Reversal ({bill['bill_id']})")
                            bills_collection.delete_one({"bill_id": bill['bill_id']})
                            st.success(f"Bill {bill['bill_id']} deleted successfully.")
                            st.rerun()
                    with cols[5]:
                        if status == 'Unpaid':
                            if st.button("Mark Paid", key=f"pay_{bill['bill_id']}"):
                                bills_collection.update_one({"bill_id": bill['bill_id']}, {"$set": {"payment_status": "Paid"}})
                                st.success(f"Bill {bill['bill_id']} marked as Paid.")
                                st.rerun()

                    color = "green" if status == "Paid" else "red"
                    st.write(f"**Payment Mode:** {bill['payment_mode']} | **Status:** <span style='color:{color};'>{status}</span>", unsafe_allow_html=True)
                    if bill.get('customer_name'): st.write(f"**Customer:** {bill['customer_name']}")
                    if bill.get('created_by'): st.write(f"**Created By:** {bill['created_by']}")
                    if bill.get('last_edited_by'): st.write(f"**Last Edited By:** {bill['last_edited_by']}")

                    items_df = pd.DataFrame(bill['items'])
                    st.dataframe(items_df, use_container_width=True)
    except Exception as e:
        st.error(f"An error occurred while fetching bills: {e}")

def analyze_profit_page():
    st.header("📊 Profit Analysis")
    all_bills = list(bills_collection.find({}, {'_id': 0}))
    all_inventory_logs = list(inventory_log_collection.find({}, {'_id': 0}))

    if not all_bills and not all_inventory_logs:
        st.info("No data available to analyze.")
    else:
        bills_df = pd.DataFrame(all_bills) if all_bills else pd.DataFrame()
        if not bills_df.empty:
            bills_df['business_date'] = bills_df['timestamp'].apply(get_business_date)
            paid_bills_df = bills_df[bills_df['payment_status'] == 'Paid'].copy()
            unpaid_bills_df = bills_df[bills_df['payment_status'] == 'Unpaid']
            total_outstanding = unpaid_bills_df['total_sell_price'].sum()
            st.metric("Total Outstanding Revenue", f"₹{total_outstanding:.2f}")
        else:
            st.metric("Total Outstanding Revenue", "₹0.00")

        if not bills_df.empty and 'payment_status' in bills_df.columns and not paid_bills_df.empty:
            st.subheader("Day-wise Realized Profit (from Paid Bills)")
            daily_profit = paid_bills_df.groupby('business_date')['profit'].sum()
            st.line_chart(daily_profit)
        else:
            st.info("No paid sales recorded yet.")

        st.subheader("Daily Paid Sales vs. Purchases")
        daily_sales = pd.Series(dtype=float)
        if not bills_df.empty and 'payment_status' in bills_df.columns and not paid_bills_df.empty:
            daily_sales = paid_bills_df.groupby('business_date')['total_sell_price'].sum().rename("Sales")

        daily_purchases = pd.Series(dtype=float)
        if all_inventory_logs:
            log_df = pd.DataFrame(all_inventory_logs)
            log_df['business_date'] = log_df['timestamp'].apply(get_business_date)
            daily_purchases = log_df.groupby('business_date')['purchase_cost_change'].sum().rename("Purchases")

        if not daily_sales.empty or not daily_purchases.empty:
            daily_summary = pd.concat([daily_sales, daily_purchases], axis=1).fillna(0).reset_index().rename(columns={'index': 'business_date'})
            if 'Sales' not in daily_summary.columns: daily_summary['Sales'] = 0
            if 'Purchases' not in daily_summary.columns: daily_summary['Purchases'] = 0
            melted_summary = pd.melt(daily_summary, id_vars=['business_date'], value_vars=['Sales', 'Purchases'], var_name='Transaction Type', value_name='Amount (₹)')
            fig = px.bar(melted_summary, x='business_date', y='Amount (₹)', color='Transaction Type', barmode='group', labels={'business_date': 'Date', 'Amount (₹)': 'Amount (₹)'})
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No sales or purchase data to display.")

        if not bills_df.empty:
            st.subheader("Profit per Bill")
            bill_status_filter = st.selectbox("Filter by status:", ["All", "Paid", "Unpaid"])
            filtered_df = bills_df if bill_status_filter == "All" else bills_df[bills_df['payment_status'] == bill_status_filter]
            profit_per_bill_df = filtered_df[['bill_id', 'timestamp', 'total_sell_price', 'total_purchase_cost', 'profit', 'payment_status']].copy().sort_values(by='timestamp', ascending=False)
            st.dataframe(profit_per_bill_df, use_container_width=True, hide_index=True)

def inventory_history_page():
    st.header("📜 Inventory Purchase and Update History")
    try:
        all_logs = list(inventory_log_collection.find({}, {'_id': 0}).sort("timestamp", -1))
        if not all_logs:
            st.info("No inventory history to display.")
        else:
            st.dataframe(pd.DataFrame(all_logs), use_container_width=True, hide_index=True)
    except Exception as e:
        st.error(f"An error occurred while fetching inventory history: {e}")

def daily_report_page():
    st.header("📄 Daily Report")
    st.write("Generate a downloadable Excel report of the current inventory status and yesterday's transactions.")

    now = datetime.now()
    today_business_date = get_business_date(now)
    yesterday_business_date = today_business_date - timedelta(days=1)
    start_of_report_period = datetime.combine(yesterday_business_date, datetime.min.time()).replace(hour=6)
    end_of_report_period = datetime.combine(today_business_date, datetime.min.time()).replace(hour=6)

    inventory_report_df = get_inventory_df()
    yesterdays_bills = list(bills_collection.find({"timestamp": {"$gte": start_of_report_period, "$lt": end_of_report_period}}, {'_id': 0}))
    bills_report_df = pd.DataFrame(yesterdays_bills)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        inventory_report_df.to_excel(writer, sheet_name='Inventory Status', index=False)
        bills_report_df.to_excel(writer, sheet_name="Yesterday's Transactions", index=False)
    
    st.download_button("📥 Download Daily Report", output.getvalue(), f"daily_report_{now.strftime('%Y-%m-%d')}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

def settings_page():
    st.header("⚙️ Settings")
    st.subheader("Reset Transactions and Stock")
    st.warning("⚠️ This will delete all bills and reset your stock levels. This action cannot be undone.")
    with st.expander("Proceed with transaction reset"):
        reset_confirmation = st.text_input("To confirm, please type `RESET BILLS`.")
        if st.button("Reset All Transactions"):
            if reset_confirmation.strip() == "RESET BILLS":
                try:
                    all_bills_to_reset = list(bills_collection.find({}))
                    for bill in all_bills_to_reset:
                        for item in bill['items']:
                            inventory_collection.update_one({"item_id": item['item_id']}, {"$inc": {"quantity": item['quantity']}})
                    bills_collection.delete_many({})
                    st.success("✅ All bills have been deleted and stock has been reset.")
                    st.rerun()
                except Exception as e:
                    st.error(f"An error occurred during the reset: {e}")
            else:
                st.error("Confirmation text did not match.")
    st.divider()
    st.subheader("Clear All Data")
    st.warning("⚠️ **DANGER ZONE**: This will permanently delete all inventory, bills, and history.")
    with st.expander("Proceed with full data deletion"):
        confirmation_text = st.text_input("To confirm, please type `DELETE`.")
        if st.button("Permanently Delete All Data"):
            if confirmation_text.strip() == "DELETE":
                try:
                    inventory_collection.drop()
                    bills_collection.drop()
                    inventory_log_collection.drop()
                    st.success("✅ All data has been successfully deleted.")
                    st.rerun()
                except Exception as e:
                    st.error(f"An error occurred while deleting data: {e}")
            else:
                st.error("Confirmation text did not match.")

def iam_page():
    st.header("🔑 Identity and Access Management (IAM)")
    
    iam_tabs = st.tabs(["Add User", "Manage Users", "My Profile"])

    with iam_tabs[0]:
        st.subheader("Add New User")
        with st.form("add_user_form", clear_on_submit=True):
            new_username = st.text_input("Username")
            new_name = st.text_input("Full Name")
            new_email = st.text_input("Email")
            new_password = st.text_input("Password", type="password")
            new_role = st.selectbox("Role", ["Admin", "Co-Admin", "Biller"])
            if st.form_submit_button("Add User"):
                if not all([new_username, new_password, new_name, new_email]):
                    st.warning("All fields are required.")
                elif users_collection.find_one({"username": new_username}):
                    st.error("Username already exists.")
                elif not check_password_strength(new_password):
                    st.error("Password must be at least 6 characters long.")
                else:
                    users_collection.insert_one({
                        "username": new_username, "name": new_name, "email": new_email,
                        "password": hash_password(new_password), "role": new_role, 
                        "status": "Offline", "last_seen": None
                    })
                    st.success(f"User '{new_username}' added successfully.")

    with iam_tabs[1]:
        st.subheader("Manage User Details")
        all_users = list(users_collection.find({}, {'_id': 0, 'password': 0}))
        if not all_users:
            st.info("No users found.")
        else:
            users_df = pd.DataFrame(all_users)
            online_threshold = datetime.now() - timedelta(minutes=5)
            users_df['status'] = users_df['last_seen'].apply(lambda last_seen: "Online" if last_seen and last_seen > online_threshold else "Offline")
            st.dataframe(users_df, use_container_width=True)

            user_to_update = st.selectbox("Select User to Update/Delete", options=[user['username'] for user in all_users])
            if user_to_update:
                user_details = users_collection.find_one({"username": user_to_update})
                with st.form("update_user_form"):
                    st.write(f"**Editing User: {user_to_update}**")
                    new_username = st.text_input("New Username", value=user_details['username'])
                    new_name = st.text_input("New Full Name", value=user_details.get('name', ''))
                    new_email = st.text_input("New Email", value=user_details.get('email', ''))
                    new_role = st.selectbox("New Role", ["Admin", "Co-Admin", "Biller"], index=["Admin", "Co-Admin", "Biller"].index(user_details['role']))
                    new_password = st.text_input("New Password (leave blank to keep unchanged)", type="password")
                    
                    col1, col2 = st.columns([1,5])
                    with col1:
                        if st.form_submit_button("Update User"):
                            update_data = {
                                "username": new_username, "name": new_name,
                                "email": new_email, "role": new_role
                            }
                            if new_password:
                                if check_password_strength(new_password):
                                    update_data['password'] = hash_password(new_password)
                                else:
                                    st.error("Password must be at least 6 characters long.")
                                    return
                            
                            # Check if new username already exists
                            if new_username != user_to_update and users_collection.find_one({"username": new_username}):
                                st.error("New username already exists.")
                            else:
                                users_collection.update_one({"username": user_to_update}, {"$set": update_data})
                                st.success(f"User '{user_to_update}' updated successfully.")
                                st.rerun()

                    with col2:
                        if user_to_update != 'admin': # Prevent admin from being deleted
                            if st.form_submit_button("Delete User"):
                                users_collection.delete_one({"username": user_to_update})
                                st.success(f"User '{user_to_update}' has been deleted.")
                                st.rerun()

    with iam_tabs[2]:
        st.subheader("My Profile")
        with st.form("update_my_password_form"):
            st.write(f"You are logged in as **{st.session_state['username']}**")
            my_new_password = st.text_input("New Password", type="password")
            confirm_password = st.text_input("Confirm New Password", type="password")
            if st.form_submit_button("Update My Password"):
                if not my_new_password:
                    st.warning("Password cannot be empty.")
                elif not check_password_strength(my_new_password):
                    st.error("Password must be at least 6 characters long.")
                elif my_new_password != confirm_password:
                    st.error("Passwords do not match.")
                else:
                    users_collection.update_one(
                        {"username": st.session_state['username']},
                        {"$set": {"password": hash_password(my_new_password)}}
                    )
                    st.success("Your password has been updated successfully.")

def log_book_page():
    st.header("📖 Log Book")
    all_bills = list(bills_collection.find({}, {'_id': 0}).sort("timestamp", -1))
    if not all_bills:
        st.info("No bills found in the log book.")
    else:
        log_data = []
        for bill in all_bills:
            log_data.append({
                "Bill ID": bill['bill_id'],
                "Created By": bill.get('created_by', 'N/A'),
                "Last Edited By": bill.get('last_edited_by', 'N/A'),
                "Timestamp": bill['timestamp'].strftime('%Y-%m-%d %H:%M:%S')
            })
        log_df = pd.DataFrame(log_data)
        st.dataframe(log_df, use_container_width=True, hide_index=True)

def login_page():
    st.header("Login")
    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Login"):
            user = users_collection.find_one({"username": username})
            if user and user['password'] == hash_password(password):
                st.session_state['logged_in'] = True
                st.session_state['username'] = user['username']
                st.session_state['role'] = user['role']
                update_user_status(username, 'Online')
                st.rerun()
            else:
                st.error("Incorrect username or password")

def initial_setup():
    if users_collection.count_documents({}) == 0:
        users_collection.insert_one({
            "username": "admin", "password": hash_password("admin"), 
            "role": "Admin", "status": "Offline", "last_seen": None,
            "name": "Default Admin", "email": "admin@example.com"
        })

# --- App Entry Point ---
def main_app():
    st.title("📦 Inventory and Billing Management System")
    update_user_status(st.session_state['username'], 'Online')

    user_role = st.session_state['role']
    if user_role == 'Admin':
        page_options = ["Inventory Management", "Billing System", "View Bills", "Analyze Profit", "Inventory History", "Daily Report", "IAM", "Log Book", "Settings"]
    elif user_role == 'Co-Admin':
        page_options = ["Inventory Management", "Billing System", "View Bills", "Analyze Profit", "Inventory History", "Daily Report", "Settings"]
    else: # Biller
        page_options = ["Billing System", "View Bills"]

    if 'page' not in st.session_state or st.session_state.page not in page_options:
        st.session_state.page = page_options[0]

    st.session_state.page = st.sidebar.radio("Navigate", page_options, index=page_options.index(st.session_state.page))
    st.sidebar.write(f"Logged in as: **{st.session_state['username']}** ({st.session_state['role']})")
    if st.sidebar.button("Logout"):
        update_user_status(st.session_state['username'], 'Offline')
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()

    if st.session_state.page == "Inventory Management": inventory_management_page()
    elif st.session_state.page == "Billing System": billing_system_page()
    elif st.session_state.page == "View Bills": view_bills_page()
    elif st.session_state.page == "Analyze Profit":
        if check_password(): analyze_profit_page()
    elif st.session_state.page == "Inventory History":
        if check_password(): inventory_history_page()
    elif st.session_state.page == "Daily Report": daily_report_page()
    elif st.session_state.page == "IAM": iam_page()
    elif st.session_state.page == "Log Book":
        if check_password(): log_book_page()
    elif st.session_state.page == "Settings": settings_page()


initial_setup()
if 'logged_in' not in st.session_state or not st.session_state['logged_in']:
    login_page()
else:
    main_app()

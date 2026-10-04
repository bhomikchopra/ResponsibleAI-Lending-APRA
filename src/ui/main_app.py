import os
import sys
import re
from pathlib import Path
from dotenv import load_dotenv

# --- 1. Path Resolution ---
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st
import duckdb
import pandas as pd

# LangChain & Gemini Imports
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage
from src.rag.vector_store import retrieve_regulatory_clauses

# Load environment variables
load_dotenv()

# --- 2. Configuration & Page Setup ---
st.set_page_config(page_title="Underwriter Console", layout="wide")

VALID_CREDENTIALS = {"admin": "password123", "underwriter_01": "securepass"}
DUCKDB_PATH = "data/audit_decisions.duckdb"

# Internal Core Banking CRM for Deanonymization
INTERNAL_CRM = {
    "APP-101": "Sarah Jenkins",
    "APP-102": "Rajiv Patel",
    "APP-103": "Emily Chen",
    "APP-104": "Emma Wright",
}

# Custom Styling
st.markdown("""
<style>
    .multicolor-text {
        background: linear-gradient(90deg, #4285f4, #ea4335, #fbbc05, #34a853);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-size: 200% auto;
        animation: gradientShift 5s ease infinite;
        font-weight: bold;
    }
    @keyframes gradientShift {
        0% { background-position: 0% 50%; }
        50% { background-position: 100% 50%; }
        100% { background-position: 0% 50%; }
    }
    .stButton>button {
        width: 100%;
    }
</style>
""", unsafe_allow_html=True)

# Initialize Session State
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "username" not in st.session_state:
    st.session_state.username = ""
if "current_page" not in st.session_state:
    st.session_state.current_page = "HOME"
if "active_app_id" not in st.session_state:
    st.session_state.active_app_id = None
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "pending_decision" not in st.session_state:
    st.session_state.pending_decision = None
if "queue_filter" not in st.session_state:
    st.session_state.queue_filter = "ALL"

# --- 3. Database Operations ---
def get_dashboard_stats():
    try:
        conn = duckdb.connect(DUCKDB_PATH)
        df = conn.execute("SELECT application_id, system_recommendation, underwriter_decision FROM audit_trail ORDER BY timestamp DESC").df()
        conn.close()
        return df.drop_duplicates(subset=['application_id'], keep='first') if not df.empty else pd.DataFrame()
    except Exception as e:
        st.error(f"Database Error: {e}")
        return pd.DataFrame()

def get_app_details(app_id):
    try:
        conn = duckdb.connect(DUCKDB_PATH)
        df = conn.execute(f"SELECT * FROM audit_trail WHERE application_id = '{app_id}' ORDER BY timestamp DESC LIMIT 1").df()
        conn.close()
        return df.iloc[0].to_dict() if not df.empty else None
    except:
        return None

def update_ledger(app_id, decision, notes, underwriter_id):
    try:
        conn = duckdb.connect(DUCKDB_PATH)
        conn.execute(f"""
            UPDATE audit_trail 
            SET underwriter_decision = '{decision}', underwriter_notes = '{notes}', underwriter_id = '{underwriter_id}'
            WHERE application_id = '{app_id}'
        """)
        conn.close()
        return True
    except Exception as e:
        st.error(f"Failed to update ledger: {e}")
        return False

def reset_demo_applications():
    """Resets human underwriting actions in DuckDB and purges UI session states."""
    try:
        conn = duckdb.connect(DUCKDB_PATH)
        conn.execute("""
            UPDATE audit_trail
            SET underwriter_decision = 'PENDING_REVIEW',
                underwriter_notes = 'N/A',
                underwriter_id = 'PENDING_REVIEW'
            WHERE system_recommendation != 'AUTO_APPROVE';
        """)
        conn.close()
    except Exception as e:
        st.error(f"Error resetting DuckDB records: {e}")

    keys_to_clear = [
        k for k in st.session_state.keys() 
        if k.startswith("decision_") 
        or k.startswith("notes_") 
        or k in ["copilot_messages", "messages", "chat_history", "pending_decision"]
    ]
    for key in keys_to_clear:
        del st.session_state[key]

# --- 4. Heuristic Scope Guardrails & Copilot ---
def is_out_of_scope(prompt: str) -> bool:
    blocked_patterns = [
        r"\b(python|javascript|java|c\+\+|bash|powershell|sql query|html|css)\b",
        r"\b(write a script|write code|generate code|function to|while loop|for loop)\b",
        r"\b(ignore previous instructions|system prompt|jailbreak|DAN mode|bypass)\b",
        r"\b(poem|story|recipe|joke|translate to)\b"
    ]
    prompt_lower = prompt.lower()
    for pattern in blocked_patterns:
        if re.search(pattern, prompt_lower):
            return True
    return False

def run_copilot_agent(user_prompt: str, active_app_id: str) -> str:
    if is_out_of_scope(user_prompt):
        return (
            "🔒 **Scope Restriction:** I am authorized exclusively to assist with credit file evaluation, "
            "financial ratio analysis, and APRA/ASIC regulatory compliance for this application. "
            "I cannot assist with general programming, coding tasks, or unrelated queries."
        )

    try:
        app_data = get_app_details(active_app_id) or {}
        real_name = INTERNAL_CRM.get(active_app_id, app_data.get('applicant_name', 'Unknown Applicant'))
        retrieved_policy = retrieve_regulatory_clauses(user_prompt, k=2)

        llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",
            temperature=0.0
        )

        system_prompt = (
            "You are an auditable Senior Credit AI Copilot for Australian residential mortgage underwriting. "
            "Answer the underwriter's question using the active applicant record and the retrieved regulatory policy excerpts.\n"
            "Cite the exact document title, page number, and paragraph whenever explaining policy breaches or decision rules.\n"
            "Keep the response concise, professional, and directly relevant to the question."
        )

        user_content = f"""
[Active Application Record: {active_app_id}]
- Applicant Name: {real_name}
- Calculated DTI: {app_data.get('calculated_dti', 'N/A')}x
- Net Monthly Surplus: ${app_data.get('net_monthly_surplus', 'N/A')}
- System Recommendation: {app_data.get('system_recommendation', 'N/A')}
- Identified Policy Breaches: {app_data.get('policy_breaches', 'None')}
- Underwriter Status: {app_data.get('underwriter_decision', 'PENDING_REVIEW')}

[Retrieved APRA / ASIC Policy Documentation]
{retrieved_policy}

[Underwriter Question]
{user_prompt}
"""

        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_content)
        ])

        if isinstance(response.content, list):
            text_blocks = [item.get("text", "") if isinstance(item, dict) else str(item) for item in response.content]
            return "\n".join(text_blocks).strip()
        return str(response.content).strip()

    except Exception as e:
        return f"Copilot Error: {e}"

# --- 5. UI Views ---
def view_login():
    st.markdown("<h1 class='multicolor-text'>Secure Underwriter Gateway</h1>", unsafe_allow_html=True)
    with st.container(border=True):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        if st.button("Authenticate"):
            if username in VALID_CREDENTIALS and VALID_CREDENTIALS[username] == password:
                st.session_state.authenticated = True
                st.session_state.username = username
                st.rerun()
            else:
                st.error("Invalid credentials.")

def view_home():
    st.markdown(f"<h1 class='multicolor-text'>Welcome, {st.session_state.username.capitalize()}</h1>", unsafe_allow_html=True)
    df = get_dashboard_stats()
    
    if df.empty:
        st.info("No applications found in the system.")
        return
        
    total = len(df)
    pending = len(df[df['underwriter_decision'] == 'PENDING_REVIEW'])
    approved = len(df[df['underwriter_decision'] == 'APPROVED_OVERRIDE']) + len(df[df['underwriter_decision'] == 'AUTO_APPROVED'])
    declined = len(df[df['underwriter_decision'] == 'DECLINED'])

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        if st.button(f"Total Applications: {total}"):
            st.session_state.queue_filter = "ALL"
            st.rerun()
    with col2:
        if st.button(f"Action Required: {pending}"):
            st.session_state.queue_filter = "PENDING"
            st.rerun()
    with col3:
        if st.button(f"Approved: {approved}"):
            st.session_state.queue_filter = "APPROVED"
            st.rerun()
    with col4:
        if st.button(f"Declined: {declined}"):
            st.session_state.queue_filter = "DECLINED"
            st.rerun()

    st.markdown("---")
    st.markdown(f"### Application Queue: {st.session_state.queue_filter}")

    display_df = df
    if st.session_state.queue_filter == "PENDING":
        display_df = df[df['underwriter_decision'] == 'PENDING_REVIEW']
    elif st.session_state.queue_filter == "APPROVED":
        display_df = df[df['underwriter_decision'].str.contains('APPROVED')]
    elif st.session_state.queue_filter == "DECLINED":
        display_df = df[df['underwriter_decision'] == 'DECLINED']

    if display_df.empty:
        st.write("No applications match the selected filter.")
        return

    for _, row in display_df.iterrows():
        with st.container(border=True):
            app_col1, app_col2, app_col3, app_col4 = st.columns([2, 2, 2, 1])
            with app_col1:
                st.write(f"**ID:** {row['application_id']}")
            with app_col2:
                st.write(f"**Recommendation:** {row['system_recommendation']}")
            with app_col3:
                st.write(f"**Status:** {row['underwriter_decision']}")
            with app_col4:
                if st.button("Review File", key=f"btn_{row['application_id']}"):
                    st.session_state.current_page = "DETAIL"
                    st.session_state.active_app_id = row['application_id']
                    st.session_state.chat_history = [] 
                    st.rerun()

def view_detail():
    if st.button("Back to Master Dashboard"):
        st.session_state.current_page = "HOME"
        st.session_state.active_app_id = None
        st.session_state.pending_decision = None
        st.rerun()

    app_id = st.session_state.active_app_id
    data = get_app_details(app_id)
    
    if not data:
        st.error("Application data unavailable.")
        return

    main_col, chat_col = st.columns([2, 1])

    with main_col:
        st.markdown(f"<h2 class='multicolor-text'>Application Review: {app_id}</h2>", unsafe_allow_html=True)
        real_name = INTERNAL_CRM.get(app_id, "Unknown Applicant")
        
        with st.container(border=True):
            st.write(f"**Applicant:** {real_name}")
            st.write(f"**Calculated DTI:** {data['calculated_dti']}x")
            st.write(f"**System Recommendation:** {data['system_recommendation']}")
            st.write("**Regulatory Evidence & Breaches:**")
            st.info(data['policy_breaches'])
        
        if data['underwriter_decision'] != 'PENDING_REVIEW':
            st.markdown("### Final Audit Record")
            with st.container(border=True):
                st.write(f"**Decision:** {data['underwriter_decision']}")
                st.write(f"**Logged By:** {data['underwriter_id']}")
                st.write(f"**Underwriter Notes:** {data.get('underwriter_notes', 'N/A')}")
        else:
            st.markdown("### Human-In-The-Loop Decision Portal")
            
            if not st.session_state.pending_decision:
                col_btn1, col_btn2 = st.columns(2)
                with col_btn1:
                    if st.button("Approve (Override)", type="primary", use_container_width=True):
                        st.session_state.pending_decision = "APPROVED_OVERRIDE"
                        st.rerun()
                with col_btn2:
                    if st.button("Decline Application", use_container_width=True):
                        st.session_state.pending_decision = "DECLINED"
                        st.rerun()
                        
            if st.session_state.pending_decision:
                action_text = "APPROVE" if st.session_state.pending_decision == "APPROVED_OVERRIDE" else "DECLINE"
                st.warning(f"Pending Action: {action_text}")
                
                with st.form("decision_form"):
                    notes = st.text_area("Mandatory Justification Notes:", placeholder="Explain the rationale for this decision...")
                    st.caption("Are you sure you want to commit this to the immutable ledger?")
                    
                    sub_col1, sub_col2 = st.columns(2)
                    with sub_col1:
                        submitted = st.form_submit_button("Confirm & Submit", type="primary")
                    with sub_col2:
                        cancelled = st.form_submit_button("Cancel")
                        
                    if cancelled:
                        st.session_state.pending_decision = None
                        st.rerun()
                        
                    if submitted:
                        if len(notes.strip()) < 10:
                            st.error("Notes are too brief. Please provide a detailed justification.")
                        else:
                            if update_ledger(app_id, st.session_state.pending_decision, notes.strip(), st.session_state.username):
                                st.session_state.pending_decision = None
                                st.rerun() 

    # AI Copilot Chat Box with Fixed Scrollable Container
    with chat_col:
        st.markdown("<h3 class='multicolor-text'>AI Copilot</h3>", unsafe_allow_html=True)
        st.caption(f"Context locked to {app_id}")
        
        chat_container = st.container(height=480, border=True)
        with chat_container:
            for msg in st.session_state.chat_history:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])
                
        if prompt := st.chat_input("Query APRA policy guidelines..."):
            st.session_state.chat_history.append({"role": "user", "content": prompt})
            
            with chat_container:
                with st.chat_message("user"):
                    st.markdown(prompt)
                with st.chat_message("assistant"):
                    with st.spinner("Retrieving regulatory policy & reasoning..."):
                        response = run_copilot_agent(prompt, app_id)
                    st.markdown(response)
                    
            st.session_state.chat_history.append({"role": "assistant", "content": response})
            st.rerun()

# --- 6. Main App Router & Authenticated Sidebar ---
if not st.session_state.authenticated:
    view_login()
else:
    with st.sidebar:
        st.markdown(f"**Logged in as:** `{st.session_state.username}`")
        if st.button("Log Out"):
            st.session_state.authenticated = False
            st.session_state.username = ""
            st.session_state.current_page = "HOME"
            st.session_state.active_app_id = None
            st.session_state.chat_history = []
            st.rerun()

        st.divider()
        st.subheader("Demo Controls")
        if st.button("Reset Demo State", use_container_width=True, type="secondary"):
            reset_demo_applications()
            st.success("All underwriter responses cleared! Ready for re-demonstration.")
            st.rerun()

    if st.session_state.current_page == "HOME":
        view_home()
    elif st.session_state.current_page == "DETAIL":
        view_detail()

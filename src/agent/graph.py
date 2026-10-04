import os
import sys
import warnings
import logging
import json
import duckdb
from typing import Dict, Any

from langgraph.graph import StateGraph, START, END
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage
from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine

from src.agent.state import UnderwritingState
from src.tools.mcp_financial_server import calculate_dti, evaluate_expenses, calculate_serviceability
from src.rag.vector_store import retrieve_regulatory_clauses

# --- 1. Global Suppressors (Clean Terminal) ---
warnings.filterwarnings("ignore")
os.environ["GRPC_VERBOSITY"] = "ERROR"
os.environ["GLOG_minloglevel"] = "2"
logging.getLogger("google").setLevel(logging.ERROR)
logging.getLogger("langchain_google_genai").setLevel(logging.ERROR)

# --- 2. Initialize Local PII Masking ---
analyzer = AnalyzerEngine()
anonymizer = AnonymizerEngine()

# --- 3. Initialize Local Audit Ledger ---
DUCKDB_PATH = "data/audit_decisions.duckdb"

def init_duckdb():
    conn = duckdb.connect(DUCKDB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_trail (
            application_id VARCHAR,
            applicant_name VARCHAR,
            system_recommendation VARCHAR,
            calculated_dti DOUBLE,
            net_monthly_surplus DOUBLE,
            policy_breaches VARCHAR,
            underwriter_id VARCHAR,
            underwriter_decision VARCHAR,
            underwriter_notes VARCHAR,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.close()

init_duckdb()

# --- Node Implementations ---

def ingest_application(state: UnderwritingState) -> Dict[str, Any]:
    file_path = f"data/raw_cdr/{state['application_id']}.json"
    with open(file_path, "r") as f:
        data = json.load(f)
    
    # Ensure default fields exist even if missing from JSON
    if "applicant_notes" not in data:
        data["applicant_notes"] = "No additional qualitative context provided."
        
    return {**data, "policy_breaches": [], "audit_committed": False}

def anonymize_data(state: UnderwritingState) -> Dict[str, Any]:
    # Client-side interception of the name before it hits external APIs
    text_to_analyze = f"The applicant's name is {state['applicant_name']}."
    
    analyzer_results = analyzer.analyze(
        text=text_to_analyze, 
        entities=["PERSON"], 
        language="en"
    )
    
    anonymized_result = anonymizer.anonymize(
        text=text_to_analyze, 
        analyzer_results=analyzer_results
    )
    
    masked_name = anonymized_result.text.replace("The applicant's name is ", "").replace(".", "").strip()
    
    if state['applicant_name'] in masked_name:
        masked_name = "[REDACTED_PERSON]"
        
    return {"applicant_name": masked_name}

def calculate_financials(state: UnderwritingState) -> Dict[str, Any]:
    dti = calculate_dti(
        gross_annual_income=state["gross_annual_income"],
        requested_loan_amount=state["requested_loan_amount"],
        existing_monthly_debts=state["existing_debts_monthly"]
    )
    
    expense = evaluate_expenses(
        declared_monthly_expenses=state["declared_living_expenses"],
        cdr_actual_monthly_expenses=state["cdr_verified_expenses_monthly"]
    )
    
    serviceability = calculate_serviceability(
        gross_annual_income=state["gross_annual_income"],
        applicable_monthly_expense=expense["applicable_monthly_expense"],
        existing_monthly_debts=state["existing_debts_monthly"],
        requested_loan_amount=state["requested_loan_amount"],
        base_interest_rate=state["base_interest_rate"]
    )
    
    return {
        "dti_result": dti,
        "expense_result": expense,
        "serviceability_result": serviceability
    }

def evaluate_policy_guardrails(state: UnderwritingState) -> Dict[str, Any]:
    breaches = []
    
    if state["dti_result"]["status"] == "BREACH":
        breaches.append(f"DTI Breach: {state['dti_result']['calculated_dti']}x (Ceiling: {state['dti_result']['apra_limit']}x)")
        
    if state["expense_result"]["status"] == "BREACH":
        breaches.append(f"Living Expense Discrepancy: Declared ${state['declared_living_expenses']} vs Actual ${state['cdr_verified_expenses_monthly']}")
        
    if state["serviceability_result"]["status"] == "BREACH":
        breaches.append(f"Net Serviceability Deficit: Stressed surplus is -${abs(state['serviceability_result']['net_monthly_surplus'])}/mo at stressed rate of {state['serviceability_result']['stressed_interest_rate']*100:.2f}%")
        
    recommendation = "ESCALATED_REFERRAL" if breaches else "AUTO_APPROVE"
    
    return {
        "policy_breaches": breaches,
        "system_recommendation": recommendation
    }

def router_condition(state: UnderwritingState) -> str:
    if state["policy_breaches"]:
        return "retrieve_rag_context"
    return "auto_approve_node"

def retrieve_rag_context(state: UnderwritingState) -> Dict[str, Any]:
    query = " ".join(state["policy_breaches"])
    citations = retrieve_regulatory_clauses(query, k=2)
    return {"regulatory_citations": citations}

# def summarize_applicant_notes(state: UnderwritingState) -> Dict[str, Any]:
#     # TEMPERATURE 0.3: Allows minor natural language variation for readability
#     llm = ChatGoogleGenerativeAI(model="gemini-3.6-flash", temperature=0.3)
    
#     prompt = f"""
#     You are an underwriting assistant. Summarize the following applicant notes into a brief, 
#     professional 2-sentence narrative context for a credit officer. Focus purely on facts.
    
#     Applicant Notes: {state['applicant_notes']}
#     """
    
#     response = llm.invoke([HumanMessage(content=prompt)])
    
#     if isinstance(response.content, list):
#         summary = "".join([block.get("text", "") if isinstance(block, dict) else str(block) for block in response.content])
#     else:
#         summary = str(response.content)
        
#     return {"applicant_narrative_summary": summary.strip()}

# def synthesize_risk_memo(state: UnderwritingState) -> Dict[str, Any]:
#     # TEMPERATURE 0.0: Strictly locked for deterministic policy extraction and formatting
#     llm = ChatGoogleGenerativeAI(model="gemini-3.6-flash", temperature=0.0)
    
#     prompt = f"""
#     You are a Senior Risk Officer at an Australian ADI.
#     Draft a concise, auditable Credit Memorandum for Application ID: {state['application_id']}.
    
#     Applicant: {state['applicant_name']}
#     Gross Annual Income: ${state['gross_annual_income']}
#     Loan Requested: ${state['requested_loan_amount']}
    
#     Narrative Context:
#     {state.get('applicant_narrative_summary', 'None provided.')}
    
#     Deterministic Metrics:
#     - Debt-to-Income (DTI): {state['dti_result']['calculated_dti']}x
#     - Stressed Monthly Repayment: ${state['serviceability_result']['stressed_monthly_repayment']}
#     - Stressed Monthly Net Surplus: ${state['serviceability_result']['net_monthly_surplus']}
    
#     Identified Breaches:
#     {chr(10).join(state['policy_breaches'])}
    
#     Regulatory Citations (Grounding Context):
#     {state['regulatory_citations']}
    
#     Summarize the risks, reference the exact APRA/ASIC citations, and recommend why this file must be halted for human underwriter review.
#     Keep the output under 150 words.
#     """
    
#     response = llm.invoke([
#         SystemMessage(content="You are an expert Australian banking credit risk officer."),
#         HumanMessage(content=prompt)
#     ])
    
#     if isinstance(response.content, list):
#         memo_text = "".join([block.get("text", "") if isinstance(block, dict) else str(block) for block in response.content])
#     else:
#         memo_text = str(response.content)

#     return {"ai_risk_memo": memo_text.strip()}

def summarize_applicant_notes(state: UnderwritingState) -> Dict[str, Any]:
    llm = ChatGoogleGenerativeAI(model="gemini-3.6-flash", temperature=0.3)
    
    # 1. Defensively extract the notes with a safe default fallback
    raw_notes = state.get('applicant_notes', 'No additional qualitative context provided.')
    
    prompt = f"""
    You are an underwriting assistant. Summarize the following applicant notes into a brief, 
    professional 2-sentence narrative context for a credit officer. Focus purely on facts.
    
    Applicant Notes: {raw_notes}
    """
    
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        
        if isinstance(response.content, list):
            summary = "".join([block.get("text", "") if isinstance(block, dict) else str(block) for block in response.content])
        else:
            summary = str(response.content)
            
    except Exception as e:
        print(f"\n[SYSTEM WARNING] Summarization API failed: {e}")
        summary = "Automated summary unavailable due to system timeout. Please refer to raw applicant notes."
        
    return {"applicant_narrative_summary": summary.strip()}


def synthesize_risk_memo(state: UnderwritingState) -> Dict[str, Any]:
    llm = ChatGoogleGenerativeAI(model="gemini-3.6-flash", temperature=0.0)
    
    prompt = f"""
    You are a Senior Risk Officer at an Australian ADI.
    Draft a concise, auditable Credit Memorandum for Application ID: {state['application_id']}.
    
    Applicant: {state['applicant_name']}
    Gross Annual Income: ${state['gross_annual_income']}
    Loan Requested: ${state['requested_loan_amount']}
    
    Narrative Context:
    {state.get('applicant_narrative_summary', 'None provided.')}
    
    Deterministic Metrics:
    - Debt-to-Income (DTI): {state['dti_result']['calculated_dti']}x
    - Stressed Monthly Repayment: ${state['serviceability_result']['stressed_monthly_repayment']}
    - Stressed Monthly Net Surplus: ${state['serviceability_result']['net_monthly_surplus']}
    
    Identified Breaches:
    {chr(10).join(state['policy_breaches'])}
    
    Regulatory Citations (Grounding Context):
    {state['regulatory_citations']}
    
    Summarize the risks, reference the exact APRA/ASIC citations, and recommend why this file must be halted for human underwriter review.
    Keep the output under 150 words.
    """
    
    try:
        response = llm.invoke([
            SystemMessage(content="You are an expert Australian banking credit risk officer."),
            HumanMessage(content=prompt)
        ])
        
        if isinstance(response.content, list):
            memo_text = "".join([block.get("text", "") if isinstance(block, dict) else str(block) for block in response.content])
        else:
            memo_text = str(response.content)
            
    except Exception as e:
        # Critical failure handling: Force a manual review if the memo cannot be drafted
        print(f"\n[CRITICAL ERROR] Risk Memo API failed: {e}")
        memo_text = "**SYSTEM FAILURE**: Unable to generate AI Credit Memo due to API error. MANDATORY MANUAL UNDERWRITER REVIEW REQUIRED."

    return {"ai_risk_memo": memo_text.strip()}


def auto_approve_node(state: UnderwritingState) -> Dict[str, Any]:
    return {
        "ai_risk_memo": f"Application {state['application_id']} meets all APRA and ASIC credit policies. Clean auto-approval granted.",
        "underwriter_id": "SYSTEM_AUTO_EXECUTION",
        "underwriter_decision": "AUTO_APPROVED",
        "underwriter_notes": "All ratios within mandated thresholds."
    }

def commit_audit_trail(state: UnderwritingState) -> Dict[str, Any]:
    conn = duckdb.connect(DUCKDB_PATH)
    conn.execute("""
        INSERT INTO audit_trail (
            application_id, applicant_name, system_recommendation,
            calculated_dti, net_monthly_surplus, policy_breaches,
            underwriter_id, underwriter_decision, underwriter_notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        state["application_id"],
        state["applicant_name"],
        state["system_recommendation"],
        state["dti_result"]["calculated_dti"],
        state["serviceability_result"]["net_monthly_surplus"],
        "; ".join(state["policy_breaches"]) if state["policy_breaches"] else "NONE",
        state.get("underwriter_id", "PENDING_REVIEW"),
        state.get("underwriter_decision", "PENDING_REVIEW"),
        state.get("underwriter_notes", "N/A")
    ))
    conn.close()
    return {"audit_committed": True}

# --- Graph Assembly ---

workflow = StateGraph(UnderwritingState)

workflow.add_node("ingest", ingest_application)
workflow.add_node("anonymize", anonymize_data) 
workflow.add_node("calculate", calculate_financials)
workflow.add_node("evaluate", evaluate_policy_guardrails)
workflow.add_node("rag", retrieve_rag_context)
workflow.add_node("summarize", summarize_applicant_notes)
workflow.add_node("memo", synthesize_risk_memo)
workflow.add_node("auto_approve", auto_approve_node)
workflow.add_node("audit", commit_audit_trail)

# Define the exact linear tracks and conditional switches
workflow.add_edge(START, "ingest")
workflow.add_edge("ingest", "anonymize")
workflow.add_edge("anonymize", "calculate")
workflow.add_edge("calculate", "evaluate")

workflow.add_conditional_edges(
    "evaluate",
    router_condition,
    {
        "retrieve_rag_context": "rag",
        "auto_approve_node": "auto_approve"
    }
)

# The sequence for breached applications
workflow.add_edge("rag", "summarize")
workflow.add_edge("summarize", "memo")
workflow.add_edge("memo", "audit")

# The sequence for clean applications
workflow.add_edge("auto_approve", "audit")
workflow.add_edge("audit", END)

underwriting_graph = workflow.compile()

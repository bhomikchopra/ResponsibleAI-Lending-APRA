from typing import TypedDict, List, Dict, Any, Optional

class UnderwritingState(TypedDict):
    # Ingestion Data
    application_id: str
    applicant_name: str
    gross_annual_income: float
    declared_living_expenses: float
    requested_loan_amount: float
    property_valuation: float
    base_interest_rate: float
    existing_debts_monthly: float
    cdr_verified_expenses_monthly: float
    
    # FastMCP Calculated Outputs
    dti_result: Optional[Dict[str, Any]]
    expense_result: Optional[Dict[str, Any]]
    serviceability_result: Optional[Dict[str, Any]]
    
    # Evaluation Flags & Citations
    policy_breaches: List[str]
    regulatory_citations: Optional[str]
    
    # Underwriting Outputs
    ai_risk_memo: Optional[str]
    system_recommendation: Optional[str]  # "AUTO_APPROVE" | "ESCALATED_REFERRAL"
    
    # Human-In-The-Loop (HITL) Fields
    underwriter_id: Optional[str]
    underwriter_decision: Optional[str]   # "APPROVED_WITH_EXCEPTION" | "DECLINED" | "INFO_REQUESTED"
    underwriter_notes: Optional[str]
    
    # Audit & Observability
    audit_committed: bool
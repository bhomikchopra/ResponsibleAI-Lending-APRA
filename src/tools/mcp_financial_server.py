from fastmcp import FastMCP

# Initialize the FastMCP server
mcp = FastMCP("Australian Underwriting Calculator")

# Australian Regulatory Constants
APRA_SERVICEABILITY_BUFFER = 0.0300  # +3.00% mandated by APRA
APRA_DTI_SOFT_LIMIT = 6.00
HEM_BASELINE_BENCHMARK = 2420        # Household Expenditure Measure (Approximate single adult)

@mcp.tool
def calculate_dti(gross_annual_income: float, requested_loan_amount: float, existing_monthly_debts: float) -> dict:
    """
    Calculates the Debt-to-Income (DTI) ratio.
    Total debt includes the requested loan amount plus capitalized existing debts (assumed 5-year payoff for simplicity).
    """
    # Convert monthly existing debt obligations to an approximate principal total
    existing_principal_approx = existing_monthly_debts * 12 * 5 
    total_debt = requested_loan_amount + existing_principal_approx
    
    dti = total_debt / gross_annual_income
    
    return {
        "calculated_dti": round(dti, 2),
        "apra_limit": APRA_DTI_SOFT_LIMIT,
        "status": "BREACH" if dti >= APRA_DTI_SOFT_LIMIT else "PASS"
    }

@mcp.tool
def evaluate_expenses(declared_monthly_expenses: float, cdr_actual_monthly_expenses: float) -> dict:
    """
    Evaluates stated living expenses against Open Banking actuals and the HEM benchmark.
    Returns the higher of the three to be used for serviceability checks.
    """
    applicable_expense = max(declared_monthly_expenses, cdr_actual_monthly_expenses, HEM_BASELINE_BENCHMARK)
    delta = cdr_actual_monthly_expenses - declared_monthly_expenses
    
    return {
        "applicable_monthly_expense": applicable_expense,
        "hem_benchmark": HEM_BASELINE_BENCHMARK,
        "expense_discrepancy": round(delta, 2),
        "status": "BREACH" if delta > 500 else "PASS"
    }

@mcp.tool
def calculate_serviceability(gross_annual_income: float, applicable_monthly_expense: float, existing_monthly_debts: float, requested_loan_amount: float, base_interest_rate: float) -> dict:
    """
    Calculates net monthly surplus using the APRA +3.00% interest rate stress buffer.
    """
    monthly_net_income = (gross_annual_income * 0.70) / 12  # Simplified 30% tax bracket
    stressed_rate = base_interest_rate + APRA_SERVICEABILITY_BUFFER
    
    # Amortization formula: M = P[r(1+r)^n/((1+r)^n)-1] for a 30-year loan (360 months)
    monthly_rate = stressed_rate / 12
    months = 360
    stressed_monthly_repayment = requested_loan_amount * (monthly_rate * (1 + monthly_rate)**months) / ((1 + monthly_rate)**months - 1)
    
    net_surplus = monthly_net_income - applicable_monthly_expense - existing_monthly_debts - stressed_monthly_repayment
    
    return {
        "stressed_interest_rate": round(stressed_rate, 4),
        "stressed_monthly_repayment": round(stressed_monthly_repayment, 2),
        "net_monthly_surplus": round(net_surplus, 2),
        "status": "BREACH" if net_surplus < 0 else "PASS"
    }

if __name__ == "__main__":
    # Allows the server to be run independently using standard IO
    mcp.run()
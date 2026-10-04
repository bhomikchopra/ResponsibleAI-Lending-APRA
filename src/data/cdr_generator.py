import json
import os

# Ensure directory exists
os.makedirs("data/raw_cdr", exist_ok=True)

# Shared base schema fields with customized persona variations
personas = [
    {
        # Persona 1: Prime Borrower (Auto-Approve)
        "application_id": "APP-101",
        "applicant_name": "Sarah Jenkins",
        "gross_annual_income": 165000,
        "declared_living_expenses": 2500,
        "requested_loan_amount": 600000,
        "property_valuation": 850000,
        "base_interest_rate": 0.0635,
        "existing_debts_monthly": 0,
        "cdr_verified_expenses_monthly": 2450 # Matches declared roughly
    },
    {
        # Persona 2: High DTI Edge Case (Breaches 6.00x APRA limit)
        "application_id": "APP-102",
        "applicant_name": "Rajiv Patel",
        "gross_annual_income": 140000,
        "declared_living_expenses": 1800,
        "requested_loan_amount": 850000,
        "property_valuation": 1060000,
        "base_interest_rate": 0.0635,
        "existing_debts_monthly": 1500, # Large car/personal loan pushing total debt up
        "cdr_verified_expenses_monthly": 1850
    },
    {
        # Persona 3: Expense Concealment (Triggers ASIC RG 209)
        "application_id": "APP-103",
        "applicant_name": "Alex Chen",
        "gross_annual_income": 150000,
        "declared_living_expenses": 1600,
        "requested_loan_amount": 700000,
        "property_valuation": 900000,
        "base_interest_rate": 0.0635,
        "existing_debts_monthly": 0,
        "cdr_verified_expenses_monthly": 3450 # Major discrepancy vs declared
    },
    {
        # Persona 4: Stressed Deficit (Fails APRA +3.00% Buffer)
        "application_id": "APP-104",
        "applicant_name": "Emma Wright",
        "gross_annual_income": 95000,
        "declared_living_expenses": 2400,
        "requested_loan_amount": 680000,
        "property_valuation": 750000,
        "base_interest_rate": 0.0635,
        "existing_debts_monthly": 800,
        "cdr_verified_expenses_monthly": 2500
    }
]

for persona in personas:
    file_path = f"data/raw_cdr/{persona['application_id']}.json"
    with open(file_path, "w") as f:
        json.dump(persona, f, indent=4)
        print(f"Generated: {file_path}")

print("All CDR mock payloads generated successfully.")
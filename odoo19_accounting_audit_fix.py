#!/usr/bin/env python3
"""
Odoo 19 Accounting Setup Auditor / Fixer
Target: UAE Accounting Localization + core accounting setup

Usage:
  export ODOO_URL="https://redoxyae.odoo.com"
  export ODOO_DB="redoxyae"
  export ODOO_USER="remiz@redoxyksa.com"
  export ODOO_API_KEY="YOUR_API_KEY"

  python3 odoo19_accounting_audit_fix.py
  python3 odoo19_accounting_audit_fix.py --apply
"""

import argparse
import logging
import os
import sys
import xmlrpc.client


# -----------------------------
# Logging
# -----------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("odoo-accounting-audit")


# -----------------------------
# Config
# -----------------------------

URL = os.getenv("ODOO_URL", "https://redoxyae.odoo.com")
DB = os.getenv("ODOO_DB", "redoxyae")
USERNAME = os.getenv("ODOO_USER", "remiz@redoxyksa.com")
PASSWORD = os.getenv("ODOO_API_KEY")

UAE_LOCALIZATION_MODULES = [
    "l10n_ae",
    "account",
]

REQUIRED_JOURNALS = [
    {"name": "Sales", "code": "INV", "type": "sale"},
    {"name": "Purchases", "code": "BILL", "type": "purchase"},
    {"name": "Bank", "code": "BNK1", "type": "bank"},
    {"name": "Cash", "code": "CSH1", "type": "cash"},
    {"name": "Miscellaneous Operations", "code": "MISC", "type": "general"},
]

REQUIRED_ACCOUNT_TYPES = {
    "asset_receivable": "Receivable account",
    "liability_payable": "Payable account",
    "asset_cash": "Bank/Cash account",
    "liability_current": "Tax payable / current liability account",
    "income": "Income account",
    "expense": "Expense account",
}


# -----------------------------
# XML-RPC Client
# -----------------------------

class OdooRPC:
    def __init__(self, url, db, username, password, dry_run=True):
        if not password:
            raise RuntimeError("Missing ODOO_API_KEY environment variable.")

        self.url = url.rstrip("/")
        self.db = db
        self.username = username
        self.password = password
        self.dry_run = dry_run

        self.common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common", allow_none=True)
        self.models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object", allow_none=True)
        self.uid = None

    def connect(self):
        log.info("Authenticating to %s / database %s as %s", self.url, self.db, self.username)
        self.uid = self.common.authenticate(self.db, self.username, self.password, {})
        if not self.uid:
            raise RuntimeError("Authentication failed. Check DB, username, and API key.")
        log.info("Authenticated successfully. UID=%s", self.uid)

    def execute(self, model, method, args=None, kwargs=None):
        args = args or []
        kwargs = kwargs or {}
        try:
            return self.models.execute_kw(
                self.db,
                self.uid,
                self.password,
                model,
                method,
                args,
                kwargs,
            )
        except xmlrpc.client.Fault as exc:
            log.error("RPC Fault on %s.%s: %s", model, method, exc)
            raise
        except Exception as exc:
            log.error("RPC Error on %s.%s: %s", model, method, exc)
            raise

    def fields(self, model):
        return self.execute(model, "fields_get", [], {"attributes": ["string", "type", "readonly"]})

    def search(self, model, domain, limit=None, order=None):
        kwargs = {}
        if limit:
            kwargs["limit"] = limit
        if order:
            kwargs["order"] = order
        return self.execute(model, "search", [domain], kwargs)

    def read(self, model, ids, fields=None):
        if not ids:
            return []
        kwargs = {}
        if fields:
            kwargs["fields"] = fields
        return self.execute(model, "read", [ids], kwargs)

    def search_read(self, model, domain, fields=None, limit=None, order=None):
        kwargs = {}
        if fields:
            kwargs["fields"] = fields
        if limit:
            kwargs["limit"] = limit
        if order:
            kwargs["order"] = order
        return self.execute(model, "search_read", [domain], kwargs)

    def create(self, model, vals):
        if self.dry_run:
            log.warning("[DRY-RUN] Would create %s: %s", model, vals)
            return None
        log.info("Creating %s: %s", model, vals)
        return self.execute(model, "create", [vals])

    def write(self, model, ids, vals):
        if not ids:
            return False
        if self.dry_run:
            log.warning("[DRY-RUN] Would update %s ids=%s vals=%s", model, ids, vals)
            return True
        log.info("Updating %s ids=%s vals=%s", model, ids, vals)
        return self.execute(model, "write", [ids, vals])


# -----------------------------
# Helpers
# -----------------------------

def get_company(odoo):
    company_ids = odoo.search("res.company", [], limit=1)
    if not company_ids:
        raise RuntimeError("No company found.")
    fields = [
        "name",
        "country_id",
        "currency_id",
        "chart_template",
        "account_fiscal_country_id",
        "fiscalyear_last_day",
        "fiscalyear_last_month",
        "account_sale_tax_id",
        "account_purchase_tax_id",
    ]
    existing_fields = odoo.fields("res.company")
    fields = [field for field in fields if field in existing_fields]
    return odoo.read("res.company", company_ids, fields)[0]


def get_currency(odoo, name):
    ids = odoo.search("res.currency", [("name", "=", name)], limit=1)
    return ids[0] if ids else None


def get_country(odoo, code):
    ids = odoo.search("res.country", [("code", "=", code)], limit=1)
    return ids[0] if ids else None


def model_exists(odoo, model_name):
    ids = odoo.search("ir.model", [("model", "=", model_name)], limit=1)
    return bool(ids)


def get_installed_module(odoo, module_name):
    res = odoo.search_read(
        "ir.module.module",
        [("name", "=", module_name)],
        ["name", "state"],
        limit=1,
    )
    return res[0] if res else None


def install_module_if_needed(odoo, module_name, issues, updates):
    module = get_installed_module(odoo, module_name)
    if not module:
        issues.append(f"Module {module_name} is not available on this database.")
        return

    if module["state"] != "installed":
        issues.append(f"Module {module_name} is not installed. Current state: {module['state']}")
        if odoo.dry_run:
            updates.append(f"[DRY-RUN] Would install module {module_name}.")
        else:
            log.info("Installing module %s", module_name)
            odoo.execute("ir.module.module", "button_immediate_install", [[module["id"]]])
            updates.append(f"Installed module {module_name}.")
    else:
        updates.append(f"Verified module {module_name} is installed.")


def find_tax(odoo, company_id, amount=5.0, tax_type="sale"):
    domain = [
        ("company_id", "in", [False, company_id]),
        ("amount", "=", amount),
        ("type_tax_use", "=", tax_type),
        ("active", "=", True),
    ]
    taxes = odoo.search_read(
        "account.tax",
        domain,
        ["name", "amount", "type_tax_use", "invoice_repartition_line_ids", "refund_repartition_line_ids"],
        limit=10,
    )
    return taxes


def find_account_by_type(odoo, company_id, account_type):
    return odoo.search_read(
        "account.account",
        [
            ("company_ids", "in", [company_id]),
            ("account_type", "=", account_type),
            ("deprecated", "=", False),
        ],
        ["code", "name", "account_type"],
        limit=5,
        order="code asc",
    )


def get_or_create_account(odoo, company_id, code, name, account_type, issues, updates):
    existing = odoo.search_read(
        "account.account",
        [
            ("code", "=", code),
            ("company_ids", "in", [company_id]),
        ],
        ["code", "name", "account_type"],
        limit=1,
    )
    if existing:
        return existing[0]["id"]

    issues.append(f"Missing account {code} - {name}.")
    vals = {
        "code": code,
        "name": name,
        "account_type": account_type,
        "company_ids": [(6, 0, [company_id])],
    }
    new_id = odoo.create("account.account", vals)
    updates.append(f"Created account {code} - {name}." if new_id else f"[DRY-RUN] Would create account {code} - {name}.")
    return new_id


def ensure_journal(odoo, company_id, spec, default_account_id, issues, updates):
    journals = odoo.search_read(
        "account.journal",
        [
            ("company_id", "=", company_id),
            ("type", "=", spec["type"]),
            "|",
            ("code", "=", spec["code"]),
            ("name", "=", spec["name"]),
        ],
        ["name", "code", "type", "default_account_id"],
        limit=1,
    )

    vals = {
        "name": spec["name"],
        "code": spec["code"],
        "type": spec["type"],
        "company_id": company_id,
    }

    journal_fields = odoo.fields("account.journal")
    if default_account_id and "default_account_id" in journal_fields:
        vals["default_account_id"] = default_account_id

    if journals:
        journal = journals[0]
        write_vals = {}
        if journal.get("code") != spec["code"]:
            write_vals["code"] = spec["code"]
        if not journal.get("default_account_id") and default_account_id and "default_account_id" in journal_fields:
            write_vals["default_account_id"] = default_account_id

        if write_vals:
            issues.append(f"Journal {spec['name']} exists but needs correction: {write_vals}")
            odoo.write("account.journal", [journal["id"]], write_vals)
            updates.append(f"Updated journal {spec['name']}.")
        else:
            updates.append(f"Verified journal {spec['name']}.")
        return journal["id"]

    issues.append(f"Missing journal {spec['name']} / {spec['code']}.")
    new_id = odoo.create("account.journal", vals)
    updates.append(f"Created journal {spec['name']}." if new_id else f"[DRY-RUN] Would create journal {spec['name']}.")
    return new_id


def set_company_defaults(odoo, company, issues, updates):
    company_id = company["id"]
    company_fields = odoo.fields("res.company")
    vals = {}

    ae_country_id = get_country(odoo, "AE")
    aed_currency_id = get_currency(odoo, "AED")

    if ae_country_id and "country_id" in company_fields:
        current_country = company.get("country_id")
        if not current_country or current_country[0] != ae_country_id:
            issues.append("Company country is not United Arab Emirates.")
            vals["country_id"] = ae_country_id

    if aed_currency_id and "currency_id" in company_fields:
        current_currency = company.get("currency_id")
        if not current_currency or current_currency[0] != aed_currency_id:
            issues.append("Company currency is not AED.")
            vals["currency_id"] = aed_currency_id

    if "fiscalyear_last_day" in company_fields and company.get("fiscalyear_last_day") != 31:
        vals["fiscalyear_last_day"] = 31
        issues.append("Fiscal year-end day is not 31.")

    if "fiscalyear_last_month" in company_fields and company.get("fiscalyear_last_month") != "12":
        vals["fiscalyear_last_month"] = "12"
        issues.append("Fiscal year-end month is not December.")

    if vals:
        odoo.write("res.company", [company_id], vals)
        updates.append(f"Updated company base settings: {vals}")
    else:
        updates.append("Verified company country, currency, and fiscal year settings.")


def apply_res_config_settings(odoo, company_id, sale_tax_id=None, purchase_tax_id=None, enable_multicurrency=True, issues=None, updates=None):
    issues = issues or []
    updates = updates or []

    if not model_exists(odoo, "res.config.settings"):
        issues.append("res.config.settings model not available.")
        return

    fields = odoo.fields("res.config.settings")
    vals = {}

    if "company_id" in fields:
        vals["company_id"] = company_id

    if enable_multicurrency:
        for field in ["group_multi_currency", "module_account_accountant"]:
            if field in fields:
                vals[field] = True

    if sale_tax_id:
        for field in ["sale_tax_id", "default_sale_tax_id"]:
            if field in fields:
                vals[field] = sale_tax_id
                break

    if purchase_tax_id:
        for field in ["purchase_tax_id", "default_purchase_tax_id"]:
            if field in fields:
                vals[field] = purchase_tax_id
                break

    if not vals:
        updates.append("No compatible res.config.settings fields found to update.")
        return

    wizard_id = odoo.create("res.config.settings", vals)
    if wizard_id:
        odoo.execute("res.config.settings", "execute", [[wizard_id]])
        updates.append(f"Applied res.config.settings wizard values: {vals}")
    else:
        updates.append(f"[DRY-RUN] Would apply res.config.settings wizard values: {vals}")


# -----------------------------
# Audit / Fix
# -----------------------------

def audit_and_fix(odoo):
    issues = []
    updates = []
    manual_steps = []

    company = get_company(odoo)
    company_id = company["id"]

    log.info("Auditing company: %s", company.get("name"))

    # 1. Verify/install modules
    for module_name in UAE_LOCALIZATION_MODULES:
        install_module_if_needed(odoo, module_name, issues, updates)

    # 2. Company base settings
    set_company_defaults(odoo, company, issues, updates)

    # Re-read company after possible updates
    company = get_company(odoo)

    # 3. Required account types
    found_accounts = {}
    for account_type, label in REQUIRED_ACCOUNT_TYPES.items():
        accounts = find_account_by_type(odoo, company_id, account_type)
        if not accounts:
            issues.append(f"Missing account type: {label} ({account_type}).")
        else:
            found_accounts[account_type] = accounts[0]["id"]
            updates.append(f"Verified {label}: {accounts[0]['code']} {accounts[0]['name']}")

    # Fallback accounts if required account types are missing
    found_accounts.get("asset_receivable") or get_or_create_account(
        odoo, company_id, "110100", "Trade Receivables", "asset_receivable", issues, updates
    )
    found_accounts.get("liability_payable") or get_or_create_account(
        odoo, company_id, "210100", "Trade Payables", "liability_payable", issues, updates
    )
    bank_cash_id = found_accounts.get("asset_cash") or get_or_create_account(
        odoo, company_id, "101000", "Bank and Cash", "asset_cash", issues, updates
    )
    tax_payable_id = found_accounts.get("liability_current") or get_or_create_account(
        odoo, company_id, "220500", "VAT Payable", "liability_current", issues, updates
    )
    income_id = found_accounts.get("income") or get_or_create_account(
        odoo, company_id, "400000", "Sales Revenue", "income", issues, updates
    )
    expense_id = found_accounts.get("expense") or get_or_create_account(
        odoo, company_id, "500000", "General Expenses", "expense", issues, updates
    )

    # 4. Taxes
    sale_taxes = find_tax(odoo, company_id, 5.0, "sale")
    purchase_taxes = find_tax(odoo, company_id, 5.0, "purchase")

    sale_tax_id = sale_taxes[0]["id"] if sale_taxes else None
    purchase_tax_id = purchase_taxes[0]["id"] if purchase_taxes else None

    if not sale_tax_id:
        issues.append("Missing UAE 5% VAT sales tax.")
        sale_tax_id = odoo.create("account.tax", {
            "name": "VAT 5% Sales",
            "amount": 5.0,
            "amount_type": "percent",
            "type_tax_use": "sale",
            "company_id": company_id,
        })
        updates.append("Created VAT 5% Sales tax." if sale_tax_id else "[DRY-RUN] Would create VAT 5% Sales tax.")
    else:
        updates.append(f"Verified UAE 5% sales VAT tax: {sale_taxes[0]['name']}")

    if not purchase_tax_id:
        issues.append("Missing UAE 5% VAT purchase tax.")
        purchase_tax_id = odoo.create("account.tax", {
            "name": "VAT 5% Purchases",
            "amount": 5.0,
            "amount_type": "percent",
            "type_tax_use": "purchase",
            "company_id": company_id,
        })
        updates.append("Created VAT 5% Purchases tax." if purchase_tax_id else "[DRY-RUN] Would create VAT 5% Purchases tax.")
    else:
        updates.append(f"Verified UAE 5% purchase VAT tax: {purchase_taxes[0]['name']}")

    # 5. Apply default taxes / multicurrency using settings wizard where possible
    apply_res_config_settings(
        odoo,
        company_id,
        sale_tax_id=sale_tax_id,
        purchase_tax_id=purchase_tax_id,
        enable_multicurrency=True,
        issues=issues,
        updates=updates,
    )

    # 6. Journals
    journal_account_map = {
        "sale": income_id,
        "purchase": expense_id,
        "bank": bank_cash_id,
        "cash": bank_cash_id,
        "general": tax_payable_id,
    }

    for spec in REQUIRED_JOURNALS:
        ensure_journal(
            odoo,
            company_id,
            spec,
            journal_account_map.get(spec["type"]),
            issues,
            updates,
        )

    # 7. Structural anomaly checks
    journals = odoo.search_read(
        "account.journal",
        [("company_id", "=", company_id)],
        ["name", "code", "type", "default_account_id"],
        limit=200,
    )

    for journal in journals:
        if journal["type"] in ["bank", "cash", "general"] and not journal.get("default_account_id"):
            issues.append(f"Journal missing default account: {journal['name']} ({journal['code']})")

    taxes = odoo.search_read(
        "account.tax",
        [("company_id", "in", [False, company_id]), ("active", "=", True)],
        ["name", "amount", "type_tax_use", "invoice_repartition_line_ids", "refund_repartition_line_ids"],
        limit=200,
    )

    for tax in taxes:
        if tax["amount"] == 5.0 and tax["type_tax_use"] in ["sale", "purchase"]:
            if not tax.get("invoice_repartition_line_ids"):
                issues.append(f"Tax has no invoice repartition lines: {tax['name']}")
            if not tax.get("refund_repartition_line_ids"):
                issues.append(f"Tax has no refund repartition lines: {tax['name']}")

    # 8. Manual steps
    manual_steps.extend([
        "Review UAE VAT report mapping before first VAT return submission.",
        "Confirm opening balances and post historical journal entries if migrating from another system.",
        "Set tax lock date, fiscal year lock date, and invoice lock date only after historical data is verified.",
        "Validate bank account details and bank feeds manually.",
        "Confirm TRN, legal company address, invoice layout, Arabic/English invoice requirements, and FTA compliance settings.",
        "Do not post live entries until the accountant signs off the chart of accounts and tax mappings.",
    ])

    return issues, updates, manual_steps


# -----------------------------
# Main
# -----------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Apply changes. Default is dry-run only.")
    args = parser.parse_args()

    dry_run = not args.apply

    if dry_run:
        log.warning("Running in DRY-RUN mode. No changes will be written.")
    else:
        log.warning("Running in APPLY mode. Changes may be written to Odoo.")

    odoo = OdooRPC(URL, DB, USERNAME, PASSWORD, dry_run=dry_run)
    odoo.connect()

    try:
        issues, updates, manual_steps = audit_and_fix(odoo)

        print("\n" + "=" * 80)
        print("ODOO 19 ACCOUNTING AUDIT SUMMARY")
        print("=" * 80)

        print("\nISSUES IDENTIFIED")
        print("-" * 80)
        if issues:
            for item in issues:
                print(f"- {item}")
        else:
            print("- No critical issues identified.")

        print("\nUPDATES / PROPOSED UPDATES")
        print("-" * 80)
        if updates:
            for item in updates:
                print(f"- {item}")
        else:
            print("- No updates required.")

        print("\nMANUAL ADMIN / ACCOUNTANT STEPS")
        print("-" * 80)
        for item in manual_steps:
            print(f"- {item}")

        print("\nMODE")
        print("-" * 80)
        print("DRY-RUN" if dry_run else "APPLY")

    except xmlrpc.client.Fault as exc:
        log.error("Odoo permission/configuration error: %s", exc)
        sys.exit(2)
    except Exception as exc:
        log.error("Unexpected error: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()

"""What a vendor must get right before the form can be sent at all.

Only the shape of each answer is checked here: required, the right format, a valid check
digit. Whether the answers are true, and agree with each other and with the documents,
is the process's job once the application is in.
"""
import re

from . import validators as v
from .checks import COUNTRIES

ENTITY_TYPES = {
    "IN": ["Private Limited", "Public Limited", "LLP", "Partnership", "Sole proprietor"],
    "US": ["LLC", "Corporation", "Partnership", "Sole proprietor"],
    "GB": ["Limited", "PLC", "LLP", "Sole proprietor"],
}
DOC_SLOTS = ("tax_certificate", "bank_proof")


def _has_letters(value: str, minimum: int = 2) -> bool:
    return len(re.findall(r"[^\W\d_]", value)) >= minimum


def _name_ok(value: str) -> tuple[bool, str]:
    if not _has_letters(value):
        return False, "Enter a name."
    if re.search(r"\d", value):
        return False, "A person's name shouldn't contain digits."
    return True, ""


def _text_ok(minimum_letters: int, message: str):
    return lambda value: (True, "") if _has_letters(value, minimum_letters) else (False, message)


def required_fields(sub: dict) -> list[str]:
    country = sub.get("country")
    fields = ["legal_name", "country", "entity_type", "address", "city", "postal_code",
              "contact_name", "contact_email", "contact_phone", "tax_id", "account_holder", "bank_name", "bank_code"]
    if country in ("IN", "US"):
        fields += ["state", "account_number"]
    if country == "IN":
        fields.append("pan")
    if sub.get("entity_type") == "Sole proprietor":
        fields.append("owner_name")
    return fields


def validate(sub: dict, docs_on_file: set[str]) -> dict[str, str]:
    """Every problem at once, keyed by field name, so the form can mark each one."""
    country = sub.get("country")
    if country not in COUNTRIES:
        return {"country": "Choose India, the United States or the United Kingdom."}
    info = COUNTRIES[country]
    rules = {
        "legal_name": _text_ok(2, "Enter the company's legal name."),
        "entity_type": lambda x: (True, "") if x in ENTITY_TYPES[country] else (False, "Choose an entity type from the list."),
        "owner_name": _name_ok,
        "address": _text_ok(3, "Enter the registered address."),
        "city": lambda x: (False, "Enter the city.") if not _has_letters(x) else (False, "A city name shouldn't contain digits.") if re.search(r"\d", x) else (True, ""),
        "state": lambda x: v.state_ok(x, country),
        "postal_code": lambda x: v.postal_ok(x, country),
        "contact_name": _name_ok,
        "contact_email": v.email_ok,
        "contact_phone": lambda x: v.phone_ok(x, country),
        "tax_id": {"IN": v.gstin_ok, "US": v.ein_ok, "GB": v.gb_vat_ok}[country],
        "pan": v.pan_ok,
        "account_holder": _text_ok(2, "Enter the name the bank account is held in."),
        "bank_name": _text_ok(2, "Enter the bank's name."),
        "account_number": (lambda x: v.account_ok(x, 9, 18)) if country == "IN" else (lambda x: v.account_ok(x, 4, 17)),
        "bank_code": {"IN": v.ifsc_ok, "US": v.aba_ok, "GB": v.iban_ok}[country],
    }
    labels = {"tax_id": info["tax_label"], "bank_code": info["bank_code_label"]}
    errors = {}
    for field in required_fields(sub):
        value = str(sub.get(field) or "").strip()
        if not value:
            errors[field] = "Required."
            continue
        rule = rules.get(field)
        ok, why = rule(value) if rule else (True, "")
        if not ok:
            if field == "tax_id":
                other = v.looks_like_other_country(value, country)
                why = f"This looks like {other}, not a {labels['tax_id']} from {info['name']}." if other else why
            errors[field] = why
    if "tax_certificate" not in docs_on_file:
        errors["tax_certificate"] = f"Upload your {info['tax_doc_name']}."
    if "bank_proof" not in docs_on_file:
        errors["bank_proof"] = "Upload a cancelled cheque or a bank letter."
    return errors

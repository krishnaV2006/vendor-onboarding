"""The rulebook. Each function looks at a submission and returns graded checks.

Every stage runs even if an earlier one failed, so a vendor gets all their problems
in one message rather than one at a time.
"""
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone

from . import names, validators as v

PASS, NOTE, FIX, REVIEW, BLOCK = "pass", "note", "fix", "review", "block"
BANK_LETTER_MAX_AGE_DAYS = 180
FREE_EMAIL = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "rediffmail.com", "proton.me"}

COUNTRIES = {
    "IN": {"name": "India", "tax_label": "GSTIN", "bank_code_label": "IFSC",
           "tax_docs": {"gst_certificate"}, "tax_doc_name": "GST registration certificate"},
    "US": {"name": "United States", "tax_label": "EIN", "bank_code_label": "ABA routing number",
           "tax_docs": {"w9"}, "tax_doc_name": "signed Form W-9"},
    "GB": {"name": "United Kingdom", "tax_label": "VAT number", "bank_code_label": "IBAN",
           "tax_docs": {"vat_certificate"}, "tax_doc_name": "VAT registration certificate"},
}
BANK_DOCS = {"cancelled_cheque", "bank_letter", "bank_statement"}
DOC_TYPE_NAMES = {
    "gst_certificate": "a GST certificate", "w9": "a Form W-9", "vat_certificate": "a VAT certificate",
    "cancelled_cheque": "a cancelled cheque", "bank_letter": "a bank letter",
    "bank_statement": "a bank statement", "company_registration": "a company registration certificate",
    "invoice": "an invoice", "other": "an unrelated document",
}


@dataclass
class Check:
    id: str
    name: str
    result: str
    detail: str          # for the reviewer: can include internal reasoning
    vendor_action: str | None = None  # for the vendor: only set when result is FIX

    def to_dict(self) -> dict:
        return asdict(self)


def _fix(cid, name, detail, action) -> Check:
    return Check(cid, name, FIX, detail, action)


def _is_blank(value) -> bool:
    return not str(value or "").strip()


def completeness(sub: dict, docs: dict) -> list[Check]:
    country = COUNTRIES.get(sub.get("country"))
    required = {
        "legal_name": "Legal company name", "entity_type": "Entity type", "address": "Registered address",
        "city": "City", "postal_code": "Postal code", "contact_name": "Contact name",
        "contact_email": "Contact email", "tax_id": country["tax_label"] if country else "Tax ID",
        "account_holder": "Bank account holder name", "bank_name": "Bank name",
    }
    if sub.get("country") == "IN":
        required.update({"pan": "PAN", "state": "State", "account_number": "Bank account number",
                         "bank_code": "IFSC"})
    elif sub.get("country") == "US":
        required.update({"state": "State", "account_number": "Bank account number",
                         "bank_code": "ABA routing number"})
    elif sub.get("country") == "GB":
        required["bank_code"] = "IBAN"
    if sub.get("entity_type") == "Sole proprietor":
        required["owner_name"] = "Owner name"

    checks = []
    if not country:
        checks.append(_fix("country", "Country", "Country is missing or not supported.",
                           "Choose your country: India, United States or United Kingdom."))
    missing = [label for field, label in required.items() if _is_blank(sub.get(field))]
    if missing:
        checks.append(_fix("fields_present", "Required fields", f"Missing: {', '.join(missing)}.",
                           f"Fill in these missing details: {', '.join(missing)}."))
    else:
        checks.append(Check("fields_present", "Required fields", PASS, f"All {len(required)} required fields are filled in."))
    tax_doc_name = country["tax_doc_name"] if country else "tax document"
    if "tax_certificate" in docs:
        checks.append(Check("tax_doc_present", "Tax document attached", PASS, docs["tax_certificate"]["filename"]))
    else:
        checks.append(_fix("tax_doc_present", "Tax document attached", "No tax document was uploaded.",
                           f"Upload your {tax_doc_name}."))
    if "bank_proof" in docs:
        checks.append(Check("bank_doc_present", "Bank document attached", PASS, docs["bank_proof"]["filename"]))
    else:
        checks.append(_fix("bank_doc_present", "Bank document attached", "No bank document was uploaded.",
                           "Upload a cancelled cheque or a bank letter showing your account details."))
    return checks


def _format_check(cid, name, value, validator) -> Check | None:
    if _is_blank(value):
        return None  # already reported by the completeness stage
    title = f"{name[0].upper()}{name[1:]} format"
    ok, why = validator(value)
    if ok:
        return Check(cid, title, PASS, f"{value} is valid.")
    return _fix(cid, title, f"{value}: {why}", f"Correct your {name}. {why}")


def formats(sub: dict) -> list[Check]:
    country = sub.get("country")
    results = [_format_check("email_format", "contact email", sub.get("contact_email"), v.email_ok)]
    tax_id = sub.get("tax_id")
    if country in COUNTRIES and not _is_blank(tax_id):
        validator = {"IN": v.gstin_ok, "US": v.ein_ok, "GB": v.gb_vat_ok}[country]
        label = COUNTRIES[country]["tax_label"]
        ok, why = validator(tax_id)
        if ok:
            results.append(Check("tax_id_format", f"{label} format", PASS, f"{tax_id} is a valid {label}."))
        else:
            other = v.looks_like_other_country(tax_id, country)
            if other:
                why = f"This looks like {other}, but the vendor says they're in {COUNTRIES[country]['name']}."
            results.append(_fix("tax_id_format", f"{label} format", f"{tax_id}: {why}",
                                f"Check your {label} and your country. {why}"))
    if country == "IN":
        results.append(_format_check("pan_format", "PAN", sub.get("pan"), v.pan_ok))
        results.append(_format_check("bank_code_format", "IFSC", sub.get("bank_code"), v.ifsc_ok))
        results.append(_format_check("account_format", "bank account number", sub.get("account_number"),
                                     lambda x: v.account_ok(x, 9, 18)))
    elif country == "US":
        results.append(_format_check("bank_code_format", "ABA routing number", sub.get("bank_code"), v.aba_ok))
        results.append(_format_check("account_format", "bank account number", sub.get("account_number"),
                                     lambda x: v.account_ok(x, 4, 17)))
    elif country == "GB":
        results.append(_format_check("bank_code_format", "IBAN", sub.get("bank_code"), v.iban_ok))
    return [c for c in results if c]


def read_quality(slot_label: str, read: dict | None, cid: str) -> list[Check]:
    """Was the document readable at all, and is anything odd about it?"""
    if read is None:
        return []
    fields = read.get("fields")
    if read["reader"] == "none":
        return [Check(f"{cid}_read", f"{slot_label} read", REVIEW,
                      f"Nothing could read this document, so a person needs to check it by hand. {read.get('error')}")]
    checks = []
    if read["reader"] == "text_parser":
        checks.append(Check(f"{cid}_reader", f"{slot_label} reader", NOTE,
                            f"The {slot_label.lower()} was read by the backup text parser, not AI. {read.get('error')}"))
    if fields["legibility"] == "low":
        checks.append(_fix(f"{cid}_read", f"{slot_label} read", "Key values on this document are hard to read.",
                           f"Upload a clearer copy of your {slot_label.lower()}."))
    else:
        checks.append(Check(f"{cid}_read", f"{slot_label} read", PASS,
                            f"Read as {DOC_TYPE_NAMES.get(fields['doc_type'], fields['doc_type'])}."))
    if fields.get("contains_instructions_to_reader"):
        checks.append(Check(f"{cid}_injection", f"{slot_label} tampering", REVIEW,
                            "This document contains text addressed to the reader, such as asking for approval. "
                            "It had no effect on the checks, but a person should look at it."))
    return checks


def _fields(read: dict | None) -> dict | None:
    return read.get("fields") if read else None


CLOSE_PAYEE = ("A close match on who owns an account goes to a person, as with a close match in the UK's "
               "Confirmation of Payee check, because payment fraud often uses a name that's nearly right.")


def _name_relation_check(cid, name, expected, found, source, payee=False) -> Check:
    relation = names.compare(expected, found)
    if relation == names.SAME:
        same_text = f'"{expected}" matches "{found}" on the {source}.'
        return Check(cid, name, PASS, same_text)
    if relation == names.CLOSE:
        if payee:
            return Check(cid, name, REVIEW, f'"{expected}" nearly matches "{found}" on the {source}, but not exactly. {CLOSE_PAYEE}')
        return Check(cid, name, NOTE, f'"{expected}" nearly matches "{found}" on the {source}, probably a typo.')
    return _fix(cid, name, f'The form says "{expected}" but the {source} says "{found}".',
                f'Your form says "{expected}" but your {source} says "{found}". Correct the form or upload the right document.')


def _digits_equal(a, b) -> bool:
    return v.clean(a) == v.clean(b)


def cross_checks(sub: dict, reads: dict, today: date | None = None) -> list[Check]:
    today = today or datetime.now(timezone.utc).date()
    country = sub.get("country")
    checks: list[Check] = []

    # Fields that contradict each other, visible only when you compare them.
    tax_id, pan = v.clean(sub.get("tax_id")), v.clean(sub.get("pan"))
    if country == "IN" and v.gstin_ok(tax_id)[0]:
        if pan and v.pan_ok(pan)[0]:
            if tax_id[2:12] == pan:
                checks.append(Check("gstin_pan", "GSTIN and PAN agree", PASS, f"The PAN inside the GSTIN matches {pan}."))
            else:
                checks.append(_fix("gstin_pan", "GSTIN and PAN agree",
                                   f"Every GSTIN contains its holder's PAN. This one contains {tax_id[2:12]}, but the form says {pan}.",
                                   f"The PAN built into your GSTIN ({tax_id[2:12]}) doesn't match the PAN you gave ({pan}). Confirm which is correct."))
        gst_state = v.GST_STATES.get(tax_id[:2])
        state = (sub.get("state") or "").strip()
        if state and gst_state:
            if state.lower() == gst_state.lower():
                checks.append(Check("gstin_state", "GSTIN and address state agree", PASS, f"Both say {gst_state}."))
            else:
                checks.append(_fix("gstin_state", "GSTIN and address state agree",
                                   f"The GSTIN starts with {tax_id[:2]}, which is {gst_state}, but the address is in {state}.",
                                   f"Your GSTIN is registered in {gst_state} but your address is in {state}. Provide the GSTIN for {state}, or correct the address."))
    if country == "IN" and pan and v.pan_ok(pan)[0]:
        holder_type = pan[3]
        expected = {"Private Limited": "C", "Public Limited": "C", "LLP": "F", "Partnership": "F", "Sole proprietor": "P"}.get(sub.get("entity_type"))
        if expected and holder_type != expected:
            checks.append(_fix("pan_entity", "PAN and entity type agree",
                               f"This PAN belongs to {v.PAN_HOLDER_TYPES.get(holder_type, 'an unknown holder type')}, but the entity type is {sub.get('entity_type')}.",
                               f"The PAN you gave belongs to {v.PAN_HOLDER_TYPES.get(holder_type, 'a different kind of holder')}, which doesn't fit {sub.get('entity_type')}. Check the PAN or the entity type."))
        elif expected:
            checks.append(Check("pan_entity", "PAN and entity type agree", PASS, f"This PAN belongs to {v.PAN_HOLDER_TYPES[holder_type]}."))
    if country == "IN" and not _is_blank(sub.get("bank_code")) and not _is_blank(sub.get("bank_name")):
        bank_names = v.IFSC_BANKS.get(v.clean(sub["bank_code"])[:4])
        expected_bank = bank_names[0] if bank_names else None
        given = names.normalize(sub["bank_name"])
        if bank_names and not any(re.search(rf"\b{re.escape(n)}\b", given) for n in bank_names):
            checks.append(_fix("ifsc_bank", "IFSC and bank name agree",
                               f"IFSC {sub['bank_code']} belongs to {expected_bank}, but the form says {sub['bank_name']}.",
                               f"Your IFSC belongs to {expected_bank}, but you named {sub['bank_name']} as your bank. Correct whichever is wrong."))

    # Whose account is it?
    checks.append(_holder_check(sub))

    # Does the tax document back up the form?
    country_info = COUNTRIES.get(country)
    tax_fields = _fields(reads.get("tax_certificate"))
    if tax_fields and country_info:
        if tax_fields["doc_type"] not in country_info["tax_docs"]:
            checks.append(_fix("tax_doc_type", "Right tax document",
                               f"Expected {country_info['tax_doc_name']}, but this is {DOC_TYPE_NAMES.get(tax_fields['doc_type'])}.",
                               f"The tax document you uploaded is {DOC_TYPE_NAMES.get(tax_fields['doc_type'])}. Upload your {country_info['tax_doc_name']} instead."))
        else:
            checks.append(Check("tax_doc_type", "Right tax document", PASS, f"This is the {country_info['tax_doc_name']} we need."))
            if tax_fields.get("legal_name"):
                checks.append(_name_relation_check("tax_doc_name", "Name on tax document", sub.get("legal_name"), tax_fields["legal_name"], "tax document"))
            else:
                checks.append(Check("tax_doc_name", "Name on tax document", REVIEW, "The company name couldn't be found on this document."))
            if tax_fields.get("tax_id"):
                if _digits_equal(tax_fields["tax_id"], sub.get("tax_id")):
                    checks.append(Check("tax_doc_id", "Tax ID on document", PASS, f"The document shows {tax_fields['tax_id']}, same as the form."))
                else:
                    checks.append(_fix("tax_doc_id", "Tax ID on document",
                                       f"The form says {sub.get('tax_id')} but the document says {tax_fields['tax_id']}.",
                                       f"The {country_info['tax_label']} on your form ({sub.get('tax_id')}) doesn't match your document ({tax_fields['tax_id']}). Correct the form or upload the right document."))
            else:
                checks.append(Check("tax_doc_id", "Tax ID on document", REVIEW, "The tax ID couldn't be found on this document."))

    # Does the bank document back up the form?
    bank_fields = _fields(reads.get("bank_proof"))
    if bank_fields:
        if bank_fields["doc_type"] not in BANK_DOCS:
            checks.append(_fix("bank_doc_type", "Right bank document",
                               f"Expected bank proof, but this is {DOC_TYPE_NAMES.get(bank_fields['doc_type'])}.",
                               f"The bank document you uploaded is {DOC_TYPE_NAMES.get(bank_fields['doc_type'])}. Upload a cancelled cheque or a bank letter instead."))
        else:
            checks.append(Check("bank_doc_type", "Right bank document", PASS, f"This is {DOC_TYPE_NAMES[bank_fields['doc_type']]}."))
            if bank_fields.get("account_holder"):
                checks.append(_name_relation_check("bank_doc_holder", "Holder on bank document", sub.get("account_holder"), bank_fields["account_holder"], "bank document", payee=True))
            else:
                checks.append(Check("bank_doc_holder", "Holder on bank document", REVIEW, "The account holder couldn't be found on this document."))
            form_account = sub.get("bank_code") if country == "GB" else sub.get("account_number")
            if bank_fields.get("account_number"):
                if _digits_equal(bank_fields["account_number"], form_account):
                    checks.append(Check("bank_doc_account", "Account on bank document", PASS, "The account number matches the form."))
                else:
                    checks.append(_fix("bank_doc_account", "Account on bank document",
                                       f"The form says {form_account} but the document says {bank_fields['account_number']}.",
                                       "The account number on your form doesn't match your bank document. Correct the form or upload the right document."))
            else:
                checks.append(Check("bank_doc_account", "Account on bank document", REVIEW, "The account number couldn't be found on this document."))
            checks += _bank_doc_age(bank_fields, today)

    domain = (sub.get("contact_email") or "").rsplit("@", 1)[-1].lower()
    if domain in FREE_EMAIL:
        checks.append(Check("email_domain", "Contact email domain", NOTE, f"The contact uses a free email address ({domain}), not a company one."))
    return checks


def _holder_check(sub: dict) -> Check:
    legal, holder = sub.get("legal_name"), sub.get("account_holder")
    name = "Who owns the bank account"
    if _is_blank(holder) or _is_blank(legal):
        return Check("bank_holder", name, NOTE, "Skipped because a name is missing.")
    relation = names.compare(legal, holder)
    owner = sub.get("owner_name")
    if sub.get("entity_type") == "Sole proprietor" and not _is_blank(owner):
        owner_relation = names.compare(owner, holder)
        if owner_relation == names.SAME:
            return Check("bank_holder", name, PASS, f'The account is in the owner\'s name ("{holder}"), which is normal for a sole proprietor.')
        if owner_relation == names.CLOSE:
            return Check("bank_holder", name, REVIEW, f'The account is in the name "{holder}", which nearly matches the owner "{owner}", but not exactly. {CLOSE_PAYEE}')
    if relation == names.SAME:
        return Check("bank_holder", name, PASS, f'"{holder}" is the same company as "{legal}", once abbreviations are expanded.')
    if relation == names.CLOSE:
        return Check("bank_holder", name, REVIEW, f'"{holder}" nearly matches "{legal}", but not exactly. {CLOSE_PAYEE}')
    if relation == names.PERSON:
        return _fix("bank_holder", name, f'The account is in what looks like a person\'s name ("{holder}"), not "{legal}". Paying a company into a personal account is a common fraud pattern.',
                    f'The bank account is in the name "{holder}", not "{legal}". Provide an account in the company\'s name, with a bank letter or cancelled cheque showing it.')
    if relation == names.OTHER_ENTITY:
        return _fix("bank_holder", name, f'"{holder}" has the same name as "{legal}" but a different legal form, so it may be a different company.',
                    f'The bank account holder "{holder}" has a different legal form from "{legal}". Provide an account held by the exact company that is applying.')
    return _fix("bank_holder", name, f'The account belongs to a different business ("{holder}"), not "{legal}".',
                f'The bank account belongs to "{holder}", not "{legal}". Provide an account in the company\'s name.')


def _bank_doc_age(fields: dict, today: date) -> list[Check]:
    if fields["doc_type"] != "bank_letter" or not fields.get("issue_date"):
        return []
    try:
        issued = date.fromisoformat(fields["issue_date"])
    except ValueError:
        return [Check("bank_doc_age", "Bank letter is recent", NOTE, f'Couldn\'t understand the date "{fields["issue_date"]}".')]
    age = (today - issued).days
    if age > BANK_LETTER_MAX_AGE_DAYS:
        return [_fix("bank_doc_age", "Bank letter is recent", f"The letter is {age} days old. The limit is {BANK_LETTER_MAX_AGE_DAYS}.",
                     f"Your bank letter is dated {issued}, which is too old. Upload one from the last {BANK_LETTER_MAX_AGE_DAYS} days.")]
    if age < 0:
        return [Check("bank_doc_age", "Bank letter is recent", REVIEW, f"The letter is dated in the future ({issued}).")]
    return [Check("bank_doc_age", "Bank letter is recent", PASS, f"The letter is {age} days old.")]


def _legal_entity(tax_id) -> str:
    """Which legal entity a tax ID belongs to. One Indian company holds a GSTIN for each state it's
    registered in, and every one of them contains the company's PAN, so for a GSTIN that's the PAN."""
    value = v.clean(tax_id)
    if len(value) == 15 and value[:2].isdigit() and v.pan_ok(value[2:12])[0]:
        return value[2:12]
    return value


def risk(sub: dict, vendors: list[dict], watchlist: list[dict], case_id: str | None = None) -> list[Check]:
    checks = []
    others = [x for x in vendors if case_id is None or x.get("case_id") != case_id]

    bank_key = v.clean(sub.get("bank_code") if sub.get("country") == "GB" else sub.get("account_number"))
    tax_key = v.clean(sub.get("tax_id"))
    entity = _legal_entity(tax_key)
    same_bank = [x for x in others if bank_key and v.clean(x["bank_account"]) == bank_key]
    same_tax = [x for x in others if tax_key and v.clean(x["tax_id"]) == tax_key]

    unrelated_bank = [x for x in same_bank if _legal_entity(x["tax_id"]) != entity]
    other_state = [x for x in same_bank if _legal_entity(x["tax_id"]) == entity and v.clean(x["tax_id"]) != tax_key]
    if unrelated_bank:
        owner = unrelated_bank[0]["name"]
        checks.append(Check("bank_unique", "Bank account not used by another vendor", BLOCK,
                            f'This account is already on file for "{owner}", a different vendor with a different tax ID. Two companies sharing an account is a strong sign of payment diversion.'))
    elif other_state:
        existing = other_state[0]
        checks.append(Check("bank_unique", "Bank account not used by another vendor", NOTE,
                            f'This account is already on file for "{existing["name"]}" under GSTIN {existing["tax_id"]}, which contains the same PAN ({entity}). '
                            "A company holds one GSTIN for each state, so this is the same company registering in another state, not a different vendor. "
                            "Consider adding this GSTIN to the existing vendor rather than creating a second record."))
    else:
        checks.append(Check("bank_unique", "Bank account not used by another vendor", PASS, f"Checked against {len(others)} vendors on file."))

    if same_tax:
        checks.append(Check("tax_unique", "Vendor isn't already on file", REVIEW,
                            f'This tax ID is already on file as "{same_tax[0]["name"]}". This may be a duplicate record, or an attempt to change existing details.'))
    else:
        checks.append(Check("tax_unique", "Vendor isn't already on file", PASS, f"No vendor on file has this tax ID."))

    people = [("company", sub.get("legal_name"))]
    if not _is_blank(sub.get("owner_name")):
        people.append(("owner", sub.get("owner_name")))
    worst = Check("watchlist", "Restricted-party screening", PASS, f"No match across {len(watchlist)} restricted parties.")
    for label, value in people:
        for entry in watchlist:
            score = names.similarity(value, entry["name"])
            if names.core(value) == names.core(entry["name"]):
                worst = Check("watchlist", "Restricted-party screening", BLOCK, f'The {label} name "{value}" exactly matches restricted party "{entry["name"]}" ({entry["reason"]}).')
                break
            if score >= 88 and worst.result == PASS:
                worst = Check("watchlist", "Restricted-party screening", REVIEW, f'The {label} name "{value}" is similar to restricted party "{entry["name"]}" ({score:.0f}% match). A person should rule it out.')
        if worst.result == BLOCK:
            break
    checks.append(worst)
    return checks


def compare_versions(previous: dict | None, current: dict) -> list[Check]:
    """On a resubmission, say what changed. Changed bank details deserve a second look."""
    if not previous:
        return []
    labels = {"legal_name": "company name", "tax_id": "tax ID", "pan": "PAN", "account_holder": "account holder",
              "account_number": "account number", "bank_code": "bank code", "bank_name": "bank name", "state": "state"}
    changed = [label for field, label in labels.items() if (previous.get(field) or "") != (current.get(field) or "")]
    if not changed:
        return [Check("version_diff", "Changes since last attempt", NOTE, "No form fields changed.")]
    bank_fields = {"account holder", "account number", "bank code", "bank name"}
    detail = f"Changed: {', '.join(changed)}."
    if bank_fields & set(changed):
        detail += " Bank details changed between attempts, so the reviewer may want to confirm the new account by phone."
    return [Check("version_diff", "Changes since last attempt", NOTE, detail)]


def decide(checks: list[Check]) -> dict:
    results = {c.result for c in checks}
    if BLOCK in results:
        status, waiting_on = "rejected", None
    elif FIX in results:
        status, waiting_on = "pending", "vendor"
    elif REVIEW in results:
        status, waiting_on = "pending", "internal"
    else:
        status, waiting_on = "approved", None
    counts = {r: sum(c.result == r for c in checks) for r in (PASS, NOTE, FIX, REVIEW, BLOCK)}
    reasons = [c.detail for c in checks if c.result in (BLOCK, REVIEW, FIX)]
    return {"status": status, "waiting_on": waiting_on, "counts": counts, "reasons": reasons}


def reviewer_message(action: str, sub: dict) -> dict:
    """Tells the vendor the outcome when a reviewer decides. Their note stays internal."""
    decision = {"status": "approved" if action == "approve" else "rejected", "waiting_on": None}
    return {**build_message(decision, [], sub), "intro_source": "template"}


def build_message(decision: dict, checks: list[Check], sub: dict, intro: str | None = None) -> dict:
    """What the vendor sees. Only FIX items are ever disclosed; BLOCK and REVIEW reasons stay internal."""
    name = sub.get("contact_name") or "there"
    vendor = sub.get("legal_name") or "your company"
    status = decision["status"]
    if status == "approved":
        return {"subject": f"{vendor} is approved as a vendor", "greeting": f"Hi {name},",
                "intro": f"Good news: {vendor} has passed our onboarding checks and is now an approved vendor. No further action is needed.",
                "items": [], "closing": "We'll be in touch when there's a purchase order for you."}
    if status == "rejected":
        return {"subject": f"Update on your vendor application for {vendor}", "greeting": f"Hi {name},",
                "intro": f"We're unable to proceed with the application for {vendor} at this time.",
                "items": [], "closing": "If you think this is a mistake, please contact your procurement representative."}
    if decision["waiting_on"] == "internal":
        return {"subject": f"Your vendor application for {vendor} is being reviewed", "greeting": f"Hi {name},",
                "intro": f"We've received everything we need for {vendor}. Our team is completing its review, and you don't need to do anything for now.",
                "items": [], "closing": "We'll contact you if we need anything else."}
    items = [c.vendor_action for c in checks if c.result == FIX and c.vendor_action]
    return {"subject": f"Action needed: your vendor application for {vendor}", "greeting": f"Hi {name},",
            "intro": intro or f"Thanks for applying to become a vendor. We've checked your application for {vendor}, and need a few things before we can finish.",
            "items": items, "closing": "You can update your application below. You only need to change what's listed here."}

from datetime import date

import pytest

from app import checks as rules
from app import names, validators as v


@pytest.mark.parametrize("value", ["27AAPFU0939F1ZV", "27-AAPFU0939F1ZV", "27aapfu0939f1zv"])
def test_real_gstin_passes(value):
    assert v.gstin_ok(value)[0]


@pytest.mark.parametrize("value", ["27AAPFU0939F1ZW", "99AAPFU0939F1ZV", "12345", ""])
def test_bad_gstin_fails(value):
    assert not v.gstin_ok(value)[0]


def test_other_formats():
    assert v.ein_ok("84-1234567")[0] and not v.ein_ok("07-1234567")[0]
    assert v.aba_ok("021000021")[0] and not v.aba_ok("021000022")[0]
    assert v.gb_vat_ok("GB123456782")[0] and not v.gb_vat_ok("GB123456783")[0]
    assert v.iban_ok("GB82WEST12345698765432")[0] and not v.iban_ok("GB82WEST12345698765433")[0]
    assert not v.iban_ok("DE89370400440532013000")[0]
    assert v.ifsc_ok("HDFC0001234")[0] and not v.ifsc_ok("HDFC1001234")[0]
    assert v.pan_ok("AAPFU0939F")[0] and not v.pan_ok("AAPFU09399")[0]


def test_wrong_country_tax_id_is_named():
    assert v.looks_like_other_country("27AAPFU0939F1ZV", "US") == "an Indian GSTIN"


@pytest.mark.parametrize("a,b,expected", [
    ("Acme Pvt Ltd", "ACME PRIVATE LIMITED", names.SAME),
    ("M/s Acme Pvt. Ltd.", "Acme Private Limited", names.SAME),
    ("Acme Private Limited", "Acme LLP", names.OTHER_ENTITY),
    ("Bluepeak Logistics Pvt Ltd", "Ramesh K Patel", names.PERSON),
    ("Bluepeak Logistics Pvt Ltd", "Orion Traders Pvt Ltd", names.DIFFERENT),
    ("Sharma Precision Tools Pvt Ltd", "Sharma Precison Tools Private Limited", names.CLOSE),
])
def test_name_comparison(a, b, expected):
    assert names.compare(a, b) == expected


def india(**changes):
    sub = {"country": "IN", "legal_name": "Acme Pvt Ltd", "entity_type": "Private Limited", "state": "Maharashtra",
           "tax_id": "27AAPFU0939F1ZV", "pan": "AAPFU0939F", "account_holder": "Acme Private Limited",
           "bank_name": "HDFC Bank", "bank_code": "HDFC0001234", "account_number": "50100234567891"}
    return {**sub, **changes}


def result_of(checks, check_id):
    return next(c.result for c in checks if c.id == check_id)


def test_sole_proprietor_personal_account_is_fine():
    sub = india(legal_name="Patel Traders", entity_type="Sole proprietor", owner_name="Ramesh K Patel", account_holder="Ramesh K Patel")
    assert rules._holder_check(sub).result == rules.PASS


def test_company_personal_account_needs_fixing():
    assert rules._holder_check(india(account_holder="Ramesh K Patel")).result == rules.FIX


def test_gstin_contradictions_are_found():
    out = rules.cross_checks(india(pan="AAPFU0940G", state="Gujarat"), {})
    assert result_of(out, "gstin_pan") == rules.FIX
    assert result_of(out, "gstin_state") == rules.FIX


def test_individual_pan_for_a_company_is_found():
    out = rules.cross_checks(india(tax_id="", pan="AAAPL1234C"), {})
    assert result_of(out, "pan_entity") == rules.FIX


def test_ifsc_bank_mismatch_is_found():
    out = rules.cross_checks(india(bank_name="ICICI Bank"), {})
    assert result_of(out, "ifsc_bank") == rules.FIX


def bank_read(**fields):
    base = dict(doc_type="bank_letter", account_holder="Acme Private Limited", account_number="50100234567891",
                issue_date="2026-10-01", legibility="high", contains_instructions_to_reader=False)
    return {"reader": "claude", "fields": {**base, **fields}}


def test_old_bank_letter_needs_replacing():
    out = rules.cross_checks(india(), {"bank_proof": bank_read(issue_date="2025-01-01")}, today=date(2026, 11, 4))
    assert result_of(out, "bank_doc_age") == rules.FIX


def test_account_on_document_must_match_form():
    out = rules.cross_checks(india(), {"bank_proof": bank_read(account_number="99999999999")}, today=date(2026, 11, 4))
    assert result_of(out, "bank_doc_account") == rules.FIX


def test_unreadable_document_goes_to_a_person_never_approved():
    out = rules.read_quality("Bank document", {"reader": "none", "error": "AI down"}, "bank_doc")
    assert rules.decide(out)["status"] == "pending" and rules.decide(out)["waiting_on"] == "internal"


def test_document_addressing_the_reader_goes_to_a_person():
    out = rules.read_quality("Tax document", bank_read(contains_instructions_to_reader=True), "tax_doc")
    assert result_of(out, "tax_doc_injection") == rules.REVIEW


VENDORS = [{"name": "Orion Traders Pvt Ltd", "tax_id": "27AABCO1234E1ZL", "bank_account": "918020045671234", "case_id": None}]
WATCHLIST = [{"name": "Northgate Trading FZE", "reason": "test"}]


def test_shared_bank_account_is_blocked():
    out = rules.risk(india(account_number="9180-2004-5671234"), VENDORS, WATCHLIST)
    assert result_of(out, "bank_unique") == rules.BLOCK


def test_same_company_registering_another_state_isnt_payment_diversion():
    # One PAN can hold a GSTIN in each state. The Maharashtra unit is on file; the Karnataka unit applies.
    on_file = [{"name": "Acme Pvt Ltd", "tax_id": "27AAPFU0939F1ZV", "bank_account": "50100234567891", "case_id": None}]
    out = rules.risk(india(tax_id="29AAPFU0939F1ZR", state="Karnataka"), on_file, WATCHLIST)
    assert result_of(out, "bank_unique") == rules.NOTE
    assert rules.decide(out)["status"] == "approved"


def test_shared_account_with_a_different_pan_is_still_blocked():
    on_file = [{"name": "Other Co Pvt Ltd", "tax_id": "29AABCO1234E1ZL", "bank_account": "50100234567891", "case_id": None}]
    out = rules.risk(india(), on_file, WATCHLIST)
    assert result_of(out, "bank_unique") == rules.BLOCK


def test_existing_tax_id_goes_for_review():
    out = rules.risk(india(tax_id="27AABCO1234E1ZL"), VENDORS, WATCHLIST)
    assert result_of(out, "tax_unique") == rules.REVIEW


def test_a_case_doesnt_conflict_with_itself():
    mine = [{**VENDORS[0], "case_id": "VND-1"}]
    out = rules.risk(india(account_number="918020045671234"), mine, WATCHLIST, case_id="VND-1")
    assert result_of(out, "bank_unique") == rules.PASS


@pytest.mark.parametrize("name,expected", [
    ("Northgate Trading FZE", rules.BLOCK),
    ("Northgate Tradng FZE", rules.REVIEW),
    ("Southgate Bakery Pvt Ltd", rules.PASS),
])
def test_watchlist(name, expected):
    assert result_of(rules.risk(india(legal_name=name), [], WATCHLIST), "watchlist") == expected


def test_decision_order():
    make = lambda result: rules.Check("x", "x", result, "x")
    assert rules.decide([make(rules.PASS), make(rules.NOTE)])["status"] == "approved"
    assert rules.decide([make(rules.FIX), make(rules.REVIEW)])["waiting_on"] == "vendor"
    assert rules.decide([make(rules.REVIEW)])["waiting_on"] == "internal"
    assert rules.decide([make(rules.FIX), make(rules.BLOCK)])["status"] == "rejected"


def test_vendor_message_never_leaks_internal_reasons():
    block = rules.Check("bank_unique", "x", rules.BLOCK, 'Already on file for "Orion Traders"')
    review = rules.Check("watchlist", "x", rules.REVIEW, "Similar to a restricted party")
    for checks_ in ([block], [review], [block, review]):
        text = str(rules.build_message(rules.decide(checks_), checks_, {"legal_name": "Apex", "contact_name": "V"}))
        assert "Orion" not in text and "restricted" not in text.lower()


def test_resubmission_flags_bank_change():
    out = rules.compare_versions({"account_number": "111"}, {"account_number": "222"})
    assert "Bank details changed" in out[0].detail

"""Compares company and account-holder names the way a careful reviewer would."""
import re

from rapidfuzz import fuzz

EXPANSIONS = {
    "PVT": "PRIVATE", "PTE": "PRIVATE", "LTD": "LIMITED", "CO": "COMPANY", "CORP": "CORPORATION",
    "INC": "INCORPORATED", "MFG": "MANUFACTURING", "INTL": "INTERNATIONAL", "&": "AND",
}
HONORIFICS = {"M/S", "MS", "MESSRS", "THE", "MR", "MRS", "SHRI", "SMT", "DR"}
SUFFIXES = {"PRIVATE", "LIMITED", "LLP", "LLC", "INCORPORATED", "CORPORATION", "COMPANY", "PLC"}

SAME = "same"            # identical once abbreviations are expanded
CLOSE = "close"          # very likely the same name with a typo
OTHER_ENTITY = "other_entity"  # same core name, different legal form
PERSON = "person"        # looks like an individual's name
DIFFERENT = "different"  # a different business


def normalize(name: str | None) -> str:
    text = (name or "").upper().replace("&", " AND ").replace("M/S", " ")
    text = re.sub(r"[^A-Z0-9 ]", " ", text)
    tokens = [EXPANSIONS.get(t, t) for t in text.split()]
    return " ".join(t for t in tokens if t not in HONORIFICS)


def core(name: str | None) -> str:
    return " ".join(t for t in normalize(name).split() if t not in SUFFIXES)


def has_legal_suffix(name: str | None) -> bool:
    return any(t in SUFFIXES for t in normalize(name).split())


def looks_like_person(name: str | None) -> bool:
    tokens = normalize(name).split()
    business_words = {"TRADERS", "ENTERPRISES", "INDUSTRIES", "SOLUTIONS", "SERVICES", "SUPPLIES",
                      "LOGISTICS", "TECHNOLOGIES", "SOFTWARE", "SYSTEMS", "GROUP", "HOLDINGS", "AND"}
    return 2 <= len(tokens) <= 4 and not has_legal_suffix(name) and not set(tokens) & business_words


def compare(a: str | None, b: str | None) -> str:
    """How does name `b` relate to name `a`?"""
    na, nb = normalize(a), normalize(b)
    if na == nb:
        return SAME
    if core(a) == core(b) and has_legal_suffix(a) and has_legal_suffix(b):
        return OTHER_ENTITY
    if core(a) == core(b):
        return SAME
    if fuzz.token_sort_ratio(na, nb) >= 92:
        return CLOSE
    if looks_like_person(b):
        return PERSON
    return DIFFERENT


def similarity(a: str | None, b: str | None) -> float:
    return fuzz.token_sort_ratio(core(a), core(b))

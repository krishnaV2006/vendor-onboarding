"""Compares company and account-holder names the way a careful reviewer would."""
import re

from rapidfuzz import fuzz

EXPANSIONS = {
    "PVT": "PRIVATE", "PTE": "PRIVATE", "LTD": "LIMITED", "CO": "COMPANY", "CORP": "CORPORATION",
    "CORPN": "CORPORATION", "INC": "INCORPORATED", "MFG": "MANUFACTURING", "MFRS": "MANUFACTURERS",
    "INTL": "INTERNATIONAL", "ENGG": "ENGINEERING", "ENGRS": "ENGINEERS", "BROS": "BROTHERS",
    "INDS": "INDUSTRIES", "SVCS": "SERVICES", "ASSOC": "ASSOCIATES", "&": "AND",
}
# Spellings of the same word that banks and registries use interchangeably.
VARIANTS = {
    "SHREE": "SHRI", "SRI": "SHRI", "SREE": "SHRI",
    "MOHD": "MOHAMMED", "MOHAMMAD": "MOHAMMED", "MUHAMMAD": "MOHAMMED", "MUHAMMED": "MOHAMMED",
    "MOHAMED": "MOHAMMED", "MOHAMAD": "MOHAMMED",
    "LAXMI": "LAKSHMI", "LUXMI": "LAKSHMI",
}
# Legal forms written out in full, reduced to the short form.
PHRASES = [
    (r"\bLIMITED LIABILITY PARTNERSHIP\b", "LLP"),
    (r"\bLIMITED LIABILITY COMPANY\b", "LLC"),
    (r"\bPUBLIC LIMITED COMPANY\b", "PLC"),
]
HONORIFICS = {"M/S", "MS", "MESSRS", "THE", "MR", "MRS", "SHRI", "SMT", "DR"}
SUFFIXES = {"PRIVATE", "LIMITED", "LLP", "LLC", "INCORPORATED", "CORPORATION", "COMPANY", "PLC"}
BUSINESS_WORDS = {
    "AND", "TRADERS", "TRADING", "ENTERPRISES", "INDUSTRIES", "INDUSTRIAL", "SOLUTIONS", "SERVICES",
    "SUPPLIES", "SUPPLY", "SUPPLIERS", "LOGISTICS", "TECHNOLOGIES", "TECHNOLOGY", "SOFTWARE", "SYSTEMS",
    "GROUP", "HOLDINGS", "WORKS", "ENGINEERING", "ENGINEERS", "BROTHERS", "SONS", "MANUFACTURING",
    "MANUFACTURERS", "AGENCIES", "AGENCY", "STORES", "EXPORTS", "IMPORTS", "INTERNATIONAL", "ASSOCIATES",
    "CONSULTANTS", "CONSULTING", "CHEMICALS", "ELECTRICALS", "ELECTRONICS", "COMPONENTS", "TOOLS",
    "MOTORS", "FABRICATION", "FABRICATORS", "PRODUCTS", "TEXTILES", "PHARMA", "PHARMACEUTICALS", "FOODS",
    "PACKAGING", "CONSTRUCTION", "CONSTRUCTIONS", "INFRA", "PROJECTS", "PARTNERS", "PRECISION", "STEEL",
    "STEELS", "PLASTICS", "POLYMERS", "MILLS", "VENTURES", "DISTRIBUTORS", "MARKETING", "LABS",
    "LABORATORIES", "EQUIPMENT", "MACHINERY", "INSTRUMENTS", "HARDWARE", "FREIGHT", "TRANSPORT",
}

SAME = "same"            # identical once abbreviations are expanded
CLOSE = "close"          # very likely the same name with a typo
OTHER_ENTITY = "other_entity"  # same core name, different legal form
PERSON = "person"        # looks like an individual's name
DIFFERENT = "different"  # a different business


def _join_initials(match: re.Match) -> str:
    return re.sub(r"[.\s]", "", match.group(0)) + " "


def normalize(name: str | None) -> str:
    text = (name or "").upper().replace("&", " AND ").replace("M/S", " ")
    text = re.sub(r"(?:\b[A-Z]\.\s*){2,}", _join_initials, text)  # A.B.C. -> ABC, d.o.o. -> DOO
    text = re.sub(r"[^A-Z0-9 ]", " ", text)
    tokens = [EXPANSIONS.get(t, t) for t in text.split()]
    tokens = [VARIANTS.get(t, t) for t in tokens]
    # "(P) Ltd" and "P Ltd" are how Indian banks shorten "Private Limited".
    tokens = ["PRIVATE" if t == "P" and nxt == "LIMITED" else t for t, nxt in zip(tokens, tokens[1:] + [""])]
    text = " ".join(t for t in tokens if t not in HONORIFICS)
    for pattern, short in PHRASES:
        text = re.sub(pattern, short, text)
    return text


def core(name: str | None) -> str:
    return " ".join(t for t in normalize(name).split() if t not in SUFFIXES)


def has_legal_suffix(name: str | None) -> bool:
    return any(t in SUFFIXES for t in normalize(name).split())


def looks_like_person(name: str | None) -> bool:
    tokens = normalize(name).split()
    return 2 <= len(tokens) <= 4 and not has_legal_suffix(name) and not set(tokens) & BUSINESS_WORDS


def _only_typos(a: str, b: str) -> bool:
    """True when two normalized names differ only by small misspellings inside words, or by a
    missing initial. A whole extra or different word, like "UK" in "Tata Steel UK Limited", is
    never a typo."""
    words_a = [t for t in a.split() if len(t) > 1]
    words_b = [t for t in b.split() if len(t) > 1]
    initials_a = {t for t in a.split() if len(t) == 1}
    initials_b = {t for t in b.split() if len(t) == 1}
    if not words_a or len(words_a) != len(words_b):
        return False
    if not (initials_a <= initials_b or initials_b <= initials_a):
        return False
    if fuzz.ratio(" ".join(words_a), " ".join(words_b)) < 92:
        return False
    return all(x == y or (min(len(x), len(y)) >= 4 and fuzz.ratio(x, y) >= 80) for x, y in zip(words_a, words_b))


def _squash(text: str) -> str:
    return text.replace(" ", "")


def compare(a: str | None, b: str | None) -> str:
    """How does name `b` relate to name `a`?"""
    na, nb = normalize(a), normalize(b)
    if na == nb or _squash(na) == _squash(nb):
        return SAME
    ca, cb = core(a), core(b)
    if ca == cb and has_legal_suffix(a) and has_legal_suffix(b):
        return OTHER_ENTITY
    if ca == cb or _squash(ca) == _squash(cb):
        return SAME
    if _only_typos(na, nb) or _only_typos(ca, cb):
        return CLOSE
    if looks_like_person(b):
        return PERSON
    return DIFFERENT


def similarity(a: str | None, b: str | None) -> float:
    return fuzz.token_sort_ratio(core(a), core(b))

"""Format and checksum rules for tax IDs and bank codes. No AI, no I/O."""
import re

ALNUM = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

GST_STATES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh",
    "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra", "29": "Karnataka",
    "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman and Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh", "38": "Ladakh",
}

# The fourth letter of a PAN says what kind of holder it belongs to.
PAN_HOLDER_TYPES = {
    "P": "an individual", "C": "a company", "F": "a firm or LLP", "H": "a Hindu undivided family",
    "A": "an association of persons", "T": "a trust", "B": "a body of individuals",
    "L": "a local authority", "J": "an artificial juridical person", "G": "a government body",
}

# First four letters of an IFSC identify the bank.
# The first IFSC letters name the bank. Each bank goes by its full name or a common short form.
IFSC_BANKS = {
    "HDFC": ("HDFC",), "ICIC": ("ICICI",), "SBIN": ("STATE BANK", "SBI"), "UTIB": ("AXIS", "UTI BANK"),
    "KKBK": ("KOTAK",), "PUNB": ("PUNJAB NATIONAL", "PNB"), "BARB": ("BANK OF BARODA", "BOB"),
    "YESB": ("YES",), "CNRB": ("CANARA",), "IDFB": ("IDFC",),
}

EIN_INVALID_PREFIXES = {"00", "07", "08", "09", "17", "18", "19", "28", "29", "49", "69", "70",
                        "78", "79", "89", "96", "97"}


def clean(value: str | None) -> str:
    return re.sub(r"[\s\-./]", "", (value or "")).upper()


def gstin_check_char(first_14: str) -> str:
    total = 0
    for i, ch in enumerate(first_14):
        product = ALNUM.index(ch) * (1 if i % 2 == 0 else 2)
        total += product // 36 + product % 36
    return ALNUM[(36 - total % 36) % 36]


def gstin_ok(value: str) -> tuple[bool, str]:
    v = clean(value)
    if not re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]", v):
        return False, "Should be 15 characters, like 27AAPFU0939F1ZV."
    if v[:2] not in GST_STATES:
        return False, f"{v[:2]} isn't a valid GST state code."
    if gstin_check_char(v[:14]) != v[14]:
        return False, "The last character (the check digit) doesn't match, so there is likely a typo."
    return True, ""


def pan_ok(value: str) -> tuple[bool, str]:
    if not re.fullmatch(r"[A-Z]{5}\d{4}[A-Z]", clean(value)):
        return False, "Should be 10 characters, like AAPFU0939F."
    return True, ""


def ein_ok(value: str) -> tuple[bool, str]:
    v = clean(value)
    if not re.fullmatch(r"\d{9}", v):
        return False, "Should be 9 digits, like 12-3456789."
    if v[:2] in EIN_INVALID_PREFIXES:
        return False, f"The IRS doesn't issue EINs starting with {v[:2]}."
    return True, ""


def gb_vat_ok(value: str) -> tuple[bool, str]:
    v = clean(value)
    if v.startswith("GB"):
        v = v[2:]
    if not re.fullmatch(r"\d{9}", v):
        return False, "Should be GB followed by 9 digits."
    total = sum(int(d) * w for d, w in zip(v[:7], range(8, 1, -1))) + int(v[7:])
    if total % 97 != 0 and (total + 55) % 97 != 0:
        return False, "The check digits don't match, so there is likely a typo."
    return True, ""


def ifsc_ok(value: str) -> tuple[bool, str]:
    if not re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", clean(value)):
        return False, "Should be 11 characters, like HDFC0001234."
    return True, ""


def aba_ok(value: str) -> tuple[bool, str]:
    v = clean(value)
    if not re.fullmatch(r"\d{9}", v):
        return False, "Should be 9 digits."
    d = [int(c) for c in v]
    if (3 * (d[0] + d[3] + d[6]) + 7 * (d[1] + d[4] + d[7]) + d[2] + d[5] + d[8]) % 10 != 0:
        return False, "The check digit doesn't match, so there is likely a typo."
    return True, ""


def iban_ok(value: str, country: str = "GB") -> tuple[bool, str]:
    v = clean(value)
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", v):
        return False, "Should start with a country code and 2 digits, like GB82WEST12345698765432."
    if v[:2] != country:
        return False, f"This IBAN is from {v[:2]}, but the vendor's country is {country}."
    if country == "GB" and len(v) != 22:
        return False, "UK IBANs are 22 characters."
    digits = "".join(str(ALNUM.index(c)) for c in v[4:] + v[:4])
    if int(digits) % 97 != 1:
        return False, "The check digits don't match, so there is likely a typo."
    return True, ""


def account_ok(value: str, min_len: int, max_len: int) -> tuple[bool, str]:
    v = clean(value)
    if not re.fullmatch(rf"\d{{{min_len},{max_len}}}", v):
        return False, f"Should be {min_len} to {max_len} digits."
    return True, ""


def email_ok(value: str) -> tuple[bool, str]:
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", (value or "").strip()):
        return False, "This isn't a valid email address."
    return True, ""


def looks_like_other_country(value: str, country: str) -> str | None:
    """Name the country a tax ID actually looks like it's from, for a clearer message."""
    v = clean(value)
    guesses = []
    if country != "IN" and gstin_ok(v)[0]:
        guesses.append("an Indian GSTIN")
    if country != "US" and ein_ok(v)[0]:
        guesses.append("a US EIN")
    if country != "GB" and v.startswith("GB") and gb_vat_ok(v)[0]:
        guesses.append("a UK VAT number")
    return guesses[0] if guesses else None

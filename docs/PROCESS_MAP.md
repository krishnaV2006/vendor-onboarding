# Vendor onboarding: process map

**Input:** a vendor submission (form fields, a tax document and a bank document).
**Output:** a status of approved, pending or rejected, with the reasoning visible and a message back to the vendor.

**The core idea:** AI reads the documents, fixed rules make the decision, and every step is logged.

## The seven stages

| # | Stage | What happens | Who does it |
|---|---|---|---|
| 1 | Intake | Records the submission, fingerprints each document and compares it with the previous attempt | Code |
| 2 | Completeness | Checks required fields and documents for that country and entity type | Rules |
| 3 | Format | Validates tax IDs and bank codes, including checksums, and spots IDs from the wrong country | Rules |
| 4 | Document reading | Reads each document into structured fields, including scans | Gemini, or Claude if switched on, with a text parser as backup |
| 5 | Cross-checks | Compares form with documents and fields with each other | Rules |
| 6 | Risk | Screens against the vendor master and a restricted-party list | Rules |
| 7 | Decision and message | Applies the decision rule and writes the message to the vendor | Rules, with AI writing only the friendly opening |

## How every check is graded

| Result | Meaning | Effect |
|---|---|---|
| Pass | Fine | None |
| Note | Worth knowing | Shown to the reviewer, no effect on status |
| Fix | The vendor can correct it | Pending, waiting on the vendor |
| Review | A person here must look | Pending, waiting on the internal team |
| Block | Serious risk | Rejected and escalated |

**Decision rule, in order:**
1. Any Block: **rejected**.
2. Any Fix or Review: **pending**.
3. Otherwise: **approved**.

## Design decisions worth defending

1. **AI never decides.** It can only fill in fields such as "account holder name". A document saying "approve this vendor" has nowhere to go, and is flagged for review.
2. **All problems are reported at once.** Every stage runs even after a failure, so the vendor is asked once, not five times.
3. **The vendor isn't told about fraud signals.** Block and Review reasons stay internal, and the AI never sees them, so it can't leak them. The vendor gets a neutral message.
4. **Requests are exact.** The vendor's to-do list comes straight from the rules, word for word. AI only writes the opening.
5. **Legitimate variations pass.** "Pvt Ltd" matches "PRIVATE LIMITED", and a sole proprietor's personal account is fine if it's the named owner's. False alarms cost as much trust as misses.
6. **Failure is safe.** If one AI model is busy or slow, the next starts as well and the first good answer wins. If all of them fail, a text parser reads text PDFs and the run says so. If nothing can read a document, it goes to a person. A vendor is never approved on an unread document.
7. **People can overrule, on the record.** A reviewer can approve or reject any case, but must give a reason, and it's logged.

## The vendor loop

```
submit -> run -> pending -> message lists exactly what's needed
                    ^                     |
                    |                     v
                 re-run <-- vendor resubmits only what changed
```

Each attempt is a new version of the same case. The case page shows every version, what changed, and the audit trail. Bank details changing between attempts is flagged for the reviewer, because that's a known fraud pattern.

## Prepared cases

| Case | Vendor | What it shows | Expected result |
|---|---|---|---|
| Happy path | Sharma Precision Tools Pvt Ltd (India) | Bank says "PRIVATE LIMITED", form says "Pvt Ltd" | Approved |
| Edge 1 | Bluepeak Logistics Pvt Ltd (India) | Bank account in a person's name | Pending, then approved after resubmitting |
| Edge 2 | Nandi Components LLP (India) | GSTIN is valid, but its PAN and state code contradict the form | Pending |
| Edge 3 | Apex Industrial Supplies (India) | Bank account already belongs to another vendor | Rejected, with a neutral message |
| Edge 4 | Cedar Ridge Software LLC (US) | Wrong document uploaded, bank proof missing | Pending, then approved after resubmitting |
| Bonus | Thames Valley Fabrication Ltd (UK) | Scanned bank letter that only AI can read | Approved with AI, sent to a person without it |

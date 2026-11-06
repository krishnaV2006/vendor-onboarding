# Assumptions

The guide says to treat ambiguity as part of the exercise: make an assumption, note it, and move on.

| Topic | Assumption | Why | In production |
|---|---|---|---|
| Countries | India, US and UK only | Enough to show that the rules change by country | Add countries as data in the rule tables |
| Required documents | One tax document and one bank document | Covers the fraud and compliance risks the brief names | Add insurance, registration or NDAs by vendor category |
| Bank letter age | Must be dated within 180 days | Common practice, but my choice | Procurement's own policy |
| Shared bank account | Rejected and escalated, even though a reviewer can overturn it. The exception is an Indian GSTIN with the same PAN as the vendor on file: that's the same company registered in another state, so it's only a note | It's the strongest payment-fraud signal, and a false alarm costs one reviewer's time | Could allow known group companies, and add the new GSTIN to the existing vendor |
| Close match on who owns the account | Goes to a person, while a close match on the tax document's name is only a note | Payment fraud often uses a name that's nearly right. UK banks handle a close Confirmation of Payee match the same way | A bank account verification service |
| Restricted parties | A small made-up list | Shows the check without needing a paid data feed | OFAC, UN and EU sanctions feeds |
| Vendor master | A seeded table standing in for the ERP | No real system to connect to | Live lookup in the ERP |
| Tax IDs | Checked for format, checksum and consistency, not against government registers | Registers need paid or restricted API access | GST, IRS TIN Match and HMRC VAT lookups |
| Bank accounts | Checked for format and against the document | Live verification needs a provider | Penny-drop or a bank verification API |
| Messages to vendors | Shown on the vendor's own page, with no email sent | The process works without a mail account | Send through the company's mail system |
| Access | Vendors see only their own application, through a private link they get when they apply. The reviewer console needs one shared passcode | Simple enough for a demo, and nothing internal is visible to a vendor | Single sign-on for reviewers, with named accounts and roles |
| Form checks | The form won't send until every field has the right shape: required, the right format, a valid check digit. Whether the answers are true is left to the process | A typo caught at the form costs the vendor seconds. Caught later, it costs a full round trip | The same, plus address lookup |
| Approving over open fixes | A reviewer can't approve a case that is still waiting on the vendor, only reject it | Approving over unfixed problems skips the checks the vendor was asked to pass | The same, with a second approver for any override |
| AI provider | Gemini's free tier runs the demo. Claude is built in as a switch, but has only been tested against a stand-in server | The free tier makes a public demo possible at no cost, and the reader is one replaceable part | A paid tier with agreed limits |
| Free-tier limits | Daily limits apply, and models can be busy, so runs usually take 5 to 20 seconds and can take longer. When they all run out, documents go to the parser or a person | Failure stays safe, so the worst case is a slower process, not a wrong approval | A paid tier |
| Free-tier data | Google may use free-tier content to improve its products, and its terms limit free-tier apps for users in the EEA, Switzerland and UK | The demo holds only made-up data | A paid tier, which keeps content out of product improvement |
| Hosting | Render's free plan, which sleeps when idle, takes about a minute to wake and wipes its disk | Costs nothing and runs the real process | A paid plan with a persistent database |
| History after a restart | The dashboard restores real runs recorded with Gemini when the app was deployed. Newer runs are lost on restart, and the banner says so | An empty dashboard wouldn't show the history view | Permanent storage, so nothing is lost |
| Re-running prepared cases | A prepared case clears its own earlier vendor record first, so it gives the same result for every viewer | Several people share one demo | Not needed: the duplicate check always applies, as it does here for anything typed into a blank form |
| Data | Test data only, with a banner saying so | A public demo shouldn't hold real bank details | Encryption and access controls |

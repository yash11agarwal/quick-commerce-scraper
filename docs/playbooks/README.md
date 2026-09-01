# Source playbooks

These are the platform playbooks exactly as supplied, verbatim. They are the source of truth
for platform mechanics. `docs/platform-specs/<platform>.md` distils each one into a checkable
spec; when a spec and a playbook disagree, the playbook wins and the spec is wrong.

| File | Covers | Verified | Notes |
|---|---|---|---|
| `01-blinkit-swiggy-2026-08-18.md` | Blinkit, Swiggy Instamart | 18 Aug 2026 | Original session. Holds the Swiggy bundle analysis (section 4.7 to 4.12), the reference Python implementation (section 6) and the captured data tables (section 7) that the rebuilt file drops. |
| `02-blinkit-swiggy-zepto-2026-08-19.md` | Blinkit, Swiggy Instamart, Zepto | 19 Aug 2026 | Rebuilt after a sandbox reset. Blinkit and Swiggy sections carry forward the 18 Aug findings. Zepto is new and only here. |
| `03-bigbasket-2026-08-19.md` | BigBasket, plus the four-platform availability comparison | 19 Aug 2026 | Written as a standalone section 5 for file 02. Its section 1 table replaces the section 1 table of file 02. |

Precedence where they overlap: file 03 section 1 over file 02 section 1; file 02 over file 01
for anything both state; file 01 for anything file 02 omits. No contradictions between the
files were found while writing the specs; the differences are omissions, not conflicts.

Reference probe used in all three: pincode 700048 (Patipukur / Kolkata Station Rd,
Dakshindari, South Dumdum, West Bengal), search term "Mango". The `health` command uses the
same probe so its results can be compared against the playbook data tables.

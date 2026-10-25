# Cross-Account Transaction Context — Doris Feature Spec

**Owner:** Agent B (surfaces) + Agent A (detector)  
**Scope:** Cal Hacks demo — mock data only, no real banking API integrations  
**Status:** Ready to build

---

## 1. Goal

Banks only see transactions inside their own walls. Doris should be a **personal agent** that sees across all of a user's cards and banks, catching vendor bank-change scams that no single institution can spot.

For the demo: when Doris processes a bank-change email, it should also surface whether the **new bank account has ever been seen** across the user's linked institutions. If the account is unknown everywhere, that raises the risk score and flags it on the decision screen.

---

## 2. User Story

As a Cal Hacks judge watching the demo:

1. I open the dashboard at `http://127.0.0.1:8788/`.
2. I click **"Send the demo bank-change email"** (`surfaces/demo/bank_change_demo.eml`).
3. Doris processes the email and flags the bank change to `****8841`.
4. **New:** A panel on the decision screen shows the user's other linked accounts (Chase, Amex, Capital One) and their last-known payment details to Acme Supply.
5. **New:** It flags that `****8841` does not appear in any linked institution — a cross-account anomaly banks would miss.
6. Decision: **CALLBACK** — reinforced by cross-account context.

---

## 3. Deliverables

| # | Deliverable | Location | Owner |
|---|-------------|----------|-------|
| 1 | Mock transaction aggregator module | `detector/transactions.py` | Agent A |
| 2 | Cross-account anomaly check | `detector/checks/cross_account.py` | Agent A |
| 3 | Wire signal into pipeline scoring | `detector/pipeline.py`, `detector/scoring.py` | Agent A |
| 4 | Decision-screen UI panel | `surfaces/` templates | Agent B |
| 5 | Mock fixture data | `detector/fixtures/transactions.json` | Agent A |
| 6 | Demo narrative / pitch bullet | `HANDOFF.md` or `README.md` | Either |

---

## 4. Data Model (Mock)

Create `detector/fixtures/transactions.json`:

```json
{
  "user_id": "demo_user_calhacks",
  "institutions": [
    {
      "name": "Chase",
      "last4": "4321",
      "type": "credit_card"
    },
    {
      "name": "American Express",
      "last4": "1234",
      "type": "credit_card"
    },
    {
      "name": "Capital One",
      "last4": "9901",
      "type": "checking"
    }
  ],
  "vendor_history": {
    "Acme Supply Co.": [
      {
        "amount": 4820.0,
        "currency": "USD",
        "account_last4": "1234",
        "institution": "American Express",
        "date": "2026-10-15",
        "invoice_id": "INV-1041"
      },
      {
        "amount": 4820.0,
        "currency": "USD",
        "account_last4": "1234",
        "institution": "American Express",
        "date": "2026-09-15",
        "invoice_id": "INV-1022"
      }
    ]
  }
}
```

**Mock aggregator API** (`detector/transactions.py`):

```python
class TransactionAggregator:
    def __init__(self, fixture_path: Path):
        self.data = json.loads(fixture_path.read_text())

    def known_accounts_for_vendor(self, vendor_name: str) -> list[dict]:
        """Return payment history for a vendor across all linked institutions."""
        return self.data.get("vendor_history", {}).get(vendor_name, [])

    def account_seen_anywhere(self, account_last4: str) -> bool:
        """Has this account ever appeared in any linked institution?"""
        for history in self.data.get("vendor_history", {}).values():
            for txn in history:
                if txn.get("account_last4") == account_last4:
                    return True
        return False

    def institutions(self) -> list[dict]:
        return self.data["institutions"]
```

---

## 5. New Check: `account_unknown_cross_account`

Create `detector/checks/cross_account.py`:

```python
from __future__ import annotations
from detector.checks.base import Check, Signal, CheckContext
from detector.transactions import TransactionAggregator

class CrossAccountUnknownCheck(Check):
    name = "account_unknown_cross_account"
    weight = "high"   # adjust in scoring if needed

    def __init__(self, aggregator: TransactionAggregator):
        self.agg = aggregator

    def run(self, ctx: CheckContext) -> Signal | None:
        invoice = ctx.invoice
        new_account = getattr(invoice, "bank_account_new", None)  # e.g. "****8841"
        vendor = ctx.vendor_claimed or ctx.payee_name

        if not new_account:
            return None

        known = self.agg.known_accounts_for_vendor(vendor)
        seen_anywhere = self.agg.account_seen_anywhere(new_account)

        if seen_anywhere:
            return Signal(
                self.name, False, "medium",
                f"Account {new_account} seen in linked history for {vendor}",
                {
                    "account_last4": new_account,
                    "seen_anywhere": True,
                    "history": known
                }
            )

        return Signal(
            self.name, True, self.weight,
            f"Account {new_account} NOT found in any linked institution for {vendor}",
            {
                "account_last4": new_account,
                "seen_anywhere": False,
                "institutions_checked": len(self.agg.institutions()),
                "known_history": known
            }
        )
```

Register it in the checks pipeline (check `detector/pipeline.py` or `detector/checks/__init__.py` for the registration pattern used by other checks).

---

## 6. Pipeline Integration

In `detector/pipeline.py`, `Doris.__init__` should instantiate the aggregator:

```python
from .transactions import TransactionAggregator

class Doris:
    def __init__(self, settings=None, ...):
        ...
        fixture = settings.project_root / "detector" / "fixtures" / "transactions.json"
        self.transactions = TransactionAggregator(fixture)
        ...
```

Pass `self.transactions` into the check context so `CrossAccountUnknownCheck` can use it.

Add the signal to the decision payload so it renders in the surfaces UI and gets written to `data/outbox/<decision_id>.json`.

---

## 7. Surfaces UI Panel

On the decision screen (`http://127.0.0.1:8788/d/<decision-id>`), add a section titled **Cross-Account Context**:

```
┌─────────────────────────────────────────────────────────┐
│  Cross-Account Context                                  │
│                                                         │
│  Linked institutions:                                   │
│  • Chase ****4321                                       │
│  • American Express ****1234                            │
│  • Capital One ****9901                                 │
│                                                         │
│  Last payment to Acme Supply Co.:                      │
│  • $4,820 on Oct 15 via Amex ****1234                   │
│  • $4,820 on Sep 15 via Amex ****1234                   │
│                                                         │
│  ⚠️  New account ****8841 NOT found in any linked      │
│     institution — cross-account anomaly                 │
└─────────────────────────────────────────────────────────┘
```

Render from the decision's `signals[]` array (look for `account_unknown_cross_account`). Agent B owns the HTML/template change.

---

## 8. Acceptance Criteria

- [ ] `detector/fixtures/transactions.json` exists with the mock schema above.
- [ ] `python -m detector check surfaces/demo/bank_change_demo.eml` returns a decision whose `signals[]` includes `account_unknown_cross_account` with `fired: true`.
- [ ] Risk score reflects the new signal (should still be CALLBACK for the demo email).
- [ ] Decision screen shows a **Cross-Account Context** panel listing institutions and flagging the unknown account.
- [ ] All existing tests still pass (offline mode).

---

## 9. Demo Pitch Line

> Banks only see transactions inside their own walls. Doris is a personal agent that watches across all your cards and banks — so it catches vendor bank-change scams no single bank can see.

---

## 10. Out of Scope (for this hackathon)

- Real Plaid / Yodlee / Nessie integration
- Storing real transaction history in Doris's SQLite
- Live streaming or webhook ingestion from banks
- Any changes to the audit-chain hash format or vendor schema

---

Build it mock-first, demo it live, ship the narrative.

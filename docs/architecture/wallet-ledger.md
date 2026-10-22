# Demo USD wallet and ledger foundation

The wallet ledger records simulated money owed to each store after a verified
Stripe test purchase. It is an accounting record inside the application, not a
bank account and not a customer payment method. No real money moves.

## How payment accounting works

1. A verified Stripe test payment allocates the purchase total to its store orders.
2. The payment service records one balanced receipt: simulated treasury receives
   the total, and each store's pending account receives its share.
3. Store balances are calculated from immutable entries, not typed into an editable
   balance field.
4. A verified customer cancellation refund adds compensating entries for the
   affected store order. It preserves the original payment's history.

A **journal** is the list of recorded transactions. A **posting** is one debit or
credit entry inside a transaction. For example, USD 10 is represented as 1000
integer cents; it can be recorded as treasury assets and an equal amount owed to
a store. The terms debit and credit describe accounting sides, not credit cards.
An asset is value held by the platform; a liability is value it owes to a store.

Pending means recorded but not released for withdrawal. Available and
withdrawal-pending account buckets exist in the model, but delivery settlement,
withdrawals, and customer wallet spending are not implemented.

The implementation supplies customer/store wallet ownership, USD accounts, an
immutable journal, a transactional posting service, and inspection-only Django
admin. Stripe test checkout and confirmed cancellation refunds use that service.
There is no customer-controlled posting API or wallet interface.

## Models and amounts

- `Wallet`: either one user or one store, never both, enforced in the database; one wallet per owner.
  Stores keep their own wallets even when they share members. Currency is always USD.
- `LedgerAccount`: one pending, available and withdrawal_pending liability account
  per wallet. The singleton platform treasury is an ownerless asset account.
  Database constraints enforce ownership, bucket/kind combinations and USD currency.
- `LedgerTransaction`: globally unique operation key, operation type, canonical
  request fingerprint and posting timestamp. A record represents a completed posting.
- `LedgerEntry`: protected transaction/account references, debit or credit side,
  and positive integer cents. One net entry per account per transaction.

Balances are derived from journal entries, not editable fields. Asset balances are
sum(debits) minus sum(credits); liability balances are the reverse. Every posting
balances, so the sole treasury asset backs all wallet liabilities under this
zero-fee model. Entries and resulting balances are limited to signed bigint range.
No Decimal, float, string or boolean amount is accepted by the posting service.
No currency conversion is performed by this foundation.

## Internal service contract

`apps.wallet.services` exposes:

- `create_wallet(user_id=...)` or `create_wallet(store_id=...)`: locks the owner,
  returns its existing wallet or creates it and all three liability accounts
  atomically. Creation is idempotent and starts with zero balances.
- `get_treasury_account()`: returns or lazily creates the singleton treasury account.
- `account_balance(account)`: returns current journal-derived integer USD cents.
  It is a read helper, not an authorization check or sufficient spending check.
- `post_transaction(operation_key=..., operation_type=..., postings=[Posting(...)])`:
  validates positive cents, unique accounts and equal debit/credit sums; locks and
  checks balances; saves the transaction and entries atomically. Returns the saved
  LedgerTransaction. Identical replay returns that record, even if balances have
  subsequently changed. A reused key with different accounts, amounts, sides or
  operation type raises Django `ValidationError`. Input line order does not matter.
  Missing accounts, overdrafts and unbalanced input also raise `ValidationError`.

Example of a **trusted internal caller** recording USD 100:

```python
from apps.wallet.services import Posting, get_treasury_account, post_transaction

receipt = post_transaction(
    operation_key=f"payment:{attempt.reference}:receipt",
    operation_type="payment_receipt",
    postings=[
        Posting(get_treasury_account().pk, "debit", 10000),
        Posting(store_pending_account.pk, "credit", 10000),
    ],
)
```

This example does not verify payment or grant access. Callers must authorize the
actor, validate payment provenance and ownership, calculate allocation amounts,
and perform related order/status updates in the same outer database transaction.
Never expose arbitrary postings or operation keys as a customer-controlled API.
A posting operation type is an audit label, not an implemented business workflow.

## Concurrency and immutability

PostgreSQL is the supported database for concurrent posting. Every posting locks
the singleton treasury first, then affected accounts in ID order. This deliberately
serializes the demo journal, including conflicting operation keys that reference
disjoint account sets. It is a throughput tradeoff for a simple, reviewable locking
protocol. Future callers must preserve this lock order and must not lock ledger
accounts first. SQLite does not provide the required row-lock behavior.

The posting service checks balances while locked and inserts all entries inside
one transaction. A failed entry insertion or failed outer business transaction
rolls back the complete posting. The posting service itself requires no separate task queue; payment recovery
uses the checkout workers.

Ordinary model saves, queryset updates/deletes and bulk mutations cannot change
financial records. Journal insertions must use the posting service. Wallet and
account identifiers/ownership are immutable, and financial relations use PROTECT.
Admin forbids adds, changes and deletion, including for superusers, while exposing
account balances and journal entries for inspection.

These are application write guards, not database triggers: raw SQL, private ORM
managers or explicit base-class methods can bypass them. No row-level SQL check
can enforce the cross-row balanced-journal invariant here. Trusted code must use
the posting service; it intentionally uses base ORM insertions only after validation
and while holding locks. Administrative database writes are outside this contract.

## Integrations and limits

Stripe test checkout and ProviderEvent are implemented; see [setup](../api/checkout/stripe.md).
Stripe test card cancellation refunds are implemented through
[customer orders](../api/ecommerce/user-orders.md), reversing the affected store pending
credit and treasury receipt after provider confirmation. No customer wallet API
or UI is added. No settlement/hold records, seven-day jobs,
withdrawal workflow, mock-bank adapter, wallet spending or fee logic are implemented.
COD does not create any ledger entries. A general reversal API and `reverses_id` link remain deferred; order refunds
instead use a protected OrderRefund-to-reversal link and one refund per order; posted history must never be edited to simulate a reversal.
PaymentAttempt holds a protected one-to-one receipt reference. Settlement and
withdrawal foreign keys remain deferred with those workflows.

Stripe uses this service inside atomic payment finalization and protects unresolved
stock reservations across preview and placement. Delivery and settlement release
remain separate unimplemented workflows.

See the [Stripe test checkout contract](../api/checkout/stripe.md) for implemented
payment behavior and the [shared SBP index](../../../docs/specific-business-processes/README.md)
for available process diagrams. Delivery and settlement remain outside the current contract.

# 🛡️ ShipInsure Covered Orders (Shopify + Recharge) – Take-Home

> [!IMPORTANT]
> **Reviewers: please read the [Technical Design Doc](docs/TDD.md) first.** It holds the thinking behind this take-home: the problem, the assumption the whole linkage rests on, the architecture, the coverage rules, the trade-offs, what was deliberately left out, and open questions. This README only covers what was built and how to run it.

A Python + [Chalice](https://aws.github.io/chalice/) prototype that treats Shopify and Recharge webhooks as the system of record. It's built for Recharge on today's Shopify checkout. A renewal order that has no ShipInsure line item inherits coverage from the subscription's original order. Recharge creates no record linking the two, so that order is found by matching customer, product and time. The billing ledger ends with the same balance per order whether events arrive once, many times, or out of order.

## How it works

1. **Persist first.** Each delivery is stored raw in `events_inbox` and committed before anything else happens. A duplicate hits a unique key, bumps `delivery_count`, and is not processed again.
2. **Normalize.** Per-source adapters upsert the latest state, guarded by `updated_at` so a stale event never overwrites newer state. Recharge sits behind a `SubscriptionPlatform` Protocol.
3. **Recompute.** A pure `resolve()` re-derives coverage for every order from stored state after each event, including matching each subscription to its original order. It never reacts to "what just happened". A missing or ambiguous match leaves the renewal pending with a reason, never billed on a guess.
4. **Post the diff.** For each order, *should be billed − already billed* is appended to an append-only ledger as a charge or a negative adjustment, in one atomic SQL statement.

## Run it

Needs Python 3.11+ (tested on 3.13) on macOS or Linux.

```bash
python3 -m venv .venv && source .venv/bin/activate
```

```bash
pip install -r requirements-dev.txt
```

### Tests

```bash
pytest
```

That takes about 15 seconds, including every ordering of four event sequences (24 for the happy path, 120 each for the late cancellation, item removal and ambiguous-match scenarios).

### Replay a scenario

```bash
python replay.py scenarios/out_of_order.json
```

Sends fixture webhooks through the same ingest code the routes use, against a throwaway SQLite file. It prints what is pending after each delivery, then the inbox, coverage, pending, ledger and balance tables. Add `--db shipinsure.db` to keep the database.

| Scenario | What it shows |
| --- | --- |
| `happy_path` | Original order → subscription → renewal → recurring Recharge order: one direct and one inherited charge |
| `duplicates` | Every delivery sent 3×: one inbox row each with `delivery_count = 3`, ledger unchanged |
| `out_of_order` | Renewal side first: R is pending for three different reasons, then charged |
| `original_order_last` | The original order arrives last: R is pending (`missing_subscription_origin`) until it does |
| `ambiguous_origin` | Two orders could be the original: R stays pending (`ambiguous_subscription_origin`), never charged |
| `renewal_not_origin` | A second subscription bought right after a renewal: the renewal isn't mistaken for its original order |
| `late_cancellation` | Renewal cancelled after billing: −2.50 adjustment |
| `item_removed` | ShipInsure item removed from the original order: both orders reversed |
| `subscription_cancelled` | Subscription cancelled: the earlier renewal stays billed, a later one is not covered |

### Local server + Postman

```bash
chalice local --stage local --port 8000
```

- **Postman:** import [`postman/shipinsure-covered-orders.postman_collection.json`](postman/shipinsure-covered-orders.postman_collection.json). Send the "Happy path" folder in order (or in any order; the final ledger is the same), then try "Changes after billing" and "Matching edge cases". Shopify requests use fixed webhook ids, so sending one twice shows dedupe.
- **Database:** the `local` stage writes `shipinsure.db` in the repo folder (git-ignored). Delete it and restart the server to start over. Plain `chalice local` uses the `dev` stage, which is the deploy config and writes to `/tmp/shipinsure.db` because Lambda can only write to `/tmp`.

| Endpoint | Notes |
| --- | --- |
| `POST /webhooks/shopify` | Needs `X-Shopify-Topic`, `X-Shopify-Shop-Domain` and `X-Shopify-Webhook-Id` (the dedupe key) |
| `POST /webhooks/recharge/{resource}/{action}` | Topic comes from the path, e.g. `/webhooks/recharge/order/created`. Dedupe key is `{topic}:{object_id}:{updated_at}` |

Responses: `200` when the delivery is stored (new, duplicate, or stored but processing failed, in which case the row is marked `failed` for retry); `400` for missing headers or a body that isn't a JSON object; `5xx` only if the raw delivery could not be stored.

## AI usage

All code, tests and fixtures were AI-generated against the TDD. The time went into the design, the research behind it, and reviewing and running the result: about 2 hours in total, including a redesign after the Recharge research. The breakdown is in [TDD §8](docs/TDD.md#8-time-and-ai-usage).

## Layout

```text
app.py                  Chalice routes (thin HTTP layer)
chalicelib/
  models.py, db.py      SQLAlchemy 2.0 models (five tables) and engine setup
  ingest.py             persist -> normalize -> recompute -> post diff
  adapters/             Shopify and Recharge payloads -> normalized records
  state.py              updated_at-guarded upserts, snapshot read
  resolve.py            coverage rules, pure functions
  ledger.py             ledger posting as a diff
  scenarios.py, report.py   replay loader and terminal tables
replay.py               scenario replay CLI
fixtures/               Shopify REST Order and Recharge 2021-11 payloads
scenarios/              delivery sequences used by replay.py and the tests
postman/                Postman collection for the local server
docs/TDD.md             the design doc
```

## Decisions made while building

Details the TDD leaves open:

- **Spotting the ShipInsure line item** (TDD open question #3): a case-insensitive `shipinsure` match on the line item's vendor or title, configurable with `SHIPINSURE_LINE_ITEM_KEYWORD`. Premium is `price × current_quantity`, so an item removed by an order edit or refunded counts as zero.
- **Joining across systems:** Recharge orders join Shopify orders on order id alone, because Recharge payloads carry no shop and Shopify order ids are globally unique.
- **Pending or not covered means "should bill 0",** so an order that loses coverage has its earlier charge reversed. Every balance is therefore a function of stored state only.
- **Match window:** an original order must be placed within 60 minutes before the subscription was created, configurable with `SUBSCRIPTION_ORIGIN_WINDOW_MINUTES` (TDD open question #5).
- **Stored but unused:** Recharge order `status`, and `checkout`-type Recharge orders from the legacy checkout (TDD §9). Neither affects coverage.
- **Dependencies are split:** `requirements.txt` (runtime, packaged by `chalice deploy`) and `requirements-dev.txt` (adds pytest).

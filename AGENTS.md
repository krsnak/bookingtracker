# BookingTracker agent instructions

## Non-negotiable invariant

Track the exact existing reservation. Never optimize for the cheapest hotel
room and never compare a price until the matcher has accepted an equivalent or
explicitly-labelled better offer.

Do not silently substitute room type, room count, dates, occupancy, meal plan,
breakfast, cancellation protection, or relevant payment conditions. A lower
price for a materially worse rate is not an alert.

## Safety and privacy

- This is a local, single-user application. Do not add Supabase, cloud hosting,
  multi-tenancy, Gmail/IMAP, inbound email, or automated booking/cancellation.
- Production is a Raspberry Pi 4 aarch64 Home Assistant add-on. Keep core
  business logic platform-independent; Docker, Ingress, `/data`, and remote
  browser components must stay in adapters/deployment layers.
- Never automate Booking credentials, store credentials, bypass CAPTCHA, or
  add anti-bot evasion. A challenge means stop and request manual action.
- `data/booking_profile` contains session state: do not inspect, print, log,
  commit, copy, or expose cookies/tokens/session storage.
- Do not commit `.env`, SQLite data, logs, sanitized fixtures containing PII,
  or real Booking confirmation data.
- noVNC/VNC is manual recovery only and must be protected by Home Assistant
  Ingress. Never expose a public remote-browser port or profile export.

## Repository truth and working state

The conversation defines the user's goal; it never overrides the observed
checkout. Resolve conflicts using this order:

1. current working tree;
2. current diff;
3. `HEAD`;
4. Git history;
5. local agent state;
6. this file;
7. relevant project documentation;
8. conversation summaries.

At task start, use the smallest useful bootstrap: this file, `git status
--short --branch`, `git log --oneline -n 10`, `.agent/CURRENT_TASK.md` when it
exists, the relevant diff, and directly affected files/tests. Inspect the
relevant module and tests before editing. Use symbol search, call sites, and
targeted line ranges before loading long modules or documents.

Do not revert or overwrite an existing dirty tree. Establish whether it is
unfinished work, preserve it, and limit edits to the requested scope.

`.agent/CURRENT_TASK.md` is an ignored local cache, never a source of truth or
a committed scratchpad. Reconstruct it from Git and the current diff if a
workspace is not persistent. Keep it to 20–50 lines (hard limit: 80) with only:

```md
# Current task

Goal:
Scope:
Do not touch:
HEAD:
Base:
Version:
Lifecycle:
Decision:
Completed:
Validated:
Open:
Next:
```

Never put transcripts, full logs/diffs, history, PII, Booking data, cookies,
tokens, credentials, or stack traces in local agent state.

## Engineering loop

Complete one logical loop autonomously; do not stop merely to make the user
transfer context between agents:

```text
inspect → classify → select cheapest sufficient tier → implement
→ targeted validation → self-review → fix → targeted re-validation
→ final gate → classify lifecycle state → report
```

If the environment supports delegation, use it internally for independent,
bounded work. If it does not, finish the loop with the current model. Model
routing is an optimization, never a dependency: when model selection is
unavailable, reduce context and reasoning to the task's actual complexity and
continue.

Classify the lifecycle truthfully in handoff and reports: `implementation`,
`validated locally`, `release validation pending`, `installable Home Assistant
release`, or `production validated`. Do not call a change installable until
the project release gate has passed and the add-on version/package metadata is
consistent. State any remaining manual/live Booking or Raspberry Pi risk.

## Model routing and decision contracts

Choose the tier deterministically before work; do not trial a cheaper tier
when the risk clearly requires stronger reasoning.

- **Tier S — strategic reasoning:** architecture; safety invariants; matcher,
  comparable-price policy, pricing, `PRICE_DROP`, tax/currency safety;
  browser auth/session/CAPTCHA; persistent profile; scheduler locking or
  concurrency; availability classification; database migrations; PDF identity
  evidence; privacy/security boundaries; unclear root causes; cross-subsystem
  design; and final safety review of a critical change. Use the strongest
  available reasoning model (for example Sol) only here.
- **Tier M — standard implementation:** default for clear Python contracts,
  FastAPI, presentation/UI, CSS/SVG, fixtures, ordinary parser-independent
  code, unit/integration tests, accessibility, and single-subsystem refactors.
- **Tier L — mechanical:** Markdown/status updates, boilerplate, renames,
  import cleanup, simple constants/CSS/HTML, formatting, approved test
  expectations, and local state. It must not make business or security
  decisions.

Tier S triggers include `app/matching/`, pricing/comparability, `PRICE_DROP`,
tax/currency safety, browser authentication, CAPTCHA, persistent profiles,
scheduler locking/concurrency, availability classification, database
migrations, PDF identity evidence, and security/privacy boundaries. Tier M
normally owns `app/web/`, templates, CSS, UI tests, presentation helpers,
FastAPI routes, and ordinary parser-independent Python. UI needs Tier S only
when it decides whether a price/delta/graph may appear, comparison category,
or healthy versus technical outcome.

For a Tier S decision, write a short 150–400-token decision contract before
implementation, then de-escalate to Tier M/L where possible:

```text
Decision
Files
Invariant
Required behavior
Forbidden behavior
Acceptance tests
```

Do not reopen a valid unchanged decision contract. Escalate Tier M to Tier S
only for an unclear requirement, security/business trade-off, unexpected
domain behavior, scope expansion, repository conflict, or ambiguous invariant
— never for lint, imports, typos, CSS, a simple failed test, or mechanical
refactoring.

## Implementation and validation discipline

1. Keep Booking DOM selectors in the adapter/selectors layer.
2. Use typed Pydantic models, explicit enums/statuses, `Decimal` for money,
   foreign keys, and append-only price history.
3. Prefer deterministic parsing/matching. LLM output must be typed, validated,
   reviewable, and never saved directly.
4. Add or update fixture-based tests for behavior changes. Do not run live
   Booking tests in normal CI.
5. Assume Home Assistant mounts the app below an arbitrary base path: do not
   hard-code root-relative links, redirects, static assets, or API URLs.

Use the narrowest useful test progression: a failing test, then its file,
then the subsystem. Run the full pytest suite only on a stable diff or at the
final gate. Do not repeat expensive validation when `HEAD`, relevant diff, and
validated subsystem are unchanged, except at the final release gate. Record
only this compact cache in `.agent/CURRENT_TASK.md`:

```text
Validated HEAD:
Validated diff scope:
Tests:
Visual validation:
```

Invalidate validation proportionately:

- documentation-only: `git diff --check` plus privacy/secrets sanity;
- CSS/templates: affected UI/template tests and, when layout changed, one
  visual smoke check;
- presentation Python: relevant unit tests plus UI/Ingress subset;
- core logic: subsystem tests, then full pytest at final gate;
- release: the complete project-defined release validation.

Do not rerun screenshots when the relevant layout diff is unchanged; record
desktop/mobile validation and the diff it covered. If full pytest passed and
only documentation changes afterward, do not rerun it.

## Handoffs and reporting

Give another model only a repository-grounded handoff, ideally under 250 tokens
(hard limit: 500):

```text
Task:
Files:
Decision:
Invariant:
Acceptance:
Do not touch:
```

The receiving model reads repository sources instead of receiving chat history.
At completion, report changed files, targeted validation and its scope, the
lifecycle state, and unverified live-site risks. Do not claim tests or a Home
Assistant installation that did not run.

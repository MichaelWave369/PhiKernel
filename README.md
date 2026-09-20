# PhiKernel v0.2.0

**PhiKernel** is a local-first constitutional runtime for governed computation, continuity, trust-aware orchestration, and bounded machine authority.

It runs **on top of Linux**. Linux remains the host operating-system substrate for hardware, filesystems, processes, drivers, and scheduling. PhiKernel adds a higher-order runtime that decides how computational participants may act, what evidence may move forward, what authority is currently valid, how failures affect future routing, and when human authorization is required.

PhiKernel began as an anchored local runtime for identity, encrypted continuity, heartbeat scheduling, field telemetry, and deterministic routing. v0.2 keeps that substrate and adds an explicit constitutional execution model.

> **Core rule:** capability is not authority.

---

## Project status

**Version:** `0.2.0`  
**Status:** open alpha / constitutional runtime milestone  
**License:** MIT for project-owned code and documentation unless otherwise noted

v0.2 establishes a tested constitutional runtime core for:

- governed state transitions
- temporary scoped authority
- provenance and evidence carriage
- contradiction handling
- failure and success routing memory
- stratified mutability
- workload-local computational time
- temporary coalition identities
- governed premise state
- relational routing
- shadow evaluation
- evidence-before-promotion
- human-gated bounded control
- mode-aware constitutional orchestration
- persistent constitutional state with hash-chained history
- restart-safe bounded constitutional action transactions
- non-replaying constitutional action crash recovery
- Anchor-signed constitutional head attestations

The current CLI still exposes the original substrate and legacy coach-routing path. The v0.2 constitutional layers are implemented as Python runtime APIs and are not yet fully wired into `phik route` / `phik ask`.

That distinction is intentional: implementation, verification, and live authorization are separate milestones.

---

## Constitutional model

PhiKernel v0.2 treats a proposed state transition as the central governed object.

```text
CURRENT STATE
     |
     v
participant proposes change
     |
     v
constitutional checks
     |
     +--> LICENSE
     +--> DEGRADE
     +--> QUARANTINE
     +--> REFUSE
     |
     v
receipted next state
```

The runtime is designed around several non-negotiable distinctions:

```text
CAPABILITY != AUTHORITY
SUCCESS != AUTHORITY
POPULARITY != AUTHORITY
ROUTING != AUTHORIZATION
OUTPUT != CARRIAGE
ELEGANCE != EVIDENCE
PREMISE != FACT
TIME != AUTHORITY
TICK RATE != AUTHORITY
READY FOR REVIEW != AUTHORIZED
ADVISE != STEERING AUTHORITY
BOUNDED CONTROL != GENERAL CONTROL
FAILURE MAY ALTER ROUTING
FAILURE MAY NOT ALTER LAW
TEMPORARY AUTHORITY EXPIRES
HUMAN SILENCE != CONSENT
HUMAN CONSTITUTIONAL AUTHORITY IS FINAL
```

---

## Runtime modes

Crane Fly vNext progresses through explicit runtime modes.

### SHADOW

- legacy `CoachRouter` remains authoritative
- relational routing evaluates in parallel
- disagreement and hard-block discoveries are receipted
- vNext has zero steering authority

### ADVISE

- legacy routing remains authoritative
- vNext may surface a recommendation
- recommendation has zero steering authority
- promotion from SHADOW requires Witness Bench evidence plus explicit human authorization

### BOUNDED_CONTROL

- steering is permitted only through a finite human-authorized control lease
- the lease is bound to an exact actor, contract, warrant, scope, resource budget, action count, clock budget, and lifetime
- each action still requires a governed Transition license
- terminal refusal does not silently fall back to legacy execution
- failure, veto, quarantine, or seal may collapse privilege back toward SHADOW

```text
SHADOW
  |
  | evidence + human authorization
  v
ADVISE
  |
  | control witness evidence + human authorization
  v
BOUNDED_CONTROL
  |
  | failure / veto / containment
  v
SHADOW
```

Privilege may collapse automatically. Privilege may not expand automatically.

---

## Architecture

### Original local substrate

The original v0.1 braid remains part of PhiKernel.

**Anchor**  
Cryptographic local identity and trust root.

- Ed25519 signatures
- Argon2id passphrase protection
- signed manifest verification
- stable local identity metadata

**Capsule**  
Encrypted continuity snapshots.

- AES-GCM sealed state
- manifest-linked continuity
- verified restore / rehydrate flow
- guarded memory writes

**Heart**  
Monotonic runtime pulse.

- recurring maintenance work
- anchor checks
- checkpoint scheduling
- runtime status persistence

**Coherence / TIEKAT telemetry**  
Runtime observation and drift scoring.

- coherence measurements
- field telemetry
- action recommendations
- TIEKAT is used as a runtime scoring model, not as a claim of closed physical law

**Shell**  
Human-facing local CLI.

**Legacy Router**  
Deterministic Titan / Flow / Sage routing retained as the currently authoritative shell routing path.

### Constitutional runtime layers

**Warrants + governed transitions**  
`warrant.py`, `transition.py`

- bearer-bound, non-transferable temporary authority
- scoped operation/target permissions
- finite resource budgets
- expiry and revocation
- atomic resource preflight
- immutable successor warrants and receipts

**Carriage + contradiction**  
`carriage.py`, `contradiction.py`

- output does not automatically become shared evidence
- provenance and parent lineage checks
- citation rights
- quarantine / refusal
- first-class unresolved contradiction state
- no automatic truth selection

**Failure scars + saturation**  
`scar.py`, `saturation.py`

- decaying failure memory
- success-concentration pressure
- routing effects without authority effects
- anti-monopoly probing only when viable alternatives exist

**Stratified mutability**  
`mutability.py`

- L0 Constitution: runtime may petition, human authority applies
- L1 Policy: runtime may propose, bounded human grant applies
- L2 Routing Weather: runtime may write ephemeral decaying routing pressure

**Clock skins**  
`clockskin.py`

- workload-local computational time
- wall-rate and explicit-signal modes
- finite tick leases
- human time does not advance from waiting
- cross-skin transfer requires current carriage rights

**Coalition charters**  
`coalition.py`

- temporary multi-participant capability unions
- coalition identity is distinct from member identity
- coalition authority does not transfer to members
- degradation reduces capability, not constitutional authority
- disband revokes temporary grants

**Premise objects**  
`premise.py`

- ACTIVE / CHALLENGED / QUARANTINED / RETIRED
- verified failures and contradictions may challenge premises
- one evidence source cannot be replayed for extra governance weight
- challenged premises can add routing pressure
- quarantined premises can block dependents
- premises may propose policy or petition constitution, never apply either

**Relational routing / Crane Fly vNext**  
`relational_router.py`

Routing is split into two stages:

1. hard admissibility
2. relational conductance among survivors

Hard gates include warrant validity, capability, resource sufficiency, citation rights, contradiction state, premise state, coalition state, clock state, and explicit human blocks.

Soft routing pressure may include scars, saturation, L2 weather, challenged premises, clock pressure, resource pressure, degraded coalition state, and human preference penalties.

A hard-blocked route receives no conductance score.

**Shadow runtime**  
`routing_shadow.py`

Runs legacy routing and Crane Fly vNext in parallel and produces comparison receipts such as:

- `AGREE`
- `DIVERGE`
- `VNEXT_BLOCKS_LEGACY`
- `VNEXT_NO_ROUTE`
- `VNEXT_ERROR`
- `LEGACY_UNMAPPED`

Shadow steering authority is always `NONE`.

**Witness Bench + promotion gate**  
`witness_bench.py`

Promotion evidence includes:

- sample count
- input diversity
- candidate coverage
- deterministic replay
- invariant preservation
- behavioral reality
- conflict review
- vNext error rate

The Bench may conclude `READY_FOR_HUMAN_REVIEW`. It cannot authorize itself.

SHADOW -> ADVISE requires an explicit human seal bound to the exact proposal and witness report.

**Control Witness Contract**  
`control_witness.py`

ADVISE -> BOUNDED_CONTROL requires a separate, stronger control-witness process.

The safe v0.2 control profile requires:

- exact scopes, with no glob widening
- finite per-rule and total action counts
- finite resource budgets
- finite clock budget
- finite lifetime
- required evidence
- declared rollback path
- interruptibility
- human veto
- post-action receipts
- terminal failure semantics

Human authorization creates a dedicated finite control warrant. Each action still goes through the normal governed-transition evaluator.

**Constitutional execution orchestrator**  
`constitutional_runtime.py`

Composes runtime mode, legacy routing, Crane Fly vNext, operator control state, bounded-control grant lineage, control session state, and action licensing.

It enforces:

```text
SHADOW  -> observe only
ADVISE  -> recommend only
BOUNDED_CONTROL -> steer only through exact human-authorized lease
```

Quarantine and seal override routing mode. Review/recovery state can hold steering without spending the control warrant. Terminal control refusal does not fall back to legacy execution.

The orchestrator licenses actions but does not itself perform external side effects.

**Persistent constitutional state**  
`constitutional_store.py`

PhiKernel persists constitutional authority state separately from ordinary runtime
configuration.

The store keeps:

- one canonical `.phik-runtime/constitutional/state.json` snapshot
- append-only `.phik-runtime/constitutional/history.jsonl`
- deterministic SHA-256 linkage between successive snapshots
- typed reconstruction of promotion state, authorization receipts, control
  contract, bounded grant, dedicated warrant, and live control session
- fail-closed lineage checks on load
- monotonic session resource/action/clock accounting

Missing constitutional state means genesis `SHADOW`. Existing malformed,
tampered, stale, expired, or lineage-inconsistent state does **not** silently
reset to SHADOW.

The state/history hash chain is an integrity/replay mechanism. PhiKernel now
supports a separate Anchor attestation layer that signs the exact current
constitutional chain heads without rewriting prior history.

The shell consumes this store for `phik constitutional status` and
`phik constitutional route`. Promotion state is resumed only after the store
reconstructs and validates its typed authority lineage.

**Constitutional action transactions**  
`constitutional_action.py`

PhiKernel can consume a persisted BOUNDED_CONTROL lease through an explicit
bounded action transaction.

The initial v0.2 executor allowlist is intentionally narrow:

```text
execute:runtime/adapter/legacy
execute:runtime/adapter/tiekat_v50
```

It does not run arbitrary subprocesses, shell commands, or Python code.

The transaction is write-ahead and fail-conservative:

```text
ControlActionRequest
    ↓
constitutional route + control license
    ↓
persist pending action / spent budgets
    ↓
journal LICENSED
    ↓
execute allowlisted runtime adapter
    ↓
journal OUTCOME + post-action receipt
    ↓
persist completed session or collapsed SHADOW
    ↓
journal COMMITTED
```

A process interruption after licensing therefore leaves a persisted pending
action with its resource/action/clock spend already accounted for. A later
action cannot silently reuse that authority.

The action evidence journal lives at:

```text
.phik-runtime/constitutional/actions.jsonl
```

and is independently SHA-256 hash-chained.

**Constitutional action recovery**  
`constitutional_recovery.py`

Recovery is reconciliation, not retry.

After a process interruption, PhiKernel compares the persisted control session
with the hash-chained action journal:

```text
pending + LICENSED only
→ execution outcome unknown
→ NEVER replay
→ verified no-op rollback for the v0.2 analysis-only executor
→ terminal control collapse to SHADOW

pending + LICENSED + OUTCOME
→ apply the already-journaled outcome
→ do not execute again
→ persist completed session or terminal collapse

completed state + OUTCOME + no COMMITTED marker
→ repair the journal commit marker
→ do not execute again

expired active bounded lease
→ structurally inspect for recovery only
→ stop/revoke/collapse to SHADOW
```

Recovery may consume surviving evidence and reduce privilege. It may not refund
spent resources, rewind action/clock accounting, create authority, or call an
executor.

The recovery shell surfaces are:

```bash
phik constitutional recovery status
phik constitutional recovery reconcile
```

The structural recovery loader may inspect expired authority solely so it can be
removed or so an already-receipted outcome can be finalized. It is not an
alternate authorization path.

**Anchor-backed constitutional attestations**  
`constitutional_attestation.py`

PhiKernel can cryptographically bind the current constitutional persistence
heads to the existing StateAnchor Ed25519 identity.

The attestation signs:

- current constitutional state snapshot hash
- constitutional state-history length
- current action-journal event hash
- action-journal length
- exact Anchor ID
- exact signed Anchor manifest hash
- previous constitutional attestation hash
- an explicit protocol domain separator

The detached attestation chain lives at:

```text
.phik-runtime/constitutional/anchor_attestations.jsonl
```

A signed current head authenticates the structural hash history behind that head
without rewriting older snapshots or action events.

Verification deliberately distinguishes:

```text
VALID + CURRENT
→ current constitutional heads are Anchor-authenticated

VALID + STALE
→ signature is authentic, but newer constitutional writes exist

INVALID
→ Anchor identity, signature, or attestation chain failed verification
```

The shell surfaces are:

```bash
phik constitutional anchor bind --passphrase "<anchor-passphrase>"
phik constitutional anchor verify
```

The passphrase is required only to create a new detached signature. Verification
uses the public key from the signed Anchor manifest.

An Anchor attestation proves identity binding of persisted constitutional
evidence. It does **not** itself grant runtime authority, promote routing mode,
or retroactively turn the structural `HumanAuthoritySeal` object into a
cryptographic human authorization.

---


## Runtime and trust controls

PhiKernel also includes trust and operator-control surfaces:

- `anc_bridge.py` for runtime guard integration
- `trust_runtime.py` for native trust outcomes
- `control_state.py` for persistent review, quarantine, seal, and recovery state
- `tiekat_v50.py` and `tiekat_v69_runtime.py` for runtime telemetry/adapters

Operator control actions exposed by the CLI include:

```text
approve
review
quarantine
seal
clear_review
release_quarantine
recover_from_seal
begin_recovery
refresh
```

Quarantine and seal are stronger than routing preference. They block execution until explicit recovery flow permits progress.

---

## Repository layout

```text
PhiKernel/
├── README.md
├── LICENSE
├── PHI_COMMONS.md
├── LICENSE_HISTORY.md
├── THIRD_PARTY_NOTICES.md
├── pyproject.toml
├── src/phikernel/
│   ├── anchor.py
│   ├── capsule.py
│   ├── heart.py
│   ├── coherence.py
│   ├── shell.py
│   ├── router.py
│   ├── anc_bridge.py
│   ├── trust_runtime.py
│   ├── control_state.py
│   ├── warrant.py
│   ├── transition.py
│   ├── carriage.py
│   ├── contradiction.py
│   ├── scar.py
│   ├── saturation.py
│   ├── mutability.py
│   ├── clockskin.py
│   ├── coalition.py
│   ├── premise.py
│   ├── relational_router.py
│   ├── routing_shadow.py
│   ├── witness_bench.py
│   ├── control_witness.py
│   ├── constitutional_runtime.py
│   ├── constitutional_shell.py
│   ├── constitutional_store.py
│   ├── constitutional_action.py
│   ├── constitutional_recovery.py
│   └── constitutional_attestation.py
└── tests/
    └── ...
```

---

## Requirements

- Python 3.11+
- Linux recommended for the primary local runtime

Runtime dependencies:

- `cryptography`
- `argon2-cffi`

Development dependency:

- `pytest`

---

## Installation

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .[dev]
```

Then:

```bash
python -m pytest
phik --help
```

---

## First run

```bash
phik init \
  --passphrase "change-me" \
  --sovereign-name "Tal-Aren-Vox" \
  --user-label "Ori"

phik anchor show
phik pulse once
phik field
phik status
```

Create continuity:

```bash
phik capsule seal \
  --passphrase "change-me" \
  --json-text '{"thread":"working session","phase":9}' \
  --summary "Manual checkpoint" \
  --tag checkpoint
```

Build a context bundle and use the current legacy routing surface:

```bash
phik think "How is the field?"
phik route "How should I begin?"
phik ask "How should I begin?"
```

Inspect and use the constitutional routing state:

```bash
phik constitutional status
phik constitutional route "How should I begin?"
phik --json constitutional route "I need momentum to create and start this draft"
```

When an exact persisted BOUNDED_CONTROL contract authorizes a normalized runtime
adapter target, execute one bounded action:

```bash
phik --json constitutional action \
  --adapter legacy \
  --json-text '{"prompt":"normal"}' \
  --resource compute_ms=10 \
  --clock-ticks 2 \
  --evidence evidence:approved \
  --rollback-ref rollback:no-side-effect
```

The actor comes from the persisted control session. The operation is fixed to
`execute`, and the target is derived from the allowlisted adapter. CLI input
cannot substitute a different actor or arbitrary execution target.

Inspect or reconcile an interrupted bounded action:

```bash
phik constitutional recovery status
phik constitutional recovery reconcile
```

Anchor-bind the current constitutional state/action heads and verify them later:

```bash
phik constitutional anchor bind --passphrase "change-me"
phik constitutional anchor verify
```

Recovery never reruns the action. If execution outcome is unknown, the v0.2
analysis-only executor is treated with a verified no-op rollback and bounded
privilege collapses to SHADOW. If an OUTCOME receipt survived, PhiKernel applies
that evidence without executing again.

With no persisted constitutional snapshot, the route command begins from
genesis `SHADOW`. If a validated ADVISE snapshot exists, the shell resumes
ADVISE and surfaces Crane Fly vNext as an advisory route while the legacy coach
remains authoritative.

If validated BOUNDED_CONTROL state exists, `constitutional route` may inspect
routing under that mode but cannot license steering because the route command
does not create a `ControlActionRequest`. The dedicated control warrant,
action count, resource budget, and clock budget remain untouched.

The shell-generated coach candidate warrants are scoped only to
`route-evaluate:coach/<Coach>`; they do not authorize tool execution or
bounded steering.

Run adapter-backed analysis:

```bash
phik execute --adapter legacy --json-text '{"prompt":"normal"}'
phik execute --adapter tiekat_v50 --json-text '{"prompt":"normal"}'
```

Apply operator control:

```bash
phik control review --note "manual check required"
phik control quarantine --note "contain suspected contamination"
phik control release_quarantine --note "operator review complete"
```

---

## CLI integration note

The shell still routes `phik route` and `phik ask` through the deterministic
legacy `CoachRouter`.

The v0.2 constitutional runtime is now exposed explicitly through:

```bash
phik constitutional route "<prompt>"
```

The shell loads constitutional mode from the validated persistent state store.
It does not manufacture ADVISE or BOUNDED_CONTROL from command-line flags.

- missing snapshot -> genesis SHADOW
- validated ADVISE snapshot -> ADVISE resumes
- validated BOUNDED_CONTROL snapshot -> mode/grant/session lineage resumes, but
  `constitutional route` does not spend the control lease because it has no
  exact control action
- corrupt, tampered, stale, or expired persistence -> shell fails closed

If seal or quarantine blocks an active persisted BOUNDED_CONTROL state, the
orchestrator's automatic collapse to SHADOW is persisted back to the
constitutional history so releasing containment cannot resurrect the old
bounded lease.

`phik constitutional action` is the only shell path in this milestone that
consumes the bounded-control lease. It requires an exact contract match and
persists the pending licensed action before invoking the executor. Successful
or failed outcomes are receipted and written to the action journal before the
session is unlocked or privilege is collapsed.

`phik constitutional recovery status` inspects interrupted action state without
mutation. `phik constitutional recovery reconcile` never calls the executor:
it applies already-journaled OUTCOME evidence, repairs a missing COMMITTED
marker, or conservatively collapses unknown/expired bounded authority.

`phik constitutional anchor verify` reports whether the detached Anchor
attestation chain is valid and whether its latest signature still covers the
current constitutional state/action heads. A valid-but-stale signature is
reported as stale rather than treated as current authentication.

This keeps the repository from confusing:

```text
IMPLEMENTED
VERIFIED
EXPOSED
AUTHORIZED
```

Those are separate states in PhiKernel.

---

## Security and governance notes

PhiKernel uses standard cryptographic primitives for identity and encrypted continuity. Symbolic or field models do not replace cryptographic verification.

The runtime is designed to fail conservatively:

- expired or revoked authority fails closed
- broken provenance blocks carriage/citation
- unresolved contradiction blocks affected use
- quarantine and seal override routing
- temporary authority expires
- control violations terminate the bounded session
- human veto immediately stops bounded control
- control failure cannot silently route around the refusal
- runtime evidence may influence routing without rewriting constitutional law

`HumanAuthoritySeal` is still a structural human-authorization object. The
constitutional attestation layer authenticates persisted ledger heads, not the
individual promotion/control seal action itself. Binding each human
authorization receipt directly to the Anchor identity remains a separate
hardening step.

---

## What v0.2 does not claim

PhiKernel v0.2 does **not**:

- replace the Linux kernel
- grant autonomous general control
- allow routing scores to manufacture authority
- let success self-promote a subsystem
- let failure rewrite constitutional law
- treat TIEKAT telemetry as established physical law
- expose the full constitutional orchestrator as the default shell routing path yet
- execute arbitrary host commands merely because the orchestrator licensed an action
- treat the initial runtime-adapter executor as permission for subprocess or arbitrary Python execution

The external executor boundary remains explicit.

---

## Testing

Run the full suite:

```bash
python -m pytest
```

The suite covers the original substrate plus constitutional invariants across warrants, transitions, provenance, contradiction, routing memory, mutability, clocks, coalitions, premises, relational routing, shadow comparison, witness promotion, bounded control, and the constitutional orchestrator.

CI currently targets Python 3.11 and 3.12.

---

## Phi Commons

PhiKernel is part of the **Phi Commons**.

Project-owned code and documentation are released under the MIT License unless otherwise noted. Third-party dependencies remain under their own licenses. See:

- `LICENSE`
- `PHI_COMMONS.md`
- `LICENSE_HISTORY.md`
- `THIRD_PARTY_NOTICES.md`

The pre-Commons proprietary state is preserved in repository history rather than rewritten.

---

## v0.2 milestone

v0.1 established:

```text
identity
continuity
pulse
telemetry
shell
first deterministic routing
```

v0.2 adds:

```text
governed transitions
evidence custody
contradiction state
routing memory
stratified mutability
computational clock skins
temporary coalitions
premise governance
relational routing
shadow comparison
behavioral witness
human-gated promotion
finite bounded control
constitutional orchestration
persistent constitutional state
restart-safe shell state resume
bounded constitutional action transactions
non-replaying crash reconciliation
Anchor-signed constitutional head attestations
```

The result is not an autonomous operating system and not a generic agent framework.

It is a local runtime foundation for **computational capability under explicit, inspectable, receipted authority**.

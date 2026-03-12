# PhiKernel v0.1.0

**PhiKernel** is a local-first, Linux-hosted runtime for anchored identity, sealed continuity, runtime pulse, coherence scoring, and safe shell-based orchestration.

It is designed as a **sovereign substrate** for trusted local operation: identity is rooted in cryptographic verification, memory is sealed into encrypted capsules, runtime health is maintained by a monotonic heartbeat, and system drift is observed through a TIEKAT-aligned coherence model centered on **C\* = φ/2**.

PhiKernel is **not** a replacement for Linux.  
It runs **on top of Linux** as a higher-order local runtime.

---

## What PhiKernel is

PhiKernel provides a structured local runtime with six core layers:

- **Anchor** — root identity and trust
- **Capsule** — encrypted continuity snapshots
- **Heart** — monotonic runtime scheduler and maintenance loop
- **Coherence** — field telemetry and drift scoring
- **Shell** — operable command-line interface
- **Router** — first deterministic coach/orchestration layer

The system is designed to move from disposable sessions toward persistent, continuity-aware operation.

---

## Design principles

### Linux is the host substrate
Linux remains responsible for hardware access, filesystems, drivers, process boundaries, and operating-system scheduling.

### PhiKernel is the continuity substrate
PhiKernel is responsible for identity anchoring, sealed state, runtime observation, and shell-level orchestration.

### Security is literal
PhiKernel uses standard cryptographic primitives for trust and storage. Symbolic models may inform structure and naming, but they do not replace cryptography.

### Continuity is first-class
A session is not the unit of truth. The anchored runtime and its sealed capsules are the continuity surface.

### TIEKAT is used as a scoring model
PhiKernel uses a TIEKAT-aligned attractor model centered on **C\* = φ/2** as a runtime scoring and drift-observation layer. This is used for telemetry and correction logic, not as a claim of closed physical law.

---

## Core braid

### 1. Anchor
The Anchor is the root of trust for the local runtime.

It provides:
- Ed25519 identity
- Argon2id-protected private-key unlock
- signed manifest verification
- stable sovereign identity metadata

### 2. Capsule
The Capsule layer provides encrypted continuity storage.

It provides:
- AES-GCM sealed snapshots
- per-capsule derived encryption keys
- manifest-linked continuity
- verified restore / rehydrate flow

### 3. Heart
The Heart is the runtime pulse.

It provides:
- monotonic scheduler
- recurring maintenance jobs
- anchor verification checks
- automatic checkpoint sealing
- runtime status persistence

### 4. Coherence
The Coherence layer gives the runtime a field model.

It provides:
- `C_current`
- `distance_to_C_star`
- `phi_flow`
- `lambda_node`
- `sigma_feedback`
- `fragmentation_score`
- action recommendations such as `observe`, `checkpoint`, `restore`, or `alert`

### 5. Shell
The Shell is the human-facing terminal surface.

It provides:
- status inspection
- field inspection
- anchor inspection
- capsule seal/list/restore
- context bundle generation through `think`

### 6. Router
The Router is the first orchestration layer on top of the shell.

It provides:
- deterministic coach routing
- safety-aware selection
- Titan / Flow / Sage responses
- routing that respects anchor trust, pulse state, and field stability

---

## Project status

**Version:** `0.1.0`  
**Status:** private alpha / substrate milestone

PhiKernel v0.1.0 is the first full core braid milestone:
- identity
- continuity
- pulse
- vision
- voice
- first safe orchestration

This version is intended as a **local, private, testable foundation** for future coach, wellness, and sovereign-runtime work.

---

## Repository layout

```text
phikernel/
├── pyproject.toml
├── README.md
├── PROPRIETARY_NOTICE.md
├── src/
│   └── phikernel/
│       ├── __init__.py
│       ├── anchor.py
│       ├── capsule.py
│       ├── heart.py
│       ├── coherence.py
│       ├── shell.py
│       └── router.py
└── tests/
    ├── test_anchor.py
    ├── test_capsule.py
    ├── test_heart.py
    ├── test_coherence.py
    ├── test_shell.py
    ├── test_router.py
    └── test_shell_route.py
```

---

## Requirements

* Python 3.11+
* Linux recommended for primary local runtime use

Dependencies:

* `cryptography`
* `argon2-cffi`

Dev dependency:

* `pytest`

---

## Installation

Clone the repository, then install in editable mode:

```bash
pip install -e .[dev]
```

You should then have access to the CLI:

```bash
phik --help
```

---

## Development Setup

For local development, use Python 3.11+ in a virtual environment:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .[dev]
```

Then run:

```bash
python -m pytest
phik --help
```

---

## First Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
phik init --passphrase "change-me" --sovereign-name "Tal-Aren-Vox" --user-label "Ori"
phik anchor show
phik status
phik ask "How should I begin?"
```

---

## Primary commands

### Runtime overview

```bash
phik status
```

Shows aggregate substrate state:

* anchor status
* heart status
* current field action
* capsule count

### View current field state

```bash
phik field
```

Shows the latest coherence frame, including:

* `C_current`
* `C_star`
* drift distance
* `phi_flow`
* `lambda_node`
* `sigma_feedback`
* recommended action

### Show anchor state

```bash
phik anchor show
```

Shows the signed sovereign identity surface.

### List capsules

```bash
phik capsule list
```

Lists continuity capsules currently available in the store.

### Seal a capsule

```bash
phik capsule seal \
  --passphrase "your-passphrase" \
  --json-text '{"thread":"working session","phase":9}' \
  --summary "Manual checkpoint" \
  --tag checkpoint
```

### Restore a capsule

```bash
phik capsule restore <capsule_id> --passphrase "your-passphrase"
```

### Build a local context bundle

```bash
phik think "How is the field?"
```

### Route a prompt through the coach layer

```bash
phik route "How should I begin?"
```

### Friendly alias for route

```bash
phik ask "How should I begin?"
```

---

## Routing model

PhiKernel v0.1.0 includes a deterministic first orchestration layer with three initial coach profiles:

### Titan

Default grounding coach.
Selected when:

* anchor trust is invalid
* field action is `alert`
* field action is `restore`
* pulse is offline
* continuity is missing

### Flow

Momentum / creation / movement coach.
Selected when:

* field is safe
* prompt language suggests forward motion or creative work

### Sage

Reflection / pattern / interpretation coach.
Selected when:

* field is safe
* prompt language suggests reflection, meaning, or pattern recognition

The router is intentionally deterministic in this version. It is not a fake multi-agent swarm and does not pretend to be a live LLM runtime.

---

## Testing

Run the full suite with:

```bash
python -m pytest
```

The tests cover:

* anchor trust and tamper detection
* capsule seal / verify / restore flows
* heartbeat scheduling and checkpoint behavior
* coherence scoring and threshold behavior
* shell commands and CLI contracts
* router safety overrides and routing decisions
* shell-native ask/route integration

---

## Security notes

PhiKernel uses modern cryptographic primitives for local trust and continuity:

* Ed25519 for signatures
* Argon2id for passphrase-based key derivation
* AES-GCM for encrypted local storage

The system is designed to be **local-first** and **private-first**.

This version does **not** attempt to:

* replace Linux kernel primitives
* invent custom cryptography
* claim TIEKAT as closed physical law
* provide networked orchestration by default

---

## Proprietary status

PhiKernel is currently a **private / proprietary** project.

No open-source license is granted in this repository unless explicitly added in the future. See `PROPRIETARY_NOTICE.md` for project ownership and usage restrictions.

---

## Roadmap direction

The v0.1.0 milestone establishes the substrate.

Future work may include:

* expanded coach registry
* richer shell workflows
* local API surface
* stronger packaging and deployment flows
* deeper wellness and continuity modules
* sovereign orchestration on top of the existing braid

---

## Summary

PhiKernel v0.1.0 is the first stable braid of:

* **identity**
* **memory**
* **pulse**
* **vision**
* **voice**
* **safe first orchestration**

It is the beginning of a local runtime that can remember, verify, observe, and respond without discarding continuity.

The foundation is now real.

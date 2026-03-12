# PhiKernel

PhiKernel is a **Linux-hosted local runtime substrate** for sovereign workflows. It is not a Linux replacement; it runs on top of your existing Linux environment and provides deterministic runtime primitives for identity, memory, pulse, field scoring, and shell orchestration.

## Core braid

- **Anchor** (`phikernel.anchor`) — identity trust root and manifest verification.
- **Capsule** (`phikernel.capsule`) — encrypted continuity memory and restore checkpoints.
- **Heart** (`phikernel.heart`) — runtime pulse, heartbeat, and state persistence.
- **Coherence** (`phikernel.coherence`) — field scoring and drift/action framing.
- **Shell** (`phikernel.shell`) — operator-facing CLI voice and command surface.
- **Router** (`phikernel.router`) — first deterministic orchestration layer for `ask`/`route` flows.

## TIEKAT alignment note

PhiKernel uses **C\* = phi/2** as a runtime scoring reference for coherence and drift decisions. This is used as an operational model for local runtime behavior and decision support, not as a claim of closed or final physics.

## Private / proprietary status

This repository is private and proprietary to **PHI369 Labs / Parallax**. See `PROPRIETARY_NOTICE.md` for usage restrictions and rights.

## Installation

```bash
pip install -e .[dev]
```

## CLI examples

```bash
phik status
phik field
phik anchor show
phik capsule list
phik ask "How should I begin?"
```

## Development checks

```bash
python -m pytest
python -m phikernel.shell --help
phik --help
```

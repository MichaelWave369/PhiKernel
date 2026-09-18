# Third-Party Notices

PhiKernel is MIT-licensed for project-owned code. Its dependencies remain independent works under their upstream licenses.

This file is informational and does not replace upstream license texts.

## Runtime dependencies

### cryptography

- PhiKernel requirement: `cryptography>=41.0.0`
- Purpose: Ed25519 signatures, AES-GCM and related standard cryptographic primitives
- Upstream license expression: `Apache-2.0 OR BSD-3-Clause`

### argon2-cffi

- PhiKernel requirement: `argon2-cffi>=23.1.0`
- Purpose: Argon2id password/passphrase hashing and key-derivation support
- Upstream license: MIT
- Its bindings/distributions may include upstream Argon2 code under separate permissive terms; redistribution must preserve any required notices supplied by the dependency.

## Development dependency

### pytest

- PhiKernel requirement: `pytest>=7.0.0`
- Purpose: test runner
- Upstream license: MIT

## Release rule

Before distributing vendored dependency source, bundled binary components, or copied upstream assets, maintainers must review the exact versions being shipped and preserve the notices required by those versions.

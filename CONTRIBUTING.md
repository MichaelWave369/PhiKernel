# Contributing to PhiKernel

Thank you for contributing to PhiKernel.

## License

Project-owned PhiKernel code and documentation are released under the MIT License unless otherwise noted.

By submitting a contribution for inclusion, you agree that your contribution may be distributed under MIT and represent that you have the right to submit it under those terms.

Do not submit third-party code or assets unless the intended use is permitted by their license and all required attribution/notice is included.

## Development

Use Python 3.11+.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .[dev]
python -m pytest
```

## Authority rule

Changes that add routing, tools, shell operations, or external effects must preserve the project rule that technical capability does not itself grant permission.

## Dependency changes

A pull request adding a dependency should identify:

- package/project name;
- version range;
- upstream source;
- license;
- whether PhiKernel redistributes it or merely depends on it.

Update `THIRD_PARTY_NOTICES.md` when appropriate.

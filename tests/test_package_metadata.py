from pathlib import Path
import tomllib

import phikernel


def test_package_version_matches_project_metadata() -> None:
    project_root = Path(__file__).resolve().parents[1]
    with (project_root / "pyproject.toml").open("rb") as fh:
        metadata = tomllib.load(fh)

    assert metadata["project"]["version"] == phikernel.__version__


def test_v020_package_identity() -> None:
    assert phikernel.__version__ == "0.2.0"

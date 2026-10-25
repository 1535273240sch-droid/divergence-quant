from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_m00_project_boundaries_exist() -> None:
    expected = [
        ROOT / "pyproject.toml",
        ROOT / "config" / "config.yaml",
        ROOT / "src" / "dquant" / "contracts" / "models.py",
        ROOT / "协作" / "00_总控" / "接口契约.md",
    ]

    assert all(path.is_file() for path in expected)

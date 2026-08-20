"""Recursively import every module under src/ to catch broken imports."""
import importlib
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"


def _module_names():
    modules = []
    for py in sorted(SRC_DIR.rglob("*.py")):
        rel = py.relative_to(SRC_DIR.parent)
        parts = list(rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if not parts:
            continue
        modules.append(".".join(parts))
    return modules


def test_src_package_importable():
    assert SRC_DIR.is_dir(), f"src directory not found: {SRC_DIR}"
    importlib.import_module("src")


def test_all_modules_import():
    modules = _module_names()
    assert len(modules) > 100, f"expected 100+ modules, found {len(modules)}"

    failures = []
    for name in modules:
        try:
            importlib.import_module(name)
        except Exception as e:  # noqa: BLE001 - collect all failures
            failures.append(f"{name}: {type(e).__name__}: {e}")

    assert not failures, "Import failures:\n" + "\n".join(failures)

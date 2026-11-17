#!/usr/bin/env python3
"""Assemble the self-contained dsh-notifyhub npm package.

The source checkout keeps the Python launcher at the repo root, while the
published package needs it under ``scripts/`` next to ``lib/``. This script also
copies ``locale/`` and ``icon.svg`` so Plugin Manager and the market can render
the bundle.
"""
import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", "node_modules", ".git")


def _python_layout():
    if (ROOT / "notifyhub.py").is_file():
        return ROOT / "notifyhub.py", ROOT / "notifyhub"
    if (ROOT / "scripts" / "notifyhub.py").is_file():
        return ROOT / "scripts" / "notifyhub.py", ROOT / "scripts" / "notifyhub"
    raise SystemExit("notifyhub launcher not found")


def _copy(source, destination):
    if source.is_dir():
        shutil.copytree(source, destination, ignore=IGNORE)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def build(out):
    launcher, package = _python_layout()
    plugin = ROOT / "integrations" / "dsh"
    if not (plugin / "package.json").is_file():
        raise SystemExit("integrations/dsh not found")

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    for name in ("lib", "locale", "docs"):
        source = plugin / name
        if source.exists():
            _copy(source, out / name)
    for name in ("cordis.patch.yml", "README.md", "icon.svg"):
        source = plugin / name
        if source.exists():
            _copy(source, out / name)

    _copy(launcher, out / "scripts" / "notifyhub.py")
    _copy(package, out / "scripts" / "notifyhub")

    license_path = ROOT / "LICENSE"
    if license_path.is_file():
        _copy(license_path, out / "LICENSE")

    manifest = json.loads((plugin / "package.json").read_text(encoding="utf-8"))
    manifest["files"] = [
        "lib",
        "cordis.patch.yml",
        "README.md",
        "package.json",
        "locale/*.json",
        "icon.svg",
        "scripts/notifyhub.py",
        "scripts/notifyhub",
        "LICENSE",
    ]
    if (out / "docs").is_dir():
        manifest["files"].append("docs")
    exports = manifest.get("exports") or {}
    exports.setdefault(".", "./lib/index.js")
    exports.setdefault("./locale/*.json", "./locale/*.json")
    exports.setdefault("./icon", "./icon.svg")
    exports.setdefault("./package.json", "./package.json")
    manifest["exports"] = exports
    manifest["license"] = "MIT"
    manifest["publishConfig"] = {"access": "public"}
    (out / "package.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"built: {out}")
    print(f"launcher: {launcher}")
    print(f"python package: {package}")
    return out


def main():
    parser = argparse.ArgumentParser(description="Assemble the dsh-notifyhub npm package")
    parser.add_argument("--out", default=str(ROOT / "dist" / "npm" / "dsh-notifyhub"))
    args = parser.parse_args()
    build(Path(args.out).expanduser())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
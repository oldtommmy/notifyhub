"""Build the standalone DSH Market tarball for a NotifyHub release."""
import argparse
import json
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from notifyhub import __version__
from notifyhub.runtime import BRAND_LOGOS

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "integrations" / "dsh"
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "node_modules", ".*")


def stage_package(destination):
    manifest = json.loads((PLUGIN / "package.json").read_text(encoding="utf-8"))
    if manifest["version"] != __version__:
        raise ValueError(f"DSH plugin version must match NotifyHub {__version__}")

    for pattern in manifest["files"]:
        for path in PLUGIN.glob(pattern):
            target = destination / path.relative_to(PLUGIN)
            if path.is_dir():
                shutil.copytree(path, target, ignore=IGNORE)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)

    shutil.copytree(ROOT / "scripts" / "notifyhub", destination / "scripts" / "notifyhub", ignore=IGNORE)
    shutil.copy2(ROOT / "scripts" / "notifyhub.py", destination / "scripts" / "notifyhub.py")
    for name in BRAND_LOGOS:
        target = destination / "assets" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "assets" / name, target)
    for name in ("LICENSE", "bot-config.json"):
        shutil.copy2(ROOT / name, destination / name)

    manifest["files"].extend(["assets", "bot-config.json", "scripts"])
    (destination / "package.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest["name"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("dist"))
    args = parser.parse_args()
    npm = shutil.which("npm")
    if npm is None:
        parser.error("Node.js and npm are required to package the DSH plugin.")

    args.output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="notifyhub-dsh-") as directory:
        staging = Path(directory)
        package = staging / "package"
        package.mkdir()
        name = stage_package(package)
        subprocess.run([npm, "pack", "--ignore-scripts", "--quiet", "--pack-destination", str(staging)],
                       cwd=package, check=True)
        target = args.output / f"{name}.tgz"
        shutil.move(staging / f"{name}-{__version__}.tgz", target)
    print(f"Built {target} for NotifyHub v{__version__}.")


if __name__ == "__main__":
    main()

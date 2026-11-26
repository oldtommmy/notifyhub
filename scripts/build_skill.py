"""Build the single Skill distribution and its SHA-256 checksum."""
import argparse
from hashlib import sha256
from pathlib import Path

from notifyhub import __version__, updates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("dist"))
    parser.add_argument("--tag", default="v" + __version__)
    args = parser.parse_args()
    if args.tag != "v" + __version__:
        parser.error(f"expected tag v{__version__}")
    args.output.mkdir(parents=True, exist_ok=True)
    bundle = updates.build_bundle(updates.skill_root())
    (args.output / updates.BUNDLE).write_bytes(bundle)
    (args.output / updates.CHECKSUM).write_text(
        f"{sha256(bundle).hexdigest()}  {updates.BUNDLE}\n", encoding="ascii"
    )
    print(f"Built NotifyHub Skill {args.tag} in {args.output}.")


if __name__ == "__main__":
    main()

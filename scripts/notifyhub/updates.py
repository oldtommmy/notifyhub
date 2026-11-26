"""Install and update the standalone NotifyHub Skill."""
import ast
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import time
from urllib.request import Request, urlopen
from zipfile import ZIP_DEFLATED, ZipFile

from . import __version__
from .state import state_lock

REPO = "oldtommmy/notifyhub"
RELEASES = f"https://github.com/{REPO}/releases"
BUNDLE = "notifyhub-skill.zip"
CHECKSUM = BUNDLE + ".sha256"
VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)\Z")
DIRECTORIES = {"assets", "integrations", "references", "scripts", "tests"}
FILES = {"SKILL.md", "README.md", "LICENSE", "CHANGELOG.md", "bot-config.json"}
REQUIRED = {"SKILL.md", "bot-config.json", "scripts/notifyhub.py", "scripts/notifyhub/__init__.py",
            "scripts/notifyhub/gui_assets/index.html", "integrations/dsh/package.json"}
LIMIT_BYTES = 32 * 1024 * 1024
CHECK_INTERVAL = 12 * 60 * 60


def skill_root():
    return Path(__file__).resolve().parents[2]


def release_version(tag):
    match = VERSION.fullmatch(str(tag))
    return tuple(int(part) for part in match.groups()) if match else ()


def bundle_files(root):
    for name in sorted(FILES):
        if (root / name).is_file():
            yield root / name
    for name in sorted(DIRECTORIES):
        for path in sorted((root / name).rglob("*")):
            relative = path.relative_to(root)
            if (path.is_file() and not path.is_symlink()
                    and not any(part.startswith(".") or part in ("__pycache__", "node_modules")
                                for part in relative.parts)):
                yield path


def build_bundle(root):
    archive = BytesIO()
    with ZipFile(archive, "w", ZIP_DEFLATED) as package:
        for path in bundle_files(root):
            package.write(path, "notifyhub/" + path.relative_to(root).as_posix())
    return archive.getvalue()


def _fetch(url, limit):
    request = Request(url, headers={"User-Agent": "NotifyHub/" + __version__})
    with urlopen(request, timeout=8) as response:
        result = response.read(limit + 1)
    if len(result) > limit:
        raise ValueError("下载内容超出了预期大小。")
    return result


def _latest_tag():
    url = f"https://api.github.com/repos/{REPO}/releases?per_page=20"
    releases = json.loads(_fetch(url, 1024 * 1024))
    if not isinstance(releases, list):
        raise ValueError("GitHub 返回了无法识别的发布列表。")
    tags = []
    for item in releases:
        tag = item.get("tag_name", "")
        names = {asset.get("name") for asset in item.get("assets", [])}
        if tag.startswith("v") and release_version(tag) and not item.get("draft") and not item.get("prerelease"):
            if {BUNDLE, CHECKSUM} <= names:
                tags.append(tag)
    return max(tags, key=release_version) if tags else ""


def check(refresh=False, root=None, cache_path=None):
    root = Path(root) if root is not None else skill_root()
    path = Path(cache_path) if cache_path else Path.home() / ".local/state/notifyhub/release-check.json"
    with state_lock(path):
        try:
            cache = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
        if not isinstance(cache, dict):
            cache = {}
        tag = cache.get("tag", "")
        error = bool(cache.get("error"))
        now = time.time()
        interval = min(300, CHECK_INTERVAL) if error else CHECK_INTERVAL
        checked_at = cache.get("checked_at")
        if refresh or not isinstance(checked_at, (int, float)) or now - checked_at > interval:
            try:
                tag, error = _latest_tag(), False
            except (OSError, ValueError):
                error = True
            cache = {"tag": tag, "error": error, "checked_at": now}
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(cache), encoding="utf-8")
            os.replace(temp, path)
    valid = release_version(tag)
    can_apply = _installed_skill(root)
    return {"current": __version__, "latest": tag if valid else "",
            "available": bool(valid and valid > release_version(__version__)),
            "release_url": RELEASES + "/tag/" + tag if valid else RELEASES,
            "can_apply": can_apply, "checkout": (root / ".git").exists(), "offline": error}


def _installed_skill(root):
    return ((root / "SKILL.md").is_file() and (root / "scripts/notifyhub.py").is_file()
            and not (root / ".git").exists())


def _content_version(root):
    version_file = root / "scripts/notifyhub/__init__.py"
    module = ast.parse(version_file.read_text(encoding="utf-8"))
    return next((node.value.value for node in module.body if isinstance(node, ast.Assign)
                 and isinstance(node.value, ast.Constant)
                 and any(isinstance(target, ast.Name) and target.id == "__version__"
                         for target in node.targets)), "")


def _unpack(archive, destination, version):
    with ZipFile(BytesIO(archive)) as package:
        seen = set()
        total = 0
        for entry in package.infolist():
            if entry.is_dir():
                continue
            parts = entry.filename.split("/")
            total += entry.file_size
            if (len(parts) < 2 or parts[0] != "notifyhub" or any(part in ("", ".", "..") for part in parts)
                    or "\\" in entry.filename or ":" in entry.filename or total > LIMIT_BYTES
                    or stat.S_ISLNK(entry.external_attr >> 16)):
                raise ValueError("Skill 压缩包包含无效路径或文件。")
            relative = PurePosixPath(*parts[1:])
            if ((len(parts) == 2 and parts[1] not in FILES)
                    or (len(parts) > 2 and parts[1] not in DIRECTORIES)
                    or relative.as_posix().casefold() in seen):
                raise ValueError("Skill 压缩包包含重复或未知的文件。")
            seen.add(relative.as_posix().casefold())
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(entry) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
        if not {path.casefold() for path in REQUIRED} <= seen:
            raise ValueError("Skill 压缩包缺少运行文件。")
    if not release_version(version) or release_version(_content_version(destination)) != release_version(version):
        raise ValueError("发布标签和 Skill 内容版本不一致。")


def _replace(archive, root, version, *, newer=False):
    root = Path(root).expanduser().resolve()
    with state_lock(root.parent / ".notifyhub-install"):
        if root.exists() and not _installed_skill(root):
            raise ValueError(f"目标目录包含其他文件或 Git 仓库，请选择 Skill 安装目录：{root}")
        if newer and release_version(version) <= release_version(_content_version(root)):
            raise ValueError("此 Skill 已经更新，请重新打开 GUI。")
        with tempfile.TemporaryDirectory(prefix=".notifyhub-stage-", dir=root.parent) as temporary:
            stage = Path(temporary) / "notifyhub"
            stage.mkdir()
            _unpack(archive, stage, version)
            backup = None
            if root.exists():
                # Preserve resources placed beside the distribution, such as a local venv.
                for path in root.iterdir():
                    if path.name in FILES | DIRECTORIES:
                        continue
                    if path.is_dir() and not path.is_symlink():
                        shutil.copytree(path, stage / path.name, symlinks=True)
                    else:
                        shutil.copy2(path, stage / path.name, follow_symlinks=False)
                backup_home = root.parent.parent / ".notifyhub-backups"
                backup_home.mkdir(mode=0o700, parents=True, exist_ok=True)
                backup = backup_home / (root.name + "-" + str(time.time_ns()))
                os.replace(root, backup)
            try:
                os.replace(stage, root)
            except OSError:
                if backup is not None:
                    os.replace(backup, root)
                raise
    return {"path": str(root), "backup": str(backup or ""), "version": version.lstrip("v")}


def install_local(destination, source=None):
    source = Path(source or skill_root()).resolve()
    destination = Path(destination).expanduser().resolve()
    if source == destination:
        return {"path": str(source), "backup": "", "version": __version__}
    return _replace(build_bundle(source), destination, __version__)


def apply(tag, root=None):
    root = Path(root or skill_root())
    if not _installed_skill(root):
        if (root / ".git").exists():
            raise ValueError("这是源码检出目录。请通过 Git 更新。")
        raise ValueError("请从 NotifyHub Skill 安装目录执行更新。")
    if not tag.startswith("v") or release_version(tag) <= release_version(__version__):
        raise ValueError("请选择比当前版本更新的正式版本。")
    url = f"{RELEASES}/download/{tag}/"
    checksum = _fetch(url + CHECKSUM, 1024).decode("ascii").split()
    if len(checksum) != 2 or checksum[1] != BUNDLE or not re.fullmatch(r"[0-9a-f]{64}", checksum[0]):
        raise ValueError("发布包校验文件无效。")
    archive = _fetch(url + BUNDLE, LIMIT_BYTES)
    if sha256(archive).hexdigest() != checksum[0]:
        raise ValueError("发布包校验失败，请重新检查更新。")
    return _replace(archive, root, tag, newer=True)

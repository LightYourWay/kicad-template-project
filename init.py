#!/usr/bin/env python3
"""Initialise a project created from kicad-template-project.

Run once right after "Use this template" + clone. The script

  1. renames template.kicad_* to <name>.kicad_* and patches every reference,
  2. fills the title block with "#<pbs> <title>" and today's date,
  3. makes sure LightYourWay/kicad-custom-libs is cloned and reachable through
     the KiCad path variable LRV_CUSTOM_LIBS (clones + sets it if not),
  4. stages the result in git and deletes itself.

Nothing is committed -- review with `git status` and `git diff --cached`.
Requires Python 3.8+ and git. Works on macOS, Linux and Windows.
"""

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, NoReturn, Optional

REPO_ROOT = Path(__file__).resolve().parent
TEMPLATE_STEM = "template"
PROJECT_EXTS = ("kicad_pro", "kicad_sch", "kicad_pcb", "kicad_prl", "kicad_dru")
NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

LIBS_VAR = "LRV_CUSTOM_LIBS"
LIBS_URLS = {
    "https": "https://github.com/LightYourWay/kicad-custom-libs.git",
    "ssh": "git@github.com:LightYourWay/kicad-custom-libs.git",
}
# Files that must exist in a valid clone (mirrors the *-lib-table entries).
LIBS_MARKERS = (Path("symbol") / "LRV.kicad_sym", Path("footprints") / "LRV")
PARTDB_LIB = Path("symbol") / "Part-DB.kicad_httplib"
PARTDB_INIT = Path("scripts") / "init-partdb-lib.py"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def fail(message: str) -> NoReturn:
    sys.stdout.flush()
    print(f"❌ {message}", file=sys.stderr)
    raise SystemExit(1)


def interactive() -> bool:
    return sys.stdin.isatty()


def ask(prompt: str, default: str = "") -> str:
    """Prompt with an optional default; non-interactive runs take the default."""
    if not interactive():
        return default
    suffix = f" [{default}]" if default else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    return answer or default


def confirm(prompt: str, default: bool = True, assume_yes: bool = False) -> bool:
    if assume_yes or not interactive():
        return default
    hint = "[Y/n]" if default else "[y/N]"
    answer = input(f"{prompt} {hint} ").strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes", "j", "ja")


def git(*args: str, cwd: Path = REPO_ROOT, capture: bool = True) -> Optional[subprocess.CompletedProcess]:
    """Run git; None if git is not installed."""
    try:
        return subprocess.run(
            ["git", "-C", str(cwd), *args],
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None


def in_git_repo() -> bool:
    result = git("rev-parse", "--is-inside-work-tree")
    return bool(result and result.returncode == 0)


def git_tracked(path: Path) -> bool:
    result = git("ls-files", "--error-unmatch", "--", str(path))
    return bool(result and result.returncode == 0)


def default_project_name() -> str:
    result = git("remote", "get-url", "origin")
    if result and result.returncode == 0 and result.stdout.strip():
        url = result.stdout.strip().rstrip("/")
        name = url.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
        if name.endswith(".git"):
            name = name[:-4]
        if name:
            return name
    return REPO_ROOT.name


def kicad_escape(text: str) -> str:
    """Escape a string for use inside a KiCad s-expression string literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


# --------------------------------------------------------------------------- #
# project files
# --------------------------------------------------------------------------- #
def rename_project_files(name: str, use_git: bool) -> List[Path]:
    renamed = []
    for ext in PROJECT_EXTS:
        src = REPO_ROOT / f"{TEMPLATE_STEM}.{ext}"
        if not src.exists():
            continue
        dst = REPO_ROOT / f"{name}.{ext}"
        if dst.exists():
            fail(f"{dst.name} already exists")
        if use_git and git_tracked(src):
            result = git("mv", "--", src.name, dst.name)
            if not result or result.returncode != 0:
                fail(f"git mv failed for {src.name}: {(result.stderr if result else '').strip()}")
        else:
            src.rename(dst)
        renamed.append(dst)
    return renamed


TITLE_RE = re.compile(r'^(?P<indent>[ \t]*)\(title "template"\)[ \t]*$', re.MULTILINE)
FILE_REF_RE = re.compile(r"(?<![A-Za-z0-9_])template\.kicad_")
BARE_NAME_RE = re.compile(r'"template"')


def patch_project_file(path: Path, name: str, title: str, date: str) -> None:
    # newline="" keeps whatever line endings the file has.
    with open(path, encoding="utf-8", newline="") as handle:
        text = handle.read()

    def title_block(match: "re.Match") -> str:
        indent = match.group("indent")
        return f'{indent}(title "{kicad_escape(title)}")\n{indent}(date "{date}")'

    text = TITLE_RE.sub(title_block, text)
    text = FILE_REF_RE.sub(f"{name}.kicad_", text)
    text = BARE_NAME_RE.sub(f'"{name}"', text)

    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


README_HEADING_RE = re.compile(r"^# kicad-template-project[ \t]*$", re.MULTILINE)
# The "Automatic setup" section only makes sense before this script has run.
README_INIT_SECTION_RE = re.compile(r"\n## Automatic setup\b.*?(?=\n## |\Z)", re.DOTALL)


def patch_readme(name: str) -> Optional[Path]:
    readme = REPO_ROOT / "README.md"
    if not readme.is_file():
        return None
    text = readme.read_text(encoding="utf-8")
    text = README_HEADING_RE.sub(f"# {name}", text, count=1)
    text = README_INIT_SECTION_RE.sub("", text)
    readme.write_text(text, encoding="utf-8")
    return readme


# --------------------------------------------------------------------------- #
# kicad-custom-libs / LRV_CUSTOM_LIBS
# --------------------------------------------------------------------------- #
def kicad_settings_files() -> List[Path]:
    """Every kicad_common.json on this machine, newest KiCad version first.

    KiCad keeps one settings directory per major.minor version below a
    platform-specific base directory (or $KICAD_CONFIG_HOME).
    """
    home = Path.home()
    bases: List[Path] = []
    override = os.environ.get("KICAD_CONFIG_HOME")
    if override:
        bases.append(Path(override))
    if sys.platform == "darwin":
        bases.append(home / "Library" / "Preferences" / "kicad")
    elif os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            bases.append(Path(appdata) / "kicad")
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME") or str(home / ".config")
        bases.append(Path(xdg) / "kicad")
        bases.append(home / ".var" / "app" / "org.kicad.KiCad" / "config" / "kicad")

    found = []
    for base in bases:
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if child.is_dir() and re.fullmatch(r"\d+\.\d+", child.name):
                common = child / "kicad_common.json"
                if common.is_file():
                    found.append(common)

    def version_key(path: Path):
        return tuple(int(part) for part in path.parent.name.split("."))

    return sorted(found, key=version_key, reverse=True)


def libs_valid(path: Optional[str]) -> bool:
    if not path:
        return False
    root = Path(path).expanduser()
    return root.is_dir() and all((root / marker).exists() for marker in LIBS_MARKERS)


def read_libs_var(settings: Path) -> Optional[str]:
    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return (data.get("environment") or {}).get("vars", {}).get(LIBS_VAR)


def write_libs_var(settings: Path, value: str) -> None:
    data = json.loads(settings.read_text(encoding="utf-8"))
    env = data.setdefault("environment", {})
    variables = env.setdefault("vars", {})
    variables[LIBS_VAR] = value
    # KiCad itself writes sorted keys with a two-space indent.
    settings.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def clone_libs(target: Path, transport: str, assume_yes: bool) -> None:
    if transport not in LIBS_URLS:
        transport = ask("🔐 clone via https or ssh", "https").lower()
        if transport not in LIBS_URLS:
            fail("transport must be https or ssh")
    url = LIBS_URLS[transport]
    print(f"⬇️  cloning {url}\n   → {target}")
    if not confirm("   continue?", True, assume_yes):
        fail("aborted")
    target.parent.mkdir(parents=True, exist_ok=True)
    result = git("clone", url, str(target), cwd=Path.cwd(), capture=False)
    if result is None:
        fail("git is not installed")
    if result.returncode != 0:
        fail("git clone failed")


def ensure_custom_libs(args: argparse.Namespace) -> None:
    print("\n📚 kicad-custom-libs")

    # 1) An OS-level variable wins over KiCad's own setting.
    env_value = os.environ.get(LIBS_VAR)
    if env_value:
        if libs_valid(env_value):
            print(f"✅ ${LIBS_VAR} is set in the environment: {env_value}")
            return
        print(f"⚠️  ${LIBS_VAR} is set in the environment but does not look like the "
              f"library: {env_value}")

    # 2) KiCad's Configure Paths (kicad_common.json), one file per KiCad version.
    settings = kicad_settings_files()
    status: Dict[Path, Optional[str]] = {s: read_libs_var(s) for s in settings}
    for common, value in status.items():
        version = common.parent.name
        if libs_valid(value):
            print(f"✅ KiCad {version}: {LIBS_VAR} = {value}")
        elif value:
            print(f"⚠️  KiCad {version}: {LIBS_VAR} = {value} (path missing or not the library)")
        else:
            print(f"❌ KiCad {version}: {LIBS_VAR} not set")

    needs_fix = [s for s, v in status.items() if not libs_valid(v)]
    if settings and not needs_fix:
        maybe_offer_partdb(next(iter(status.values())), args)
        return
    if not settings:
        print("⚠️  no KiCad settings found (kicad_common.json) – has KiCad been started once?")

    # 3) Find or create a clone.
    known = next((v for v in [env_value, *status.values()] if libs_valid(v)), None)
    default_path = known or args.libs_path or str(REPO_ROOT.parent / "kicad-custom-libs")
    if args.libs_path:
        chosen = args.libs_path
    else:
        chosen = ask("📁 path to kicad-custom-libs (existing clone or where to clone it)", default_path)
    libs_dir = Path(chosen).expanduser().resolve()

    if libs_valid(str(libs_dir)):
        print(f"✅ using existing clone at {libs_dir}")
    elif libs_dir.exists() and any(libs_dir.iterdir()):
        fail(f"{libs_dir} exists but is not kicad-custom-libs")
    else:
        clone_libs(libs_dir, args.clone_with, args.yes)
        if not libs_valid(str(libs_dir)):
            fail(f"clone at {libs_dir} does not contain the expected files")

    # 4) Persist into every KiCad version that lacks it.
    if needs_fix:
        print(f"\n✏️  setting {LIBS_VAR} in: " + ", ".join(f"KiCad {s.parent.name}" for s in needs_fix))
        print("   ⚠️  KiCad rewrites its settings on exit – make sure it is closed now.")
        if not confirm("   KiCad is closed, write the setting?", True, args.yes):
            print(f"   ⏭️  skipped; add {LIBS_VAR} = {libs_dir} under Preferences → Configure Paths yourself")
        else:
            for common in needs_fix:
                write_libs_var(common, str(libs_dir))
                print(f"   ✅ KiCad {common.parent.name}: {LIBS_VAR} = {libs_dir}")
    else:
        print(f"\n💡 add {LIBS_VAR} = {libs_dir} in KiCad under Preferences → Configure Paths "
              f"(or export it as an environment variable)")

    maybe_offer_partdb(str(libs_dir), args)


def maybe_offer_partdb(libs_path: Optional[str], args: argparse.Namespace) -> None:
    """The Part-DB HTTP library carries a personal token and is generated locally."""
    if not libs_path:
        return
    root = Path(libs_path).expanduser()
    if (root / PARTDB_LIB).exists() or not (root / PARTDB_INIT).exists():
        return
    print(f"\n📦 {PARTDB_LIB} is missing (holds your Part-DB API token, git-ignored).")
    if args.skip_partdb or not interactive():
        print(f"   💡 generate it with: python3 {root / PARTDB_INIT}")
        return
    if confirm("   run scripts/init-partdb-lib.py now?", True, False):
        subprocess.run([sys.executable, str(root / PARTDB_INIT)], check=False)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Initialise this repository from the KiCad template: rename the "
        "project files, fill the title block, ensure kicad-custom-libs is set up, "
        "then delete this script. Every prompt can be pre-answered with a flag.",
    )
    parser.add_argument("--name", help="project / repository name (default: from git remote 'origin')")
    parser.add_argument("--pbs", help="PBS number for the title block, any format (default: prompted, may be empty)")
    parser.add_argument("--title", help="board title (default: the project name)")
    parser.add_argument("--date", help="title block date, YYYY-MM-DD (default: today)")
    parser.add_argument("--libs-path", metavar="DIR", help="existing kicad-custom-libs clone, or where to clone it")
    parser.add_argument("--clone-with", choices=sorted(LIBS_URLS), help="transport for cloning kicad-custom-libs")
    parser.add_argument("--skip-libs", action="store_true", help="do not check/set up kicad-custom-libs")
    parser.add_argument("--skip-partdb", action="store_true", help="do not offer to run init-partdb-lib.py")
    parser.add_argument("--keep", action="store_true", help="keep this script instead of deleting it")
    parser.add_argument("-y", "--yes", action="store_true", help="assume yes for confirmations")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not (REPO_ROOT / f"{TEMPLATE_STEM}.kicad_pro").is_file():
        fail(f"{TEMPLATE_STEM}.kicad_pro not found – already initialised?")

    use_git = in_git_repo()

    # --- gather ---------------------------------------------------------- #
    name = args.name or ask("📛 project / repository name", default_project_name())
    if not NAME_RE.match(name):
        fail("name may only contain letters, digits, '.', '_' and '-'")
    pbs = args.pbs if args.pbs is not None else ask("🔢 PBS number (leave empty for none)")
    title = args.title or ask("🏷️  board title", name)
    date = args.date or _dt.date.today().isoformat()
    full_title = f"#{pbs} {title}" if pbs else title

    print(f"\n   files : {TEMPLATE_STEM}.kicad_* → {name}.kicad_*")
    print(f"   title : {full_title}")
    print(f"   date  : {date}")
    if not confirm("\n▶️  continue?", True, args.yes):
        fail("aborted")

    # --- rename + patch -------------------------------------------------- #
    renamed = rename_project_files(name, use_git)
    for path in renamed:
        patch_project_file(path, name, full_title, date)
        print(f"📝 {path.name}")
    readme = patch_readme(name)
    if readme:
        print(f"📝 {readme.name}")

    # --- libraries ------------------------------------------------------- #
    if args.skip_libs:
        print("\n⏭️  skipping kicad-custom-libs check")
    else:
        ensure_custom_libs(args)

    # --- stage + self-destruct ------------------------------------------- #
    me = Path(__file__).resolve()
    if use_git:
        git("add", "-A", "--", *[p.name for p in renamed], *([readme.name] if readme else []))
        if not args.keep and git_tracked(me):
            git("rm", "-q", "--cached", "--", me.name)
    if args.keep:
        print(f"\n🧷 keeping {me.name} (--keep)")
    else:
        me.unlink()
        print(f"\n🗑️  removed {me.name}")

    print("\n✅ done. Review with:  git status && git diff --cached")
    print(f"   then commit, e.g.:  git commit -m 'feat: :truck: initialise project {name}'")


if __name__ == "__main__":
    main()

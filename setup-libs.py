#!/usr/bin/env python3
"""Make LightYourWay/kicad-custom-libs available to KiCad on this machine.

The project references the library through the KiCad path variable
LRV_CUSTOM_LIBS, which is a per-machine setting. Run this once on every
machine that opens the project (init.py runs it for you on the machine the
project was created on). It

  1. checks whether LRV_CUSTOM_LIBS points to a valid clone -- as an OS
     environment variable or in KiCad's Configure Paths (kicad_common.json of
     every installed KiCad version),
  2. if not: reuses an existing clone or clones the library (https/ssh) and
     writes the variable into KiCad's settings (close KiCad first),
  3. offers to generate the personal Part-DB HTTP library file if missing.

Requires Python 3.8+ and git. Works on macOS, Linux and Windows.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, NoReturn, Optional

REPO_ROOT = Path(__file__).resolve().parent

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


# --------------------------------------------------------------------------- #
# KiCad settings
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
        # KiCad uses this directory *instead of* the platform default.
        bases.append(Path(override))
    elif sys.platform == "darwin":
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
    # KiCad writes "vars": null when no user variables are defined.
    variables = (data.get("environment") or {}).get("vars") or {}
    return variables.get(LIBS_VAR)


def write_libs_var(settings: Path, value: str) -> None:
    data = json.loads(settings.read_text(encoding="utf-8"))
    env = data.get("environment")
    if not isinstance(env, dict):
        env = data["environment"] = {}
    variables = env.get("vars")
    if not isinstance(variables, dict):
        variables = env["vars"] = {}
    variables[LIBS_VAR] = value
    # KiCad itself writes sorted keys with a two-space indent.
    settings.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# library clone
# --------------------------------------------------------------------------- #
def clone_libs(target: Path, transport: Optional[str], assume_yes: bool) -> None:
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


def maybe_offer_partdb(libs_path: Optional[str], skip: bool) -> None:
    """The Part-DB HTTP library carries a personal token and is generated locally."""
    if not libs_path:
        return
    root = Path(libs_path).expanduser()
    if (root / PARTDB_LIB).exists() or not (root / PARTDB_INIT).exists():
        return
    print(f"\n📦 {PARTDB_LIB} is missing (holds your Part-DB API token, git-ignored).")
    if skip or not interactive():
        print(f"   💡 generate it with: python3 {root / PARTDB_INIT}")
        return
    if confirm("   run scripts/init-partdb-lib.py now?", True, False):
        subprocess.run([sys.executable, str(root / PARTDB_INIT)], check=False)


def ensure_custom_libs(libs_path: Optional[str], clone_with: Optional[str], skip_partdb: bool, assume_yes: bool) -> None:
    print("📚 kicad-custom-libs")

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
        maybe_offer_partdb(next(iter(status.values())), skip_partdb)
        return
    if not settings:
        print("⚠️  no KiCad settings found (kicad_common.json) – has KiCad been started once?")

    # 3) Find or create a clone.
    known = next((v for v in [env_value, *status.values()] if libs_valid(v)), None)
    default_path = known or libs_path or str(REPO_ROOT.parent / "kicad-custom-libs")
    chosen = libs_path or ask("📁 path to kicad-custom-libs (existing clone or where to clone it)", default_path)
    libs_dir = Path(chosen).expanduser().resolve()

    if libs_valid(str(libs_dir)):
        print(f"✅ using existing clone at {libs_dir}")
    elif libs_dir.exists() and any(libs_dir.iterdir()):
        fail(f"{libs_dir} exists but is not kicad-custom-libs")
    else:
        clone_libs(libs_dir, clone_with, assume_yes)
        if not libs_valid(str(libs_dir)):
            fail(f"clone at {libs_dir} does not contain the expected files")

    # 4) Persist into every KiCad version that lacks it.
    if needs_fix:
        print(f"\n✏️  setting {LIBS_VAR} in: " + ", ".join(f"KiCad {s.parent.name}" for s in needs_fix))
        print("   ⚠️  KiCad rewrites its settings on exit – make sure it is closed now.")
        if not confirm("   KiCad is closed, write the setting?", True, assume_yes):
            print(f"   ⏭️  skipped; add {LIBS_VAR} = {libs_dir} under Preferences → Configure Paths yourself")
        else:
            for common in needs_fix:
                write_libs_var(common, str(libs_dir))
                print(f"   ✅ KiCad {common.parent.name}: {LIBS_VAR} = {libs_dir}")
    else:
        print(f"\n💡 add {LIBS_VAR} = {libs_dir} in KiCad under Preferences → Configure Paths "
              f"(or export it as an environment variable)")

    maybe_offer_partdb(str(libs_dir), skip_partdb)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check that kicad-custom-libs is cloned and reachable through the KiCad "
        "path variable LRV_CUSTOM_LIBS; clone it and set the variable if not. Run once per machine.",
    )
    parser.add_argument("--libs-path", metavar="DIR", help="existing kicad-custom-libs clone, or where to clone it")
    parser.add_argument("--clone-with", choices=sorted(LIBS_URLS), help="transport for cloning kicad-custom-libs")
    parser.add_argument("--skip-partdb", action="store_true", help="do not offer to run init-partdb-lib.py")
    parser.add_argument("-y", "--yes", action="store_true", help="assume yes for confirmations")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ensure_custom_libs(args.libs_path, args.clone_with, args.skip_partdb, args.yes)


if __name__ == "__main__":
    main()

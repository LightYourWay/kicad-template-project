#!/usr/bin/env python3
"""Initialise a project created from kicad-template-project.

Run once right after "Use this template" + clone. The script

  1. renames template.kicad_* to <name>.kicad_* and patches every reference,
  2. fills the title block with "#<pbs> <title>" and today's date,
  3. runs setup-libs.py so kicad-custom-libs is available on this machine,
  4. stages the result, offers to fold it into the single commit GitHub
     created from the template ("feat: :tada: initialise project <name>"),
     and deletes itself.

If the initial commit was amended, review with `git show --stat` and push
with `git push --force-with-lease`; otherwise nothing is committed.
Requires Python 3.8+ and git. Works on macOS, Linux and Windows.
"""

import argparse
import datetime as _dt
import re
import subprocess
import sys
from pathlib import Path
from typing import List, NoReturn, Optional

REPO_ROOT = Path(__file__).resolve().parent
TEMPLATE_STEM = "template"
PROJECT_EXTS = ("kicad_pro", "kicad_sch", "kicad_pcb", "kicad_prl", "kicad_dru")
NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
SETUP_LIBS = REPO_ROOT / "setup-libs.py"


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


def git(*args: str) -> Optional[subprocess.CompletedProcess]:
    """Run git in the repo; None if git is not installed."""
    try:
        return subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None


def in_git_repo() -> bool:
    result = git("rev-parse", "--is-inside-work-tree")
    return bool(result and result.returncode == 0)


def commit_count() -> int:
    result = git("rev-list", "--count", "HEAD")
    if result and result.returncode == 0:
        try:
            return int(result.stdout.strip())
        except ValueError:
            pass
    return 0


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
# main
# --------------------------------------------------------------------------- #
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Initialise this repository from the KiCad template: rename the "
        "project files, fill the title block, run setup-libs.py, then delete this "
        "script. Every prompt can be pre-answered with a flag.",
    )
    parser.add_argument("--name", help="project / repository name (default: from git remote 'origin')")
    parser.add_argument("--pbs", help="PBS number for the title block, any format (default: prompted, may be empty)")
    parser.add_argument("--title", help="board title (default: the project name)")
    parser.add_argument("--date", help="title block date, YYYY-MM-DD (default: today)")
    parser.add_argument("--skip-libs", action="store_true", help="do not run setup-libs.py")
    amend = parser.add_mutually_exclusive_group()
    amend.add_argument("--amend", action="store_true",
                       help="fold the changes into GitHub's 'Initial commit' without asking")
    amend.add_argument("--no-amend", action="store_true",
                       help="never touch the initial commit, only stage the changes")
    parser.add_argument("--keep", action="store_true", help="keep this script instead of deleting it")
    parser.add_argument("-y", "--yes", action="store_true", help="assume yes for confirmations")
    args, libs_args = parser.parse_known_args()
    # Anything unknown (e.g. --libs-path, --clone-with, --skip-partdb) is passed on to setup-libs.py.
    args.libs_args = libs_args
    return args


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
        print("\n⏭️  skipping setup-libs.py")
    elif SETUP_LIBS.is_file():
        print()
        sys.stdout.flush()  # keep our output ahead of the child's when piped
        extra = ["-y"] if args.yes else []
        subprocess.run([sys.executable, str(SETUP_LIBS), *extra, *args.libs_args], check=False)
    else:
        print(f"\n⚠️  {SETUP_LIBS.name} not found – set up kicad-custom-libs by hand (see README)")

    # --- stage + self-destruct ------------------------------------------- #
    me = Path(__file__).resolve()
    if use_git:
        git("add", "-A", "--", *[p.name for p in renamed], *([readme.name] if readme else []))
        if not args.keep and git_tracked(me):
            git("rm", "-q", "--cached", "--", me.name)

    # Fold everything into the single commit GitHub created from the template,
    # so the project starts with one clean conventional commit.
    message = f"feat: :tada: initialise project {name}"
    amended = False
    if use_git and not args.no_amend:
        if commit_count() != 1:
            if args.amend:
                print("\n⚠️  more than one commit on this branch – not touching history")
        elif args.amend or confirm(
            f"\n✏️  amend the initial commit to '{message}'?", True, args.yes
        ):
            result = git("commit", "--amend", "-m", message)
            if result and result.returncode == 0:
                amended = True
            else:
                print(f"⚠️  git commit --amend failed: {(result.stderr if result else '').strip()}")

    if args.keep:
        print(f"\n🧷 keeping {me.name} (--keep)")
    else:
        me.unlink()
        print(f"\n🗑️  removed {me.name}")

    if amended:
        print("\n✅ done. Review with:  git show --stat")
        print("   then push with:     git push --force-with-lease")
    else:
        print("\n✅ done. Review with:  git status && git diff --cached")
        print(f"   then commit, e.g.:  git commit -m '{message}'")


if __name__ == "__main__":
    main()

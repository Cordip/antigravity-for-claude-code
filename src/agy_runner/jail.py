"""The bubblewrap jail around agy (Linux).

Why a jail instead of agy's own knobs (measured on agy 1.2.11):
  * --sandbox confines the SHELL only, while agy's file-write tool still writes anywhere
    once permissions are skipped, and reads and the network are unrestricted;
  * without skip-permissions every permissioned tool is auto-denied headless, so agy
    cannot run a single test command.
Under bwrap both tools hit the same read-only mount, so --dangerously-skip-permissions is
safe to pass: the kernel, not agy's prompt, is the boundary. agy's terminal sandbox (sbox)
cannot nest inside bwrap ("remount root ro: operation not permitted"), so the jail mounts
a private settings copy with enableTerminalSandbox=false over agy's own. The user's
settings.json is never modified.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass, field

from .options import option, option_on

# Paths under $HOME hidden inside the jail (empty tmpfs / /dev/null). Override with
# AGY_ISOLATION_HIDE (space-separated, relative to $HOME).
HIDE_DEFAULT = (
    ".ssh .gnupg .aws .azure .kube .docker .config/gh .config/gcloud .password-store "
    ".local/share/keyrings .netrc .git-credentials .claude .claude.json .codex"
)

# Caches (workspace jail only). $HOME is read-only in the jail, so without this every
# package manager fails on its cache (live trial 2: uv could not write ~/.cache/uv, and agy
# worked around it by adding a cache dir to pyproject.toml). ~/.cache as a whole is NOT made
# writable: it holds shell init scripts (p10k instant prompt, zoxide init) that run outside
# the jail on the next shell start. Instead XDG_CACHE_HOME points at a private, persistent
# jail cache, and the real package caches below are bound into it (xdg:<name>) or at their
# own path (tools that ignore XDG). Only directories that exist are bound. A cache the jail
# writes can later run outside it; shared_caches=off closes that at the cost of a cold
# cache. Override the list with AGY_ISOLATION_CACHES (same syntax).
CACHES_DEFAULT = (
    "xdg:uv xdg:pip xdg:go-build .npm .cargo/registry .cargo/git go/pkg/mod "
    ".gradle/caches .m2/repository"
)

SETTINGS_REL = ".gemini/antigravity-cli/settings.json"


class IsolationUnavailable(Exception):
    """The jail cannot be built; the caller exits 16 (fail closed, never run unjailed)."""


def can_isolate() -> bool:
    return (
        platform.system() == "Linux"
        and shutil.which("bwrap") is not None
    )


def real_dir(path: str) -> str | None:
    p = os.path.realpath(os.path.expanduser(path))
    return p if os.path.isdir(p) else None


def settings_copy(dst: str) -> bool:
    """Write agy's settings with the terminal sandbox off to dst. False when the user has
    no settings.json (nothing to override; agy's default leaves sbox off)."""
    src = os.path.join(os.path.expanduser("~"), SETTINGS_REL)
    if not os.path.isfile(src):
        return False
    try:
        with open(src, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise IsolationUnavailable(f"could not read {src} as JSON") from exc
    if not isinstance(data, dict):
        raise IsolationUnavailable(f"could not read {src} as JSON")
    data["enableTerminalSandbox"] = False
    with open(dst, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return True


def _git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *args], capture_output=True, text=True, check=False, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


@dataclass
class Jail:
    mode: str  # workspace | readonly
    add_dirs: list[str] = field(default_factory=list)
    dry_run: bool = False
    notes: list[str] = field(default_factory=list)

    def command(self, settings: str | None) -> list[str]:
        """`bwrap <mounts...>`; agy's argv is appended by the caller. settings = the path
        of the settings copy to mount over agy's own ("" or None = none)."""
        home = real_dir(os.environ.get("HOME", "~"))
        if home is None:
            raise IsolationUnavailable(f"HOME ({os.environ.get('HOME')}) is not a directory")
        cwd = os.path.realpath(os.getcwd())

        rw: list[str] = []
        if self.mode == "workspace":
            root = _git("rev-parse", "--show-toplevel") or cwd
            rw.append(os.path.realpath(root))
            # A linked worktree commits into the main repository's git dir.
            common = _git("rev-parse", "--path-format=absolute", "--git-common-dir")
            if common and os.path.isdir(common):
                common = os.path.realpath(common)
                if not (common + "/").startswith(rw[0] + "/"):
                    rw.append(common)
        for d in self.add_dirs:
            p = real_dir(d)
            if p is None:
                raise IsolationUnavailable(f"--dir '{d}' is not a directory")
            rw.append(p)

        extra: list[str] = []
        if self.mode == "workspace":
            for raw in option("ISOLATION_WRITABLE").split():
                p = raw
                if p == "~":
                    p = home
                elif p.startswith("~/"):
                    p = os.path.join(home, p[2:])
                w = real_dir(p)
                if w is None:
                    self.notes.append(
                        f"isolation_writable path '{p}' is not a directory — skipped"
                    )
                    continue
                extra.append(w)

        for p in rw + extra:
            # A writable root at or above $HOME would put every dotfile back in reach.
            if p in ("/", home):
                raise IsolationUnavailable(
                    f"refusing to make '{p}' writable (run from a project directory)"
                )
            if (home + "/").startswith(p + "/"):
                raise IsolationUnavailable(f"refusing to make '{p}' writable: it contains $HOME")

        cmd = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
               "--tmpfs", "/tmp"]
        if self.mode == "workspace":
            jc = os.environ.get("AGY_JAIL_CACHE") or os.path.join(home, ".cache", "agy-jail")
            if not self.dry_run:
                try:
                    os.makedirs(jc, exist_ok=True)
                except OSError as exc:
                    raise IsolationUnavailable(f"could not create the jail cache {jc}") from exc
            cmd += ["--bind", jc, jc, "--setenv", "XDG_CACHE_HOME", jc]
            if option_on("SHARED_CACHES"):
                caches = os.environ.get("AGY_ISOLATION_CACHES", CACHES_DEFAULT)
                for rel in caches.split():
                    if rel.startswith("xdg:"):
                        name = rel[4:]
                        src, dst = os.path.join(home, ".cache", name), os.path.join(jc, name)
                    else:
                        src = dst = os.path.join(home, rel)
                    if not os.path.isdir(src) or os.path.islink(src):
                        continue
                    if not self.dry_run and dst != src:
                        try:
                            os.makedirs(dst, exist_ok=True)
                        except OSError:
                            continue
                    cmd += ["--bind", src, dst]
        else:
            # readonly: tools still get a cache, a throwaway one in the private /tmp.
            cmd += ["--setenv", "XDG_CACHE_HOME", "/tmp/.cache"]
        for p in extra:
            cmd += ["--bind", p, p]
        # Hidden paths come after the extra writable ones, so a broad isolation_writable
        # entry cannot re-expose them.
        for rel in os.environ.get("AGY_ISOLATION_HIDE", HIDE_DEFAULT).split():
            p = os.path.join(home, rel)
            if os.path.islink(p):
                continue
            if os.path.isdir(p):
                cmd += ["--tmpfs", p]
            elif os.path.isfile(p):
                cmd += ["--ro-bind", "/dev/null", p]
        gemini = os.path.join(home, ".gemini")
        if os.path.isdir(gemini):
            cmd += ["--bind", gemini, gemini]
        if settings:
            cmd += ["--bind", settings, os.path.join(home, SETTINGS_REL)]
        for p in rw:
            cmd += ["--bind", p, p]
        cmd += ["--setenv", "TMPDIR", "/tmp", "--chdir", cwd, "--new-session",
                "--die-with-parent"]
        return cmd


_RO_PATH = re.compile(r"""(/[^\s'"`:,()\[\]]+)""")


def readonly_paths(texts: list[str]) -> list[str]:
    """Paths under $HOME that agy's reply or stderr reports as read-only. Advisory: the
    caller names them and points at isolation_writable, so an unlisted cache costs one
    rerun instead of an agy workaround in the repository."""
    home = os.environ.get("HOME", "")
    found: list[str] = []
    for text in texts:
        for line in text.splitlines():
            if "read-only file system" not in line.lower():
                continue
            for m in _RO_PATH.finditer(line):
                p = m.group(1)
                if home and p.startswith(home + "/") and p not in found:
                    found.append(p)
    return sorted(found)[:3]

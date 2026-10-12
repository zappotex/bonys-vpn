# SPDX-License-Identifier: GPL-3.0-or-later
"""Dateien von Bony's VPN an ihren Platz legen – für den Paketbau – und alte Installationen aufräumen.

Eigenständig lauffähig (nur Standardbibliothek, keine Importe aus dem Paket):

    python3 -I install.py --package bonys-vpn --root PAKETORDNER           # Paketbau (build-deb.sh)
    python3 -I install.py --package bonys-vpn-agentpc --root PAKETORDNER
    python3 -I install.py --remove-legacy                                    # als root (postinst)

``--remove-legacy`` entfernt die alte Installation unter /usr/local (bis Bony's Agents 0.14 im Agent-PC
über ``install.py --variant agent``). Tunnel in /etc/wireguard und der Kill-Switch-Zustand in
/etc/bonys-vpn bleiben unverändert. Die alte polkit-Regel „Benutzer ohne Passwort“ übernimmt das Paket
bonys-vpn-agentpc (``--migrate-agent-rule``).
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = "bonys_vpn"
MODULES = ("__init__.py", "conf.py", "killswitch.py", "helper.py", "cli.py", "install.py", "model.py", "gui.py",
           "host.py", "updates.py", "signing.py")
# Von der Oberfläche zur Laufzeit gebraucht (relativ zu data/)
GUI_DATA = ("bonys-vpn.png", "icons/bonys-vpn-connected.svg", "icons/bonys-vpn-disconnected.svg",
            "icons/bonys-vpn-blocked.svg", "icons/bonys-vpn-warning.svg")

# ---------- Paket bonys-vpn (nichts unter /etc: keine Conffiles, den Rest legt der Helfer an)
LIB = "usr/lib/bonys-vpn"
HELPER = "usr/sbin/bonys-vpn-helper"
CLI = "usr/bin/bonys-vpn"
GUI = "usr/bin/bonys-vpn-gui"
DESKTOP = "usr/share/applications/bonys-vpn.desktop"
ICON = "usr/share/icons/hicolor/96x96/apps/bonys-vpn.png"
POLICY = "usr/share/polkit-1/actions/io.github.bonys-agents.vpn.policy"
RULES = "usr/share/polkit-1/rules.d/50-bonys-vpn.rules"
UNIT = "usr/lib/systemd/system/bonys-vpn-killswitch.service"

# ---------- Paket bonys-vpn-agentpc (nur im Agent-PC von Bony's Agents)
GROUP = "bonys-vpn"
AGENT_RULES = "usr/share/polkit-1/rules.d/49-bonys-vpn-agentpc.rules"
AGENT_AUTOSTART = "etc/xdg/autostart/bonys-vpn-agentpc-tray.desktop"
AGENT_MARKER = "usr/share/bonys-vpn/agentpc"
STATE_DIR = "etc/bonys-vpn"
# Steuerung des Agent-PCs: QEMU-User-Netz (SSH-Weiterleitung, Host 10.0.2.2, DNS 10.0.2.3).
AGENT_EXCEPTIONS = ["10.0.2.0/24"]

# ---------- alte Installation (install.py bis Bony's Agents 0.14, Agent-PC)
LEGACY = {
    "lib": "usr/local/lib/bonys-vpn",
    "files": ("usr/local/sbin/bonys-vpn-helper", "usr/local/bin/bonys-vpn", "usr/local/bin/bonys-vpn-gui",
              "usr/local/share/applications/io.github.bonys_agents.vpn.desktop",
              "etc/xdg/autostart/bonys-vpn-tray.desktop"),
    "unit": "etc/systemd/system/bonys-vpn-killswitch.service",
    "rules": "etc/polkit-1/rules.d/50-bonys-vpn.rules",
}
KS_UNIT = "bonys-vpn-killswitch.service"

WRAPPER = """#!/usr/bin/python3 -IB
# SPDX-License-Identifier: GPL-3.0-or-later
# {what} – Bony's VPN
import sys

sys.path.insert(0, "/{lib}")
from {pkg}.{module} import main  # noqa: E402

sys.exit(main())
"""


def clean_env() -> dict[str, str]:
    """Umgebung für externe Programme – ohne Variablen, die fremde Bibliotheken oder Python-Pfade einschleusen."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONPATH", "PYTHONHOME")}
    env["PATH"] = "/usr/sbin:/usr/bin:/sbin:/bin"
    return env


def _write(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def build(package: str, root: Path) -> list[Path]:
    """Dateien eines Pakets unter ``root`` anlegen (Paketbau). Gibt die geschriebenen Pfade zurück."""
    root = Path(root)
    written: list[Path] = []
    data = HERE / "data"

    def put(rel: str, content: str | bytes, mode: int = 0o644) -> None:
        p = root / rel
        _write(p, content.encode("utf-8") if isinstance(content, str) else content, mode)
        written.append(p)

    if package == "bonys-vpn":
        for m in MODULES:
            put(f"{LIB}/{PKG}/{m}", (HERE / m).read_bytes())
        for rel in GUI_DATA:
            put(f"{LIB}/{PKG}/data/{rel}", (data / rel).read_bytes())
        put(HELPER, WRAPPER.format(what="Root-Helfer (über pkexec)", lib=LIB, pkg=PKG, module="helper"), 0o755)
        put(CLI, WRAPPER.format(what="Kommandozeile", lib=LIB, pkg=PKG, module="cli"), 0o755)
        put(GUI, WRAPPER.format(what="Oberfläche", lib=LIB, pkg=PKG, module="gui"), 0o755)
        put(DESKTOP, (data / "bonys-vpn.desktop").read_text("utf-8").replace("@GUI@", f"/{GUI}"))
        put(ICON, (data / "bonys-vpn.png").read_bytes())
        put(POLICY, (data / "io.github.bonys-agents.vpn.policy").read_text("utf-8").replace("@HELPER@", f"/{HELPER}"))
        put(RULES, (data / "host.rules").read_text("utf-8").replace("@HELPER@", f"/{HELPER}"))
        put(UNIT, (data / "bonys-vpn-killswitch.service").read_text("utf-8"))
    elif package == "bonys-vpn-agentpc":
        put(AGENT_RULES, (data / "agentpc.rules").read_text("utf-8").replace("@GROUP@", GROUP))
        put(AGENT_AUTOSTART, (data / "bonys-vpn-tray.desktop").read_text("utf-8").replace("@GUI@", f"/{GUI}"))
        put(AGENT_MARKER, "Bony's VPN läuft hier im Agent-PC von Bony's Agents (Paket bonys-vpn-agentpc).\n")
    else:
        raise ValueError(f"unbekanntes Paket: {package}")
    return written


def _systemctl(*args: str) -> subprocess.CompletedProcess | None:
    if not shutil.which("systemctl", path=clean_env()["PATH"]):
        return None
    return subprocess.run(["systemctl", *args], capture_output=True, text=True, env=clean_env())


def remove_legacy(root: Path = Path("/"), live: bool | None = None) -> list[str]:
    """Alte Installation unter /usr/local entfernen. Tunnel, Kill-Switch-Zustand und die alte polkit-Regel
    bleiben. War der Kill-Switch eingeschaltet, zeigt er danach auf die Unit aus dem Paket."""
    root = Path(root)
    live = root == Path("/") if live is None else live
    removed: list[str] = []
    for rel in LEGACY["files"]:
        p = root / rel
        if p.exists() or p.is_symlink():
            p.unlink()
            removed.append(f"/{rel}")
    lib = root / LEGACY["lib"]
    if lib.exists():
        shutil.rmtree(lib)
        removed.append(f"/{LEGACY['lib']}")
    unit = root / LEGACY["unit"]
    if unit.exists():
        enabled = False
        if live:
            r = _systemctl("is-enabled", KS_UNIT)
            enabled = r is not None and r.stdout.strip() == "enabled"
            if enabled:
                _systemctl("disable", KS_UNIT)  # Verweis auf die alte Datei entfernen
        unit.unlink()
        removed.append(f"/{LEGACY['unit']}")
        if live:
            _systemctl("daemon-reload")
            if enabled:
                _systemctl("enable", KS_UNIT)   # jetzt die Unit aus dem Paket
    return removed


def migrate_agent_rule(root: Path = Path("/"), live: bool | None = None) -> str | None:
    """Alte Regel „Benutzer X ohne Passwort“ (Agent-PC) → X kommt in die Gruppe bonys-vpn, die Regel geht.
    Gibt den Benutzer zurück (oder None, wenn es keine alte Regel gab)."""
    root = Path(root)
    live = root == Path("/") if live is None else live
    rules = root / LEGACY["rules"]
    try:
        text = rules.read_text("utf-8")
    except OSError:
        return None
    m = re.search(r'subject\.user == "([a-z_][a-z0-9_-]{0,31})"', text)
    if not m or "io.github.bonys-agents.vpn" not in text:
        return None
    user = m.group(1)
    if live:
        r = subprocess.run(["usermod", "-a", "-G", GROUP, user], capture_output=True, text=True, env=clean_env())
        if r.returncode != 0:
            return None  # Benutzer gibt es nicht mehr o. Ä. – alte Regel bleibt
    rules.unlink()
    return user


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Bony's VPN: Paketdateien anlegen bzw. alte Installation aufräumen.")
    p.add_argument("--package", choices=["bonys-vpn", "bonys-vpn-agentpc"])
    p.add_argument("--root", default="/", help="Zielverzeichnis")
    p.add_argument("--remove-legacy", action="store_true", help="alte Installation unter /usr/local entfernen")
    p.add_argument("--migrate-agent-rule", action="store_true",
                   help="alte polkit-Regel des Agent-PCs in die Gruppe bonys-vpn übernehmen")
    args = p.parse_args(argv)
    root = Path(args.root)
    if args.package:
        if root == Path("/"):
            print("Fehler: --package nur mit --root (die Dateien gehören dem .deb).", file=sys.stderr)
            return 1
        build(args.package, root)
    if (args.remove_legacy or args.migrate_agent_rule) and root == Path("/") and os.geteuid() != 0:
        print("Fehler: Bitte als root ausführen.", file=sys.stderr)
        return 1
    if args.remove_legacy:
        for path in remove_legacy(root):
            print(f"Alte Installation entfernt: {path}")
    if args.migrate_agent_rule:
        user = migrate_agent_rule(root)
        if user:
            print(f"Benutzer {user} darf Bony's VPN jetzt über die Gruppe {GROUP} ohne Passwort steuern.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

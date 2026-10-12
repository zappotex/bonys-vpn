# SPDX-License-Identifier: GPL-3.0-or-later
"""Der Rechner, auf dem Bony's VPN läuft – ohne GTK, damit es sich testen lässt.

- Eigener Rechner oder Agent-PC von Bony's Agents? (Paket ``bonys-vpn-agentpc`` bzw. alte Installation)
- Fehlende Pakete (wireguard-tools, nftables) nach Rückfrage per pkexec/apt nachinstallieren
- Kann polkit Regeln (``*.rules``)? Dann fragt ``status`` ohne Passwort (siehe data/host.rules)
- WireGuard-Verbindungen des NetworkManager erkennen (dieselbe Routing-Tabelle wie wg-quick)
- Bony's Agents: installiert? starten
"""

from __future__ import annotations

import ipaddress
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import updates

TOOLS = {"wg": "wireguard-tools", "wg-quick": "wireguard-tools", "nft": "nftables"}
PACKAGES = ("wireguard-tools", "nftables")
SYSTEM_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"

# Agent-PC: Das Paket bonys-vpn-agentpc legt diese Datei an. Bis Bony's Agents 0.14 gab es stattdessen
# eine eigene Installation unter /usr/local mit der Regel „Benutzer agent ohne Passwort“.
AGENTPC_MARKER = Path("/usr/share/bonys-vpn/agentpc")
LEGACY_AGENT_RULES = Path("/etc/polkit-1/rules.d/50-bonys-vpn.rules")
AGENTPC_RULES = Path("/usr/share/polkit-1/rules.d/49-bonys-vpn-agentpc.rules")
RULES_FILES = (Path("/usr/share/polkit-1/rules.d/50-bonys-vpn.rules"), AGENTPC_RULES, LEGACY_AGENT_RULES)

WHOLE_COMPUTER = "Das VPN gilt dann für deinen ganzen Rechner."
INTRO = (WHOLE_COMPUTER + "\n\nJedes Programm auf diesem Rechner – Browser, Mail, Updates und auch virtuelle "
         "Rechner wie die Agent-PCs von Bony's Agents – geht dann durch den Tunnel.\n\n"
         "Für Aktionen wie Verbinden und Trennen fragt dein Rechner nach dem Administrator-Passwort und "
         "merkt es sich einige Minuten.")
KILLSWITCH_WARNING = (
    "Kill-Switch für den ganzen Rechner einschalten?\n\n"
    "Solange kein Tunnel verbunden ist, kommt dann kein Programm auf diesem Rechner mehr ins Internet – "
    "auch nach einem Neustart nicht, bis du den Kill-Switch wieder abschaltest. Fällt der VPN-Server aus, "
    "bist du offline.\n\n"
    "Agent-PCs von Bony's Agents starten und laufen weiter (Steuerung, Fernzugriff und Herunterfahren gehen "
    "über diesen Rechner selbst), ihr Internet geht dann aber ebenfalls nur über den Tunnel.\n\n"
    "Geräte im Heimnetz (Drucker, NAS) erreichst du ohne Tunnel nur, wenn du ihr Netz als Ausnahme einträgst.")
INTRO_EN = (
    "The VPN then applies to your whole computer.\n\nEvery program on this computer – browser, mail, updates "
    "and also virtual computers such as the agent PCs of Bony's Agents – then goes through the tunnel.\n\n"
    "For actions like connecting and disconnecting your computer asks for the administrator password and "
    "remembers it for a few minutes.")
KILLSWITCH_WARNING_EN = (
    "Turn on the kill switch for the whole computer?\n\n"
    "As long as no tunnel is connected, no program on this computer can reach the internet – not even after "
    "a restart, until you turn the kill switch off again. If the VPN server fails, you are offline.\n\n"
    "Agent PCs of Bony's Agents keep starting and running (control, remote access and shutdown go through "
    "this computer itself), but their internet then also only goes through the tunnel.\n\n"
    "Devices in your home network (printer, NAS) are only reachable without a tunnel if you add their network "
    "as an exception.")

# Bony's Agents: .deb (/opt), sonst install.sh (~/.local/share/bonys-agents/app)
AGENTS_COMMANDS = (Path("/opt/bonys-agents/BonysAgents"), Path("/usr/bin/bonys-agents-gui"))


def _env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = updates.clean_env()
    env.update(extra or {})
    return env


# ---------------------------------------------------------------- Agent-PC oder eigener Rechner
def is_agentpc(marker: Path = AGENTPC_MARKER, legacy_rules: Path = LEGACY_AGENT_RULES) -> bool:
    """Läuft Bony's VPN in einem Agent-PC von Bony's Agents?"""
    if marker.exists():
        return True
    try:
        return "subject.user ==" in legacy_rules.read_text("utf-8")  # alte Regel „agent ohne Passwort“
    except OSError:
        return False


# ---------------------------------------------------------------- Pakete
def missing_tools(path: str = SYSTEM_PATH) -> list[str]:
    """Debian-Pakete, die für Bony's VPN noch fehlen (wireguard-tools, nftables)."""
    missing = []
    for tool, pkg in TOOLS.items():
        if not shutil.which(tool, path=path) and pkg not in missing:
            missing.append(pkg)
    return missing


def install_tools_script(packages: list[str] | None = None) -> str:
    pkgs = [p for p in (packages or list(PACKAGES)) if p in PACKAGES]
    return ("set -e\nexport DEBIAN_FRONTEND=noninteractive\napt-get update -q || true\n"
            f"apt-get install -y -q --no-install-recommends {' '.join(pkgs)}\n")


def resolvconf_missing(path: str = SYSTEM_PATH) -> bool:
    """wg-quick braucht für das DNS-Feld ``resolvconf`` (systemd-resolved oder openresolv)."""
    return not shutil.which("resolvconf", path=path)


# ---------------------------------------------------------------- polkit
def polkit_version(run=subprocess.run) -> tuple[int, ...] | None:
    try:
        out = run(["pkaction", "--version"], capture_output=True, text=True, timeout=5, env=_env()).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"(\d+)(?:\.(\d+))?", out or "")
    return tuple(int(g) for g in m.groups() if g is not None) if m else None


def rules_supported(run=subprocess.run) -> bool:
    """JavaScript-Regeln (``*.rules``) gibt es ab polkit 0.106. Ubuntu 22.04/Mint 21 haben 0.105 –
    dort gilt nur die Aktion selbst (``auth_admin_keep``), auch für den Status."""
    v = polkit_version(run)
    return v is not None and (v[0] > 0 or (len(v) > 1 and v[1] >= 106))


def status_without_password(rules: tuple[Path, ...] = RULES_FILES, run=subprocess.run) -> bool:
    """Darf ``status`` ohne Passwort? Sonst fragt die Oberfläche den Helfer nicht regelmäßig."""
    if not rules_supported(run):
        return False
    for f in rules:
        try:
            text = f.read_text("utf-8")
        except OSError:
            continue
        if any(m in text for m in ('command_line") == "', "subject.user ==", "isInGroup(")):
            return True
    return False


# ---------------------------------------------------------------- NetworkManager
NM_ACTIVATION_EXTERNAL = 0x80   # NM_ACTIVATION_STATE_FLAG_EXTERNAL: nur übernommen, nicht selbst verbunden


def _nm_external(path: str, run) -> bool:
    """Hat der NetworkManager die Verbindung nur übernommen? Er meldet jede WireGuard-Schnittstelle als
    aktive Verbindung – auch die, die wg-quick (also Bony's VPN) angelegt hat. Im Zweifel: nein."""
    if not path.startswith("/org/freedesktop/NetworkManager/") or not shutil.which("busctl"):
        return False
    try:
        proc = run(["busctl", "get-property", "org.freedesktop.NetworkManager", path,
                    "org.freedesktop.NetworkManager.Connection.Active", "StateFlags"], capture_output=True,
                   text=True, timeout=5, env=_env({"LC_ALL": "C"}))
        kind, value = proc.stdout.split()
        return proc.returncode == 0 and kind == "u" and bool(int(value) & NM_ACTIVATION_EXTERNAL)
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


def nm_wireguard(run=subprocess.run) -> set[str]:
    """Aktive WireGuard-Verbindungen des NetworkManager. Die trennt nur der NetworkManager selbst
    (wg-quick kennt sie nicht), und sie benutzen dieselbe Routing-Tabelle wie wg-quick (51820).
    Tunnel, die der NetworkManager nur übernommen hat (mit wg-quick verbunden), zählen nicht."""
    if not shutil.which("nmcli"):
        return set()
    try:
        proc = run(["nmcli", "-t", "-f", "NAME,TYPE,ACTIVE-PATH", "connection", "show", "--active"],
                   capture_output=True, text=True, timeout=5, env=_env({"LC_ALL": "C"}))
    except (OSError, subprocess.SubprocessError):
        return set()
    out = set()
    for line in proc.stdout.splitlines():
        rest, _, path = line.replace("\\:", "\0").rpartition(":")
        name, _, kind = rest.rpartition(":")
        if kind == "wireguard" and not _nm_external(path.replace("\0", ":"), run):
            out.add(name.replace("\0", ":"))
    return out


# ---------------------------------------------------------------- Kill-Switch-Ausnahmen
def parse_exceptions(text: str) -> list[str]:
    """„192.168.178.0/24, 10.0.0.5“ → Liste. Ungültige Einträge → ValueError mit dem Eintrag."""
    out = []
    for item in text.replace(";", ",").replace("\n", ",").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            out.append(str(ipaddress.ip_network(item, strict=False)))
        except ValueError:
            raise ValueError(f"„{item}“ ist keine IP-Adresse und kein Netz (z. B. 192.168.178.0/24).") from None
    return out


# ---------------------------------------------------------------- Bony's Agents
def agents_command(home: Path | None = None) -> list[str] | None:
    """Befehl, der Bony's Agents startet – None, wenn es nicht installiert ist."""
    home = home or Path.home()
    for p in (*AGENTS_COMMANDS, home / ".local/share/bonys-agents/app/bin/bonys-agents-gui"):
        if p.is_file() and os.access(p, os.X_OK):
            return [str(p)]
    return None


def open_agents(home: Path | None = None) -> bool:
    cmd = agents_command(home)
    if cmd is None:
        return False
    subprocess.Popen(cmd, env=_env(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    return True


def open_url(url: str) -> None:
    """Adresse im Standardbrowser öffnen (als Benutzer, ohne auf den Browser zu warten)."""
    if not re.match(r"^https://[\w.-]+(/[\w./@%#?=&+-]*)?$", url):
        raise ValueError("ungültige Adresse")
    opener = shutil.which("xdg-open") or shutil.which("gio")
    if opener is None:
        return
    cmd = [opener, url] if opener.endswith("xdg-open") else [opener, "open", url]
    subprocess.Popen(cmd, env=_env(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)

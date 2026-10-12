# SPDX-License-Identifier: GPL-3.0-or-later
"""Bony's VPN – WireGuard-Tunnel verwalten, auf dem eigenen Rechner und im Agent-PC von Bony's Agents.

Nur die Python-Standardbibliothek und Debian-Pakete (python3, wireguard-tools, nftables, polkit,
für die Oberfläche python3-gi und GTK 3). Nur relative Importe – der Helfer läuft als root mit
``python3 -I`` aus /usr/lib/bonys-vpn.

- ``conf``       – .conf prüfen, Tunnelnamen, Schlüssel verbergen
- ``killswitch`` – nftables-Regelwerk des Kill-Switch (als Text)
- ``helper``     – ``bonys-vpn-helper``, läuft als root über pkexec
- ``cli``        – ``bonys-vpn``, die Kommandozeile (offizielle Schnittstelle, siehe docs/cli.md)
- ``model``      – Logik der Oberfläche ohne GTK
- ``gui``        – ``bonys-vpn-gui`` (GTK 3)
- ``host``       – eigener Rechner: fehlende Pakete, NetworkManager, polkit, Agent-PC-Erkennung
- ``updates``    – Update-Suche (GitHub-Releases, APT), geprüfte Downloads; auch für Bony's Agents
- ``signing``    – minisign-Signaturen prüfen (Ed25519)
- ``install``    – Dateien für das .deb an ihren Platz legen, alte Installation entfernen
"""

__version__ = "1.0.1"
APP_NAME = "Bony's VPN"
# Version des JSON-Formats der Kommandozeile (docs/cli.md). Steigt nur bei inkompatiblen Änderungen –
# neue Felder kommen ohne neue Schema-Version dazu.
SCHEMA = 1

REPO = "zappotex/bonys-vpn"
REPO_URL = f"https://github.com/{REPO}"
COPYRIGHT = "Copyright © 2026 Bony"
LICENSE_ID = "GPL-3.0-or-later"

# Bony's Agents (Knopf im Menü „Info“)
AGENTS_REPO = "zappotex/BonysAgents"
AGENTS_URL = f"https://github.com/{AGENTS_REPO}"
YOUTUBE_CHANNEL_URL = "https://www.youtube.com/@BonysAgents"

# Öffentlicher minisign-Schlüssel – derselbe wie bei Bony's Agents. Die Release-Pipelines beider
# Projekte signieren damit die Datei SHA256SUMS (siehe docs/RELEASE.md).
UPDATE_PUBLIC_KEY = "RWSbJYUNae7BsW6nDlNspFi16gc1nsM4hGbdmXq9noaPLpeVMtvDdIM6"

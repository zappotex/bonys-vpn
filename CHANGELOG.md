# Änderungen

Alle Versionen von Bony's VPN. Bis zur Version 0.14 von [Bony's Agents](https://github.com/zappotex/BonysAgents)
war Bony's VPN ein Teil davon. Die Entstehung steht im Verlauf dieses Repositorys.

## 1.0.0

Erste eigenständige Version – Bony's VPN ist jetzt ein eigenes Projekt mit eigenen Paketen.

- **Eigene Pakete:** `bonys-vpn` (Oberfläche, Kommandozeile, Helfer, polkit, Kill-Switch-Unit) für jeden Rechner und
  `bonys-vpn-agentpc` nur für die Agent-PCs von Bony's Agents. Reiner Python-Code mit GTK 3 aus den Paketquellen,
  ein Paket für amd64 und arm64 (Debian 12/13, Ubuntu 22.04+, Linux Mint 21+), 84 KB.
- **Oberfläche auch auf dem eigenen Rechner** (bisher dort die Qt-Fassung in Bony's Agents): Hinweis „gilt für den
  ganzen Rechner“, Warnung vor dem Kill-Switch, Kill-Switch-Ausnahmen, Erkennung von NetworkManager-Tunneln, fehlende
  Pakete nachinstallieren, Hinweis bei fehlendem `resolvconf`.
- **Menü:** Nach Updates suchen, „Bony's Agents – virtuelle Agent-PCs“ (öffnen oder aus der offiziellen Quelle
  installieren, im Agent-PC ausgeblendet), Hilfe, Über.
- **Updates:** Suche über GitHub-Releases (höchstens einmal am Tag), Installation über die Paketquelle oder als
  geprüftes .deb (minisign-Signatur, Version, SHA-256).
- **Paketquelle:** gemeinsam mit Bony's Agents (`https://zappotex.github.io/BonysAgents/apt/`, Suite `bonys-vpn`) –
  `apt install bonys-vpn` geht überall, wo eine der beiden Paketquellen eingerichtet ist.
- **Stabile Schnittstelle:** `--json` für alle Befehle, Schema-Version 1, `bonys-vpn version`, Import von der
  Standardeingabe (`import - --name NAME`), Fehler als JSON. Beschrieben in `docs/cli.md`.
- **Agent-PC:** polkit über die Gruppe `bonys-vpn` statt eines festen Benutzernamens.
- **Umstieg:** Die alte Installation unter `/usr/local` (Bony's Agents bis 0.14) wird beim Installieren entfernt.
  Tunnel und Kill-Switch-Zustand bleiben, die alte polkit-Regel geht in die Gruppe über.

# Änderungen

Alle Versionen von Bony's VPN. Bis zur Version 0.14 von [Bony's Agents](https://github.com/zappotex/BonysAgents)
war Bony's VPN ein Teil davon. Die Entstehung steht im Verlauf dieses Repositorys.

## Unveröffentlicht

- **Bony-Netz: dieser Rechner nimmt keine Verbindungen an** (`tailscale up/login --shields-up`). Vorher erreichten ihn
  alle eigenen Agent-PCs auf allen Ports; ausgehende Verbindungen (z. B. die Hilfe des Kursleiters) gehen weiter.
- Gemeinsamer Baustein `bony_netz` 0.2.1: Das Instanz-Token folgt keiner Weiterleitung und geht nie über `http://`.
- Gemeinsamer Baustein `bony_netz` 0.1.1 (gleich wie in Bony's Agents; Korrektur im Beitrittsskript für Agent-PCs,
  betrifft Bony's VPN selbst nicht).
- **Bereich „Bony-Netz“** neben den WireGuard-Tunneln (oben im Fenster umschalten, nicht im Agent-PC): Server
  wählen und prüfen, Instanz anlegen oder löschen, Kursleiter-Code einlösen, Liste der Geräte der Instanz mit
  Netzadresse, online/offline und „zuletzt gesehen“ (alle 30 Sekunden, solange der Bereich sichtbar ist),
  „Entfernen …“ mit Rückfrage. Gemeinsamer Baustein `bony_netz` 0.1.0 und gemeinsame Anmeldung (`konto.json`) mit
  Bony's Agents: Eine dort angelegte Instanz erscheint ohne erneutes Anlegen.
- **„Diesen Rechner verbinden“:** bringt den Rechner mit Tailscale ins Netz der Instanz; fehlt Tailscale,
  Installation aus der offiziellen Paketquelle. Rückfrage, bevor ein anderes Tailscale-Konto ersetzt wird
  (es bleibt als Profil erhalten). Zustand immer aus `tailscale status --json`.
- **Helfer:** neue Befehle `netz start|up|down|join` – der Geräteschlüssel kommt nur über die Standardeingabe.
- Hinweis im Bereich, wenn der Kill-Switch das Bony-Netz sperrt (Ausnahme `100.64.0.0/10`).

## 1.0.1

- **Symbol in der Leiste bei jedem Start:** Wird Bony's VPN aus dem Startmenü geöffnet, bleibt es nach dem Schließen
  des Fensters als Symbol in der Leiste (Status, Schnellwahl, Meldung bei Abbruch) – wie früher das Fenster
  „Bony's VPN“ in Bony's Agents. Beenden über das Symbol → „Beenden“. Beim ersten Schließen erklärt eine
  Benachrichtigung das. Zeigt der Desktop keine Symbole (GNOME ohne Erweiterung), beendet Schließen das Programm.
  In 1.0.0 gab es das Symbol nur im Agent-PC (Autostart mit `--tray`).

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

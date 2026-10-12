# Bony's VPN – Sicherheitskonzept

Bony's VPN verwaltet WireGuard-Tunnel auf dem eigenen Linux-Rechner und in den Agent-PCs von
[Bony's Agents](https://github.com/zappotex/BonysAgents). Der Code liegt in `src/bonys_vpn/`. Er braucht nur
die Python-Standardbibliothek und Debian-Pakete: `wireguard-tools`, `nftables`, `pkexec` und `polkitd`, für die
Oberfläche `python3-gi` und `gir1.2-gtk-3.0`. Für das `DNS`-Feld einer Konfiguration ruft wg-quick
`resolvconf` auf. Den Befehl bringt systemd-resolved mit (Ubuntu ab 24.04, Linux Mint 22, Debian 13 mit
resolved, die Agent-PCs), sonst `openresolv`. Ist systemd-resolved aktiv, darf `openresolv` nicht zusätzlich
installiert werden – das Paket zieht es deshalb auch nicht automatisch nach. Fehlt `resolvconf`, zeigt die
Oberfläche bei Tunneln mit DNS-Feld einen Hinweis.

Bis Version 0.14 von Bony's Agents war Bony's VPN ein Teil davon. Seit Bony's VPN 1.0.0 ist es ein eigenes
Projekt mit eigenen Paketen. Bony's Agents nutzt es nur noch über die Kommandozeile (siehe `docs/cli.md`).

## Aufbau

| Teil | Läuft als | Aufgabe |
|---|---|---|
| `bonys-vpn` (`cli.py`) | Benutzer | Kommandozeile, offizielle Schnittstelle (`docs/cli.md`). Liest .conf-Dateien mit den Rechten des Benutzers |
| `bonys-vpn-gui` (`gui.py`, `model.py`, `host.py`) | Benutzer | Oberfläche (GTK 3), Tray-Symbol |
| `bonys-vpn-helper` (`helper.py`) | root, über `pkexec` | Die wenigen Aktionen, für die root nötig ist |
| `conf.py` | – | Prüft .conf-Dateien und Tunnelnamen, verbirgt Schlüssel |
| `killswitch.py` | – | Baut das nftables-Regelwerk als Text |
| `updates.py`, `signing.py` | Benutzer | Update-Suche, geprüfte Downloads (minisign), Installation über pkexec/apt |
| `install.py` | root (Paketbau, postinst) | Legt die Paketdateien an, räumt eine alte Installation auf |

## Was der Helfer darf

Nur diese festen Unterbefehle, sonst nichts:

| Befehl | Wirkung |
|---|---|
| `list`, `status [NAME]`, `show NAME` | Tunnel auflisten, Status als JSON, Konfiguration mit verborgenen Schlüsseln |
| `up NAME`, `down [NAME]` | `wg-quick up/down`. `up` trennt vorher einen anderen aktiven Tunnel von Bony's VPN |
| `import NAME [--allow-hooks] [--replace]` | Konfiguration von der **Standardeingabe** prüfen und nach `/etc/wireguard/NAME.conf` schreiben |
| `delete NAME`, `rename ALT NEU`, `export NAME` | Nur für Tunnel, die Bony's VPN selbst angelegt hat |
| `autostart NAME on/off` | `systemctl enable/disable wg-quick@NAME`. Immer nur ein Tunnel |
| `killswitch on [--exception NETZ …] / off / status` | Kill-Switch (siehe unten) |

Eingaben werden so geprüft:

- **Tunnelnamen**: nur `[a-zA-Z0-9_=+.-]{1,15}`, nicht `.` oder `..` und kein `-` am Anfang, damit
  kein Name als Option von `wg-quick` oder `systemctl` gelesen wird. Damit sind Pfade (`../`),
  Shell-Zeichen (`;`, `$()`, Backticks) und Leerzeichen ausgeschlossen. Für den Autostart ist
  zusätzlich kein `+` oder `=` erlaubt, weil systemd diese Zeichen in Unit-Namen nicht zulässt.
- **Keine Pfade**: Der Helfer bekommt nie einen Dateipfad. Die Kommandozeile liest die .conf als
  Benutzer und gibt den Inhalt über die Standardeingabe weiter. So kann niemand den Helfer dazu
  bringen, als root fremde Dateien (etwa `/etc/shadow`) zu lesen. Geschrieben wird nur in
  `/etc/wireguard` und `/etc/bonys-vpn`. Symbolische Links werden dort weder gelesen noch beschrieben.
- **Keine Shell**: `subprocess` läuft nur mit Argumentlisten, festem `PATH`
  (`/usr/sbin:/usr/bin:/sbin:/bin`) und leerer Umgebung. Der Helfer startet mit `python3 -I`, das
  `PYTHONPATH`, `PYTHONHOME` und das Benutzer-`site-packages` ignoriert.
- **Eigene Tunnel**: Was Bony's VPN anlegt, bekommt als erste Zeile `# Verwaltet von Bony's VPN`.
  Löschen, Umbenennen und Exportieren gehen nur bei solchen Tunneln. Andere WireGuard-Konfigurationen
  auf dem Host werden angezeigt und lassen sich verbinden, aber nicht verändern.
- **Sperre**: Jede ändernde Aktion hält eine Dateisperre (`/etc/bonys-vpn/.lock`), damit zwei Aufrufe
  sich nicht in die Quere kommen.

## Prüfung der .conf

- Höchstens 64 KiB, UTF-8, keine Steuerzeichen. Eine BOM und Windows-Zeilenenden werden entfernt.
- Nur die Abschnitte `[Interface]` (genau einmal) und `[Peer]` (mindestens einmal), nur bekannte Felder.
  Doppelte Felder sind nur erlaubt, wo wg-quick sie aneinanderhängt (`Address`, `DNS`, `AllowedIPs`, Hooks).
- Pflichtfelder: `PrivateKey`, `Address`, je Peer `PublicKey` und `AllowedIPs`.
- Schlüssel: 44 Zeichen Base64, ergeben genau 32 Byte.
- `Address`, `AllowedIPs`, `DNS` und `Endpoint` (`host:port` oder `[IPv6]:port`, Port 1–65535)
  werden einzeln geprüft. `SaveConfig = true` wird abgelehnt, weil wg-quick die Datei sonst beim
  Trennen überschreibt.
- **Fehlermeldungen nennen nur Zeile und Feldname, nie den Wert.**

## Warum Hook-Zeilen blockiert sind

`PreUp`, `PostUp`, `PreDown` und `PostDown` sind Shell-Befehle, die wg-quick **als root** ausführt.
Eine fremde .conf, etwa von einem VPN-Anbieter oder aus dem Netz, könnte damit beliebigen Code als
root starten. Der Helfer lehnt solche Konfigurationen deshalb ab. Nur mit dem ausdrücklichen Schalter
`--allow-hooks` werden sie übernommen. Die Oberfläche setzt ihn erst nach einer deutlichen Warnung,
die Kommandozeile heißt dafür `--hooks-erlauben` und zeigt die Warnung ebenfalls. `status` zeigt bei
solchen Tunneln einen Hinweis.

## Wo die Schlüssel liegen

- Nur in `/etc/wireguard/NAME.conf`, Rechte `600`, Besitzer `root:root`. Der Ordner selbst hat `700`.
  Geschrieben wird atomar: temporäre Datei mit `600` im selben Ordner, `fsync`, dann `rename`.
- **Nie** in Ausgaben, Protokollen, `vm.json`, Vorlagen-Metadaten, Tests oder im Repo.
  - `status`, `list` und `show` geben nur nicht geheime Angaben aus. `show` ersetzt `PrivateKey` und
    `PresharedKey` durch `(verborgen)`. Speichert der Editor `(verborgen)` zurück (`import --replace`),
    setzt der Helfer die gespeicherten Schlüssel wieder ein.
  - Live-Werte kommen aus `wg show NAME endpoints|latest-handshakes|transfer`, nie aus `wg show … dump`,
    denn `dump` enthält den privaten Schlüssel.
  - Fehlerausgaben fremder Programme (wg-quick, nft) laufen durch einen Filter, der alles entfernt,
    was wie ein WireGuard-Schlüssel aussieht.
- `export` ist die einzige Ausnahme. Es gibt die Konfiguration mit Schlüssel zurück, die
  Kommandozeile schreibt sie mit Rechten `600` und überschreibt keine vorhandene Datei.
- Die Tests prüfen, dass der private Schlüssel in keiner Ausgabe auftaucht.

## Kill-Switch

Eigene Tabelle `inet bonys_vpn` mit einer Kette am Hook `output` (Richtlinie `drop`). Erlaubt sind:

1. Loopback (`lo`) und die WireGuard-Geräte aller Tunnel
2. Antworten auf Verbindungen, die von außen hereinkommen (`ct direction reply`), etwa SSH zum Agent-PC
3. DHCP (v4/v6) und IPv6-Nachbarsuche, damit die Netzwerkkarte ihre Adresse behält
4. die Endpunkte aller Tunnel (IP + UDP-Port), damit der Tunnel aufgebaut werden kann
5. DNS (Port 53) **nur für root** und, falls vorhanden, für `systemd-resolve`. So lassen sich
   Endpunkt-Namen wie `xyz.myfritz.net` auflösen, bevor der Tunnel steht. Normale Programme
   bekommen ohne Tunnel kein DNS. Mit systemd-resolved können Namensanfragen anderer Programme
   ohne Tunnel über resolved hinausgehen, Daten aber nicht.
6. die Ausnahmeliste (Netze)

Alles andere wird mit „administratively prohibited“ abgewiesen. Programme bekommen so sofort einen
Fehler und hängen nicht.

- **Endpunkt-Namen** werden beim Einschalten, bei `up` und bei Änderungen an den Tunneln neu aufgelöst.
  Klappt das nicht, gelten die zuletzt bekannten Adressen aus `/etc/bonys-vpn/killswitch.json` weiter.
- **Atomar**: Das Regelwerk (`/etc/bonys-vpn/killswitch.nft`) ersetzt die Tabelle in einer einzigen
  `nft -f`-Transaktion. Es gibt also keinen Moment ohne Regeln, und andere Tabellen (Docker, ufw,
  wg-quick) bleiben unberührt.
- **Nach einem Neustart** lädt die Unit `bonys-vpn-killswitch.service` die Regeln vor
  `network-pre.target`, also bevor das Netz hochkommt. Sie hat absichtlich kein `ExecStop`, damit der
  Schutz bis zum Ausschalten bleibt.
- **Ausschalten** (`killswitch off`): Unit deaktivieren, Tabelle löschen, Regeldatei entfernen.
  Danach ist nichts mehr übrig. Beim Entfernen des Pakets (`apt remove bonys-vpn`) schaltet `prerm` den
  Kill-Switch ebenfalls ab, sonst bliebe der Rechner offline.
- **Grenzen**: Gefiltert wird nur Verkehr, den dieser Rechner selbst erzeugt. Weitergeleiteter Verkehr
  (Docker-Container, Bridges, libvirt) läuft nicht über `output`. Programme von Bony's Agents mit
  QEMU-User-Netz (die Agent-PCs selbst) sind dagegen erfasst, weil QEMU ein Prozess auf dem Host ist.


## Pakete

| Paket | Für | Inhalt |
|---|---|---|
| `bonys-vpn` | jeden Rechner | Oberfläche, Kommandozeile, Helfer, polkit-Aktion und `host.rules` (Administrator-Passwort), systemd-Unit des Kill-Switch, Desktop-Eintrag, Symbol, Paketquelle für Updates |
| `bonys-vpn-agentpc` | **nur** Agent-PCs von Bony's Agents | polkit-Regel „Gruppe `bonys-vpn` ohne Passwort“, Tray-Symbol nach der Anmeldung, Kill-Switch-Ausnahme `10.0.2.0/24`, Kennzeichnung als Agent-PC |

Beide Pakete sind `Architecture: all`: reiner Python-Code, nichts Kompiliertes und kein gebündeltes Qt.
Dasselbe Paket läuft auf amd64 und arm64.

| Pfad | Inhalt |
|---|---|
| `/usr/lib/bonys-vpn/bonys_vpn/` | Das Python-Paket (System-Python, `python3 -IB`) |
| `/usr/sbin/bonys-vpn-helper` | Helfer, `#!/usr/bin/python3 -IB` |
| `/usr/bin/bonys-vpn`, `/usr/bin/bonys-vpn-gui` | Kommandozeile, Oberfläche |
| `/usr/share/polkit-1/actions/io.github.bonys-agents.vpn.policy` | polkit-Aktion, gilt nur für `/usr/sbin/bonys-vpn-helper`, Standard `auth_admin_keep` |
| `/usr/share/polkit-1/rules.d/50-bonys-vpn.rules` | `host.rules`: `AUTH_ADMIN_KEEP` für aktive lokale Sitzungen, sonst `AUTH_ADMIN`. Einzige Ausnahme ohne Passwort: genau `bonys-vpn-helper status` |
| `/usr/share/polkit-1/rules.d/49-bonys-vpn-agentpc.rules` | nur `bonys-vpn-agentpc`: Gruppe `bonys-vpn` ohne Passwort |
| `/usr/lib/systemd/system/bonys-vpn-killswitch.service` | Kill-Switch nach dem Neustart (eingeschaltet nur über den Helfer) |
| `/usr/share/applications/bonys-vpn.desktop` | Desktop-Eintrag „Bony's VPN“ |
| `/etc/xdg/autostart/bonys-vpn-agentpc-tray.desktop` | nur `bonys-vpn-agentpc`: Tray-Symbol nach der Anmeldung |
| `/etc/apt/sources.list.d/bonys-vpn.sources` | Paketquelle für Updates (legt `postinst` an, siehe unten) |
| `/etc/wireguard/NAME.conf` | Tunnel (600, root:root), legt der Helfer an |
| `/etc/bonys-vpn/killswitch.json` | an/aus, Ausnahmen, zuletzt aufgelöste Endpunkte (keine Schlüssel) |

- Das Hauptpaket liefert unter `/etc` nichts aus (keine Conffiles). `/etc/bonys-vpn` und `/etc/wireguard` legt
  der Helfer bei Bedarf an. `apt purge bonys-vpn` löscht `/etc/bonys-vpn`, **nie** `/etc/wireguard`.
- `prerm remove` schaltet den Kill-Switch ab. Sonst bliebe der Rechner ohne Tunnel offline, ohne dass es noch
  ein Programm zum Abschalten gäbe. Bei einem Update bleibt er an.
- Alle Dateien gehören root und sind nur für root beschreibbar. Sonst könnte ein Benutzer den Code ändern, den
  pkexec als root startet.
- `Replaces: bonys-agents (<< 0.15)`: Bis 0.14 brachte das .deb von Bony's Agents Helfer, Kommandozeile,
  polkit-Dateien, Unit und Desktop-Eintrag an denselben Orten mit. `bonys-vpn` übernimmt diese Dateien.

## Unterschied Agent-PC / eigener Rechner

| | Agent-PC (`bonys-vpn` + `bonys-vpn-agentpc`) | eigener Rechner (`bonys-vpn`) |
|---|---|---|
| polkit | Gruppe `bonys-vpn` **ohne Passwort** | `auth_admin_keep`: Administrator-Passwort, einige Minuten gemerkt |
| Kill-Switch-Ausnahmen | `10.0.2.0/24`, das QEMU-User-Netz | keine (eigene Netze über „Kill-Switch-Ausnahmen …“, z. B. Drucker im Heimnetz) |
| Hinweise | keine | „Das VPN gilt dann für deinen ganzen Rechner“ vor dem ersten Verbinden/Import, Warnung vor dem Kill-Switch |
| Menü „Bony's Agents“ | ausgeblendet | Vorstellung, öffnen bzw. installieren |
| Einordnung | Der Benutzer des Agent-PCs hat dort ohnehin `sudo` ohne Passwort. Die Regel erspart nur die Abfrage und ist **keine Sicherheitsgrenze**: Jedes Programm im Agent-PC, auch der KI-Agent, kann den Tunnel steuern, den Kill-Switch abschalten und den Schlüssel lesen | Hier ist der Helfer die Grenze: Ohne Administrator-Passwort geht nichts außer `status` |

**Warum ein eigenes Paket statt einer Option?** Eine Regel „ohne Passwort“ im Hauptpaket – auch abgeschaltet –
wäre ein Schalter, den ein Fehler oder ein fremdes Skript auf dem eigenen Rechner umlegen könnte. Als eigenes
Paket kommt sie nur dorthin, wo jemand es ausdrücklich installiert: Bony's Agents tut das beim Einrichten
eines Agent-PCs (`apt install bonys-vpn bonys-vpn-agentpc` und `usermod -aG bonys-vpn BENUTZER`). Die Paketbeschreibung
warnt vor der Installation auf dem eigenen Rechner. Die Regel nennt keinen festen Benutzernamen, sondern
eine Gruppe – der Benutzer im Agent-PC kann beliebig heißen. polkit liest die Gruppen bei jeder Prüfung
neu, eine neue Anmeldung ist nicht nötig.

**Warum `10.0.2.0/24` im Agent-PC?** Bony's Agents steuert den Agent-PC über das QEMU-User-Netz:
Die SSH-Weiterleitung (`hostfwd`) kommt von `10.0.2.2`, der Host ist unter `10.0.2.2` erreichbar,
das QEMU-DNS liegt auf `10.0.2.3`. Der Gast-Agent (Befehle, Herunterfahren) läuft über
virtio-serial und braucht gar kein Netz. Ohne diese Ausnahme blieben Steuerung und Herunterfahren
trotzdem möglich, RDP und SSH aber nur eingeschränkt. Weil `10.0.2.3` in dieser Ausnahme liegt,
gehen DNS-Anfragen im Agent-PC bei getrenntem Tunnel weiter an das QEMU-DNS. Daten gehen nicht hinaus.

### Umstieg von der alten Installation im Agent-PC

Bis Bony's Agents 0.14 lag Bony's VPN im Agent-PC unter `/usr/local` (installiert mit `install.py --variant agent`),
mit der Regel `/etc/polkit-1/rules.d/50-bonys-vpn.rules` „Benutzer agent ohne Passwort“. Beim Installieren der
Pakete:

1. `bonys-vpn` (`postinst`, `install.py --remove-legacy`) entfernt `/usr/local/lib/bonys-vpn`, die alten
   Programme, den alten Desktop-Eintrag, den alten Autostart und die Unit unter `/etc/systemd/system`. War
   der Kill-Switch eingeschaltet, zeigt die Unit danach auf die aus dem Paket. **Tunnel in `/etc/wireguard`
   und `/etc/bonys-vpn/killswitch.json` bleiben unverändert.**
2. `bonys-vpn-agentpc` (`postinst`, `install.py --migrate-agent-rule`) legt die Gruppe `bonys-vpn` an, nimmt
   den Benutzer aus der alten Regel in die Gruppe auf und löscht die alte Regel.

## Kommandozeile

Siehe `docs/cli.md` (offizielle Schnittstelle mit Schema-Version).

## Oberfläche

`bonys-vpn-gui` (GTK 3 über `python3-gi`, Tray über Ayatana-AppIndicator) läuft als Benutzer und
ruft für alles, was root braucht, denselben Helfer auf wie die Kommandozeile.

**Warum GTK 3 und nicht Qt:** Cinnamon, Xfce, GNOME und MATE bauen auf GTK 3 auf, `python3-gi` gehört zur
Grundausstattung von Debian, Ubuntu und Mint. Dazu kommen nur `gir1.2-gtk-3.0` und
`gir1.2-ayatanaappindicator3-0.1`, zusammen etwa 2 MB. PySide6 aus Debian bräuchte zusätzlich rund
74 MB, selbst wenn die Qt6-Bibliotheken schon da sind (gemessen mit apt in Debian 13, Xfce), und in Debian 12
und Ubuntu 22.04 gibt es PySide6 gar nicht als Paket. Ein gebündeltes Qt (wie bei Bony's Agents) wäre über 100 MB groß
und müsste für jede Architektur einzeln gebaut werden. So ist das Paket 84 KB groß und läuft auf amd64 und
arm64 gleich. Nur unter KDE Plasma und LXQt (Qt) holt `gir1.2-gtk-3.0` die GTK-3-Bibliotheken nach.
Tray-Symbole funktionieren mit beiden Toolkits gleich (StatusNotifierItem). Keine pip-Pakete.

- **Tray:** StatusNotifierItem (Cinnamon, Xfce, KDE, MATE, LXQt), sonst das klassische Infobereich-Symbol
  (XEmbed). Zeigt der Desktop keine Statussymbole (GNOME ohne Erweiterung), läuft der Hintergrund ohne
  Symbol weiter und meldet nur Abbrüche. Eine Instanz (D-Bus): Ein zweiter Aufruf öffnet das Fenster der
  laufenden.
- **Status ohne ständiges pkexec:** Ob ein Tunnel verbunden ist und wie viele Daten fließen, liest die
  Oberfläche ohne root aus `/sys/class/net`. Den Helfer (`status`) fragt sie nur nach Aktionen, bei
  offenem Fenster alle 10 s (Handshake) und im Hintergrund, wenn 150 s nichts mehr hereinkommt, obwohl
  gesendet wird (beides nur, wenn `status` ohne Passwort geht, siehe unten).
- **Abbruch-Meldung:** Der Tunnel verschwindet ohne eigene Aktion, oder der Server antwortet nicht mehr:
  Handshake älter als 3 Minuten, obwohl mehr als 135 s nach dem letzten gesendet wurde. Ein untätiger
  Tunnel hat nur einen alten Handshake und gilt nicht als abgebrochen.
- **Öffentliche IP:** nur auf Knopfdruck, über `https://api.ipify.org`. Die Oberfläche nennt den Dienst.
- **Editor:** zeigt die Konfiguration mit `(verborgen)` statt Schlüssel (`show`). „Schlüssel anzeigen“ holt
  sie über `export`, nur bei eigenen Tunneln. Vor dem Speichern wird geprüft. Unverändert gelassenes
  `(verborgen)` setzt der Helfer wieder ein. Hook-Zeilen brauchen beim Import und beim Speichern eine
  ausdrückliche Bestätigung.
- **Export** schreibt mit Rechten `600` (atomar). Dateidialoge starten im persönlichen Ordner.
- **Sprache:** Deutsch, auf nicht deutschen Systemen Englisch (`LANGUAGE`, `LC_ALL`, `LC_MESSAGES`, `LANG`).
- **Eigener Rechner:** Vor dem ersten Verbinden oder Import der Hinweis „Das VPN gilt dann für deinen ganzen
  Rechner“ (abschaltbar mit „Nicht mehr anzeigen“), Kill-Switch nur nach einer zusätzlichen Warnung,
  Ausnahmen über das Menü. Fehlen `wireguard-tools` oder `nftables` (z. B. von Hand entfernt), bietet eine
  Leiste an, sie nach Rückfrage mit `pkexec apt-get install` nachzuinstallieren.
- **Einstellungen** der Oberfläche (Hinweis gesehen, Update-Suche) stehen in `~/.config/bonys-vpn/settings.json`,
  nie Tunnel oder Schlüssel.

## Eigener Rechner

### Status ohne Passwort

Die Oberfläche fragt bei offenem Fenster und verbundenem Tunnel alle 10 s den Helfer (`status`) nach dem Handshake. Mit
`auth_admin_keep` allein käme immer wieder die Passwortabfrage. Deshalb erlaubt `host.rules` der aktiven
lokalen Sitzung genau die Befehlszeile `/usr/sbin/bonys-vpn-helper status` (ohne weitere Argumente)
**ohne Passwort**. pkexec gibt sie den Regeln als `command_line` mit. Alles andere fragt weiter nach dem
Administrator-Passwort.

Zuerst war dafür eine zweite polkit-Aktion mit `org.freedesktop.policykit.exec.argv1 = status` gedacht.
Das greift nicht: pkexec nimmt die **erste** Aktion, deren `exec.path` passt, und eine Aktion ohne `argv1`
passt immer (im echten Test auf Mint 22 mit polkit 124 kam deshalb trotzdem die Abfrage).

`status` gibt nur nicht geheime Angaben aus: Tunnelnamen, Adressen, DNS, Endpunkte, Handshake, Datenmenge,
Kill-Switch-Zustand. Die sieht damit jeder Benutzer, der am Rechner angemeldet ist, Schlüssel nie. Fehlt die
Regel oder kann polkit keine Regeln (Version 0.105 in Ubuntu 22.04 und Linux Mint 21), fragt die Oberfläche
den Helfer nur beim Start, bei Aktionen und auf „Neu laden“ und liest Verbunden/Getrennt und die Datenmenge
aus `/sys`.

**Wie oft fragt polkit?** `auth_admin_keep` merkt sich die Anmeldung einige Minuten **für den aufrufenden
Prozess**. Im Fenster von Bony's VPN ist das immer derselbe Prozess, also eine Abfrage für mehrere
Aktionen. Jeder einzelne Aufruf von `bonys-vpn` auf der Kommandozeile ist ein neuer Prozess und fragt neu.

### WireGuard-Verbindungen des NetworkManager

Tunnel, die der NetworkManager verwaltet (z. B. über „Netzwerkverbindungen“ importiert), stehen oft
zusätzlich in `/etc/wireguard` und erscheinen dann als fremde Tunnel. Trennen kann sie nur der
NetworkManager (`wg-quick down` kennt sie nicht), und sie benutzen dieselbe Routing-Tabelle und Markierung
(51820) wie wg-quick. Die Oberfläche erkennt sie über `nmcli` (als Benutzer): Bei ihnen ist „Trennen“ aus,
mit dem Hinweis auf das Netzwerk-Symbol, und vor dem Verbinden eines anderen Tunnels kommt die Bitte,
den NetworkManager-Tunnel zuerst zu trennen.

### Kill-Switch auf dem eigenen Rechner und die Agent-PCs

Die Agent-PCs nutzen das QEMU-User-Netz: QEMU ist ein Prozess auf dem Host, ihr Internetverkehr ist also
Verkehr des Hosts und läuft durch `output`. Mit Kill-Switch und Tunnel geht er durch den Tunnel, ohne Tunnel
wird er abgewiesen. Alles, womit Bony's Agents die Agent-PCs steuert, braucht kein Internet und bleibt
erlaubt:

- QMP und Gast-Agent: Unix-Sockets bzw. virtio-serial, kein Netz
- SSH-, RDP- und SPICE-Weiterleitungen (`hostfwd`, 127.0.0.1): Loopback (`lo`) ist erlaubt
- Zugriffe aus dem Heimnetz auf eingeschalteten Fernzugriff: Antworten (`ct direction reply`) sind erlaubt
- DNS im Agent-PC (QEMU-DNS 10.0.2.3 → Resolver des Hosts über `lo` → systemd-resolved): geht, Daten nicht

Geprüft im echten Test (Bony's Agents 0.14.0, Linux Mint 22.3). Ohne Tunnel dauert das
Herunterfahren eines Agent-PCs mit Hermes Agent und Telegram-Anbindung länger (gemessen 65 s statt 20 s):
Das Hermes-Gateway versucht beim Beenden weiter Telegram zu erreichen, bis sein eigener Watchdog nach 60 s
abbricht. Das bleibt unter den 90 s, nach denen Bony's Agents hart ausschaltet. Dasselbe passiert bei jedem
Agent-PC ohne Internet, auch mit dem Kill-Switch im Agent-PC.


## Updates

- **Update-Suche:** höchstens einmal am Tag beim Öffnen des Fensters und über das Menü („Nach Updates
  suchen …“). Gefragt wird `https://api.github.com/repos/zappotex/bonys-vpn/releases/latest`, ohne Token,
  mit Zeitlimit. Ist GitHub nicht erreichbar, gibt es still „keine Updates“.
- **Installieren** nur nach Rückfrage, über pkexec:
  - Ist eine der Paketquellen eingerichtet (`bonys-vpn.sources` oder `bonys-agents.sources`), mit
    `apt-get install --only-upgrade bonys-vpn`. Updates kommen dann auch über die Aktualisierungsverwaltung.
  - Sonst (oder wenn die Paketquelle die Version noch nicht hat) das .deb aus dem offiziellen Release: nur
    über HTTPS und nur von `https://github.com/zappotex/bonys-vpn/releases/download/…`. Vorher prüft
    Bony's VPN die **minisign-Signatur** von `SHA256SUMS` mit dem fest eingebauten öffentlichen Schlüssel
    (derselbe wie bei Bony's Agents), ob der signierte Kommentar genau diese Version nennt (Schutz vor echten,
    aber alten Dateien), und die **SHA-256-Prüfsumme** der Datei. Stimmt etwas nicht, wird nichts installiert.
- **Paketquelle:** Bony's VPN und Bony's Agents teilen sich eine APT-Paketquelle auf GitHub Pages
  (`https://zappotex.github.io/BonysAgents/apt/`, signiert mit dem GPG-Schlüssel von Bony's Agents).
  Bony's Agents trägt die Suite `stable` ein, Bony's VPN die Suite `bonys-vpn` – beide enthalten alle
  Pakete. Zwei Einträge für dieselbe Suite mit verschiedenen `Signed-By` lehnt apt ab („Conflicting values set
  for option Signed-By“); mit eigenen Suiten vertragen sich beide Pakete, und jedes kann allein entfernt werden.

## Bony's Agents installieren

Auf dem eigenen Rechner zeigt das Menü „Bony's Agents – virtuelle Agent-PCs …“: kurze Beschreibung, Link zum
YouTube-Kanal und „Bony's Agents öffnen“ (wenn installiert: `/opt/bonys-agents/BonysAgents`, `bonys-agents-gui`
oder die Installation aus `install.sh`) bzw. „Bony's Agents installieren …“. Installiert wird nach Rückfrage
aus der offiziellen Quelle, genauso wie die Updates von Bony's VPN: aus der Paketquelle (`apt-get install
bonys-agents`), sonst das passende .deb (`amd64`/`arm64`) aus dem neuesten Release von `zappotex/BonysAgents`
mit Signatur- und Prüfsummenprüfung, dann `apt-get install ./datei.deb` über pkexec. Ein Fenster zeigt den
Fortschritt (Download-Balken, Ausgabe von apt), Fehler stehen in Klartext darüber. Im Agent-PC ist der Menüpunkt
ausgeblendet.

## Anbindung an Bony's Agents

Bony's Agents steuert Bony's VPN im Agent-PC nur über die Kommandozeile (`docs/cli.md`): als Benutzer per SSH
oder als root über den QEMU-Gast-Agent (`bonys-vpn-helper`, Konfigurationen über die Standardeingabe). Eine
.conf, die man beim Erstellen eines Agent-PCs auswählt, gelangt nur in diesen einen Agent-PC. Vorlagen,
Klone und die Übertragung beschreibt die Dokumentation von Bony's Agents.

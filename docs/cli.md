# Kommandozeile `bonys-vpn` – offizielle Schnittstelle

`bonys-vpn` ist die Kommandozeile von Bony's VPN und gleichzeitig die **offizielle Schnittstelle** für
andere Programme, vor allem für [Bony's Agents](https://github.com/zappotex/BonysAgents). Alles, was hier
steht, bleibt innerhalb einer Schema-Version gleich.

**Aktuell: Schema-Version 1** (seit Bony's VPN 1.0.0).

## Regeln

- Mit `--json` gibt jeder Befehl genau **ein JSON-Objekt** auf der Standardausgabe aus (UTF-8). Hinweise
  stehen auf der Standardfehlerausgabe. Ohne `--json` ist die Ausgabe für Menschen gedacht und kann sich ändern.
- Jedes JSON-Objekt enthält `schema` (Zahl) und `version` (Version von Bony's VPN, z. B. `"1.0.0"`).
- **Neue Felder** können jederzeit dazukommen, ohne dass `schema` steigt. Programme müssen unbekannte Felder
  ignorieren.
- **Entfernte oder umgedeutete Felder** gibt es nur mit einer neuen Schema-Version. Ein Programm, das Schema 1
  erwartet, prüft `schema == 1` (oder bei mehreren unterstützten Versionen, ob `schema` dabei ist).
- **Rückgabewert:** `0` = Erfolg, `1` = Fehler (mit `--json` zusätzlich `{"schema": 1, "version": …, "error": "…"}`),
  `2` = falscher Aufruf (argparse), `130` = abgebrochen (Strg+C).
- Fehlermeldungen sind deutsch und nennen **nie Werte aus einer Konfiguration**, insbesondere keine Schlüssel.
- Private und Preshared Keys erscheinen in **keiner** JSON-Ausgabe. Nur `bonys-vpn export` gibt eine
  Konfiguration mit Schlüssel aus, und zwar in eine Datei mit den Rechten `600`.

## Rechte

`bonys-vpn` läuft als Benutzer und ruft für alles, was root braucht, den Helfer `/usr/sbin/bonys-vpn-helper`
über `pkexec` auf:

| Rechner | polkit |
|---|---|
| eigener Rechner | Administrator-Passwort (`auth_admin_keep`, einige Minuten gemerkt). `status` ohne weitere Argumente geht ohne Passwort (ab polkit 0.106, nicht unter Ubuntu 22.04/Mint 21) |
| Agent-PC (Paket `bonys-vpn-agentpc`) | Mitglieder der Gruppe `bonys-vpn` ohne Passwort |

Als root ruft `bonys-vpn` den Helfer direkt auf. Die Umgebungsvariable `BONYS_VPN_HELPER` setzt einen anderen
Pfad (nur für Tests).

## Version und Schema prüfen

```console
$ bonys-vpn version --json
{"schema": 1, "version": "1.0.0", "helper": "/usr/sbin/bonys-vpn-helper"}
```

| Feld | Bedeutung |
|---|---|
| `schema` | Schema-Version dieser Schnittstelle |
| `version` | Version von Bony's VPN |
| `helper` | Pfad des Helfers, `null`, wenn er fehlt |

`bonys-vpn --version` gibt `bonys-vpn 1.0.0` aus. Alternativ: `dpkg-query -W -f='${Version}' bonys-vpn`.

## Befehle

### `bonys-vpn status [NAME] [--json]`

Alle Tunnel (oder nur `NAME`) mit Status, Live-Werten und Kill-Switch.

```json
{
  "schema": 1,
  "version": "1.0.0",
  "active": "fritz",
  "tunnels": [
    {
      "name": "fritz",
      "active": true,
      "autostart": true,
      "managed": true,
      "addresses": ["10.8.0.2/24"],
      "dns": ["10.8.0.1"],
      "mtu": null,
      "peers": [{"public_key": "…", "allowed_ips": ["0.0.0.0/0"], "endpoint": "vpn.example.net:51820"}],
      "hooks": [],
      "handshake": 1700000000,
      "rx": 2048,
      "tx": 1024,
      "peers_live": [{"public_key": "…", "endpoint": "198.51.100.7:51820", "handshake": 1700000000, "rx": 2048, "tx": 1024}]
    }
  ],
  "killswitch": {"enabled": true, "active": true, "exceptions": ["10.0.2.0/24"]}
}
```

| Feld | Bedeutung |
|---|---|
| `active` | Name des verbundenen Tunnels oder `null` |
| `tunnels` | Liste der Tunnel in `/etc/wireguard`, nach Namen sortiert |
| `name` | Tunnelname (`[a-zA-Z0-9_=+.-]{1,15}`) |
| `active` | verbunden? (im Tunnel-Objekt) |
| `autostart` | verbindet sich beim Hochfahren (`wg-quick@NAME`) |
| `managed` | von Bony's VPN angelegt? Nur solche lassen sich umbenennen, exportieren, löschen |
| `addresses`, `dns`, `mtu` | aus `[Interface]` |
| `peers` | je Peer `public_key`, `allowed_ips`, `endpoint` (`host:port` oder `null`) |
| `hooks` | Namen vorhandener Hook-Zeilen (`PreUp`, `PostUp`, …) – laufen beim Verbinden als root |
| `handshake` | letzter Handshake (Unix-Zeit) oder `null` – nur bei verbundenen Tunneln |
| `rx`, `tx` | empfangene/gesendete Bytes – nur bei verbundenen Tunneln |
| `peers_live` | Live-Werte je Peer – nur bei verbundenen Tunneln |
| `error` | nur, wenn die Konfiguration fehlerhaft ist (dann fehlen die übrigen Felder) |
| `killswitch` | `enabled` (eingeschaltet), `active` (Regeln geladen), `exceptions` (Netze, die immer erreichbar bleiben) |

### `bonys-vpn list [--json]`

```json
{"schema": 1, "version": "1.0.0", "tunnels": [{"name": "fritz", "active": false, "autostart": false, "managed": true}]}
```

### `bonys-vpn up NAME [--json]` / `bonys-vpn down [NAME] [--json]`

`up` verbindet und trennt vorher einen anderen aktiven Tunnel von Bony's VPN (immer nur einer). `down` ohne
Namen trennt alle Tunnel von Bony's VPN. Antwort mit `--json`: `{"schema": 1, "version": …, "ok": true}`.

### `bonys-vpn import DATEI … [--name NAME] [--ersetzen] [--hooks-erlauben] [--json]`

Importiert `.conf`-Dateien oder ZIP-Archive mit `.conf`-Dateien. `-` liest **eine** Konfiguration von der
Standardeingabe, dann ist `--name` Pflicht. Die Kommandozeile liest die Datei als Benutzer und reicht den
Inhalt über die Standardeingabe an den Helfer weiter. Der Helfer bekommt nie einen Pfad.

- `--ersetzen`: vorhandenen (eigenen) Tunnel gleichen Namens ersetzen
- `--hooks-erlauben`: Konfigurationen mit `PreUp`/`PostUp`/`PreDown`/`PostDown` zulassen (laufen als root!)

```json
{"schema": 1, "version": "1.0.0", "imported": [{"name": "fritz", "ok": true}, {"name": "kaputt", "ok": false, "error": "Zeile 3: …"}]}
```

`imported` listet jede Konfiguration mit `ok` und gegebenenfalls `error`. Schlägt eine fehl, ist der
Rückgabewert `1`, und das Objekt enthält zusätzlich `error` (Zusammenfassung). Auch dann gibt es nur ein
JSON-Objekt.

### `bonys-vpn delete NAME --ja [--json]` / `bonys-vpn rename ALT NEU [--json]`

Nur für Tunnel, die Bony's VPN angelegt hat. Ohne `--ja` fragt `delete` nach (nur im Terminal). Antwort `ok`.

### `bonys-vpn export NAME [DATEI]`

Schreibt die Konfiguration **mit privatem Schlüssel** nach `DATEI` (Standard: `NAME.conf`), Rechte `600`,
überschreibt nichts. Kein `--json`.

### `bonys-vpn autostart NAME an|aus [--json]`

`on`/`off` gehen auch. Immer nur ein Tunnel verbindet sich automatisch.

### `bonys-vpn killswitch an|aus|status [--ausnahme NETZ …] [--keine-ausnahmen] [--json]`

`on`/`off` gehen auch. `--ausnahme` (mehrfach) ersetzt die Liste der Netze, die ohne Tunnel erreichbar
bleiben. `--keine-ausnahmen` leert sie. Ohne beides bleibt die Liste, wie sie ist. Antwort: das
`killswitch`-Objekt (`enabled`, `active`, `exceptions`) mit `schema` und `version`.

### `bonys-vpn version [--json]`

Siehe oben.

## Root-Schnittstelle: `bonys-vpn-helper`

Bony's Agents ruft im Agent-PC über den QEMU-Gast-Agent als root direkt den Helfer auf. Es gelten dieselben
Unterbefehle ohne `--json` (der Helfer antwortet immer mit JSON, mit `schema` und `version`):

```text
bonys-vpn-helper status [NAME] | list | show NAME
bonys-vpn-helper up NAME | down [NAME]
bonys-vpn-helper import NAME [--allow-hooks] [--replace]   < KONFIGURATION
bonys-vpn-helper delete NAME | rename ALT NEU | export NAME
bonys-vpn-helper autostart NAME on|off
bonys-vpn-helper killswitch on [--exception NETZ … | --no-exceptions] | off | status
```

`show` gibt `{"name", "managed", "conf"}` zurück, `conf` mit `(verborgen)` statt Schlüsseln. `export` gibt die
Konfiguration als Text aus (nicht JSON). Fehler: Rückgabewert `1`, Meldung auf der Standardfehlerausgabe mit
`Fehler: …`.

## Pakete und Pfade

| Pfad | Inhalt |
|---|---|
| `/usr/bin/bonys-vpn` | Kommandozeile |
| `/usr/bin/bonys-vpn-gui` | Oberfläche (`--tray`: nur Symbol in der Leiste) |
| `/usr/sbin/bonys-vpn-helper` | Root-Helfer |
| `/usr/lib/bonys-vpn/bonys_vpn/` | Python-Paket (System-Python, nur Standardbibliothek und python3-gi) |
| `/etc/wireguard/NAME.conf` | Tunnel (600, root) |
| `/etc/bonys-vpn/killswitch.json` | Kill-Switch an/aus, Ausnahmen (keine Schlüssel) |
| `/usr/share/bonys-vpn/agentpc` | nur im Agent-PC (Paket `bonys-vpn-agentpc`) |

Bis Bony's Agents 0.14 lag Bony's VPN im Agent-PC unter `/usr/local`. Das Paket `bonys-vpn` räumt diese alte
Installation bei der Installation auf. Tunnel und Kill-Switch-Zustand bleiben, die Kommandozeile findet
übergangsweise auch noch den alten Helfer `/usr/local/sbin/bonys-vpn-helper`.

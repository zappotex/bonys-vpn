# SPDX-License-Identifier: GPL-3.0-or-later
"""Bereich „Bony-Netz“ – Logik ohne GTK (Oberfläche: ``netz_gui.py``).

Mit der Zentrale spricht der gemeinsame Baustein ``bony_netz`` (derselbe wie in Bony's Agents, unverändert
übernommen). Die Anmeldung liegt in seiner Datei ``konto.json``, die sich beide Programme teilen: Eine in Bony's
Agents angelegte Instanz gilt hier sofort. Deshalb wird die Datei vor jeder Änderung frisch geladen.

Diesen Rechner verbindet Tailscale mit dem Netz der Instanz. Root-Rechte bekommt dafür nur der Helfer
(``bonys-vpn-helper netz up|down|join|start``); den Zustand liest die Oberfläche selbst mit
``tailscale status --json`` (geht ohne root). Der Geräteschlüssel geht nur über die Standardeingabe an den Helfer.

Alle Funktionen, die die Zentrale fragen, blockieren bis zu 15 Sekunden: nur im Hintergrund aufrufen.
Das Instanz-Token und der Geräteschlüssel erscheinen nirgends – weder in Texten noch in Fehlermeldungen.
"""

from __future__ import annotations

import datetime
import json
import re
import shlex
import shutil
import socket
import subprocess
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from bony_netz import Konto, NetzFehler, Zentrale, ZentraleFehler
from bony_netz.agentenpc import geraetename
from bony_netz.konto import server_normalisieren

from . import model
from .cli import CliError
from .model import tr

SYSTEM_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
ABFRAGE_SEKUNDEN = 30          # Geräteliste und Zustand höchstens so oft, solange der Bereich sichtbar ist
REKORD = "vpn_rechner"         # konto.extra: {instanz_id, name} – mit welchem Namen Bony's VPN diesen Rechner verband
# Netz der Agenten-PCs (Headscale vergibt Adressen aus 100.64.0.0/10) – Ausnahme für den Kill-Switch
TAILNET = "100.64.0.0/10"

# Offizielle Quellen von Tailscale
PAKETQUELLE = "https://pkgs.tailscale.com/stable"
WINDOWS_INSTALLER = "https://pkgs.tailscale.com/stable/tailscale-setup-latest.exe"
DOWNLOAD_MAC = "https://tailscale.com/download/mac"
DOWNLOAD = "https://tailscale.com/download"

# Zugehörigkeit des Tailscale-Kontos auf diesem Rechner
EIGEN = "eigen"          # mit dieser Instanz verbunden (bzw. zuletzt gewesen)
FREMD = "fremd"          # anderes Konto oder andere Instanz angemeldet
ENTFERNT = "entfernt"    # war mit dieser Instanz verbunden, wurde auf dem Server aber entfernt
KEINS = "keins"          # nichts angemeldet

EN = {
    "Bony-Netz": "Bony-Netz",
    "Keine Verbindung zum Server": "No connection to the server",
    "Anmeldung ungültig, bitte Instanz neu anlegen": "Sign-in invalid, please create a new instance",
    "Verbunden mit {server}": "Connected to {server}",
    "Unter {server} antwortet kein Bony-Netz-Server ({grund}). Bitte die Adresse prüfen.":
        "No Bony-Netz server answers at {server} ({grund}). Please check the address.",
    "unerwartete Antwort": "unexpected response",
    "Fehler {code}": "error {code}",
    "Die Anmeldung ließ sich nicht speichern ({fehler}).": "The sign-in could not be saved ({fehler}).",
    "Der Server wurde inzwischen geändert – bitte den Bereich neu laden.":
        "The server has been changed in the meantime – please reload this section.",
    "Es ist keine Instanz angemeldet.": "No instance is signed in.",
    "Tailscale ist nicht installiert.": "Tailscale is not installed.",
    "Der Tailscale-Dienst läuft nicht.": "The Tailscale service is not running.",
    "Verbunden als {name} · {ip}": "Connected as {name} · {ip}",
    "Getrennt – Tailscale ist ausgeschaltet.": "Disconnected – Tailscale is turned off.",
    "Verbindet …": "Connecting …",
    "Mit einem anderen Tailscale-Konto verbunden ({netz}).": "Connected to another Tailscale account ({netz}).",
    "Ein anderes Tailscale-Konto ist angemeldet ({netz}).": "Another Tailscale account is signed in ({netz}).",
    "Dieser Rechner wurde aus dem Netz entfernt.": "This computer was removed from the network.",
    "Nicht mit dem Bony-Netz verbunden.": "Not connected to the Bony-Netz.",
    "online": "online",
    "offline": "offline",
    "zuletzt gesehen {wann}": "last seen {wann}",
    "dieser Rechner": "this computer",
    "Tailscale gibt es aus der offiziellen Paketquelle nur für Debian, Ubuntu und davon abgeleitete Systeme. "
    "Bitte von {url} installieren.":
        "Tailscale's official package repository only covers Debian, Ubuntu and derived systems. Please install "
        "it from {url}.",
    "Die Gerätegrenze ist erreicht.": "The device limit has been reached.",
}
model.EN.update(EN)


class KeinBonyNetz(Exception):
    """Unter der Adresse antwortet etwas, aber kein Bony-Netz-Server (z. B. eine normale Webseite)."""


class Rueckfrage(Exception):
    """Ein anderes Tailscale-Konto ist angemeldet – erst nach Rückfrage ersetzen (``verbinden(ersetzen=True)``)."""

    def __init__(self, netz: str):
        super().__init__(netz)
        self.netz = netz


FEHLER = (NetzFehler, ZentraleFehler, KeinBonyNetz, CliError, OSError, RuntimeError)


def fehlertext(e: BaseException) -> str:
    if isinstance(e, ZentraleFehler):
        return e.meldung
    if isinstance(e, OSError) and not isinstance(e, NetzFehler):
        return tr("Die Anmeldung ließ sich nicht speichern ({fehler}).", fehler=e.strerror or e)
    return str(e)


def kurz_id(instanz_id: str) -> str:
    return instanz_id[:8]


# ---------------------------------------------------------------- Server und Instanz
def aendern(fn: Callable[[Konto], object]) -> Konto:
    """Konto frisch laden, ändern, speichern (OSError, wenn das nicht geht) – so bleiben Änderungen aus
    Bony's Agents erhalten."""
    konto = Konto.laden()
    fn(konto)
    konto.speichern()
    return konto


def _kein_server(server: str, grund: str) -> KeinBonyNetz:
    return KeinBonyNetz(tr("Unter {server} antwortet kein Bony-Netz-Server ({grund}). Bitte die Adresse prüfen.",
                           server=server, grund=grund))


def _fragen(server: str, fn: Callable[[], object]):
    """Aufruf der Zentrale. Antwortet dort etwas anderes (Webseite statt JSON, 404), gibt es KeinBonyNetz –
    der Baustein meldet eine Antwort, die kein JSON ist, nicht selbst als Fehler."""
    try:
        return fn()
    except ZentraleFehler as e:
        if e.status in (404, 405):
            raise _kein_server(server, tr("Fehler {code}", code=e.status)) from None
        raise
    except (ValueError, KeyError, TypeError):
        raise _kein_server(server, tr("unerwartete Antwort")) from None


def _abmelden_wenn_ungueltig(konto: Konto, e: ZentraleFehler) -> None:
    """Instanz auf dem Server gelöscht: nur diese Anmeldung verwerfen (Bony's Agents könnte schon eine neue haben)."""
    if e.anmeldung_ungueltig:
        alt = konto.instanz_id
        try:
            aendern(lambda k: k.abmelden() if k.instanz_id == alt else None)
        except OSError:
            pass


def server_info(server: str) -> dict:
    """``/info`` des Servers – mit Prüfung, dass es wirklich eine Zentrale ist."""
    server = server_normalisieren(server)
    info = _fragen(server, lambda: Zentrale(server).info())
    if not isinstance(info, dict) or not isinstance(info.get("server_name"), str):
        raise _kein_server(server, tr("unerwartete Antwort"))
    return info


@dataclass
class Zustand:
    text: str                       # Zustandszeile
    konto: Konto                    # frisch geladen (evtl. mit aktualisiertem Kursleiter-Status oder abgemeldet)
    info: dict = field(default_factory=dict)   # Antwort von /info, leer ohne Verbindung
    verbunden: bool = False
    detail: str = ""


def zustand() -> Zustand:
    """Server fragen und – falls angemeldet – die Instanz. Wirft nicht: Fehler stehen im Ergebnis."""
    konto = Konto.laden()
    try:
        info = server_info(konto.server)
        if konto.angemeldet:
            instanz = _fragen(konto.server, lambda: Zentrale(konto.server, konto.token).instanz())
            if not isinstance(instanz, dict):
                raise _kein_server(konto.server, tr("unerwartete Antwort"))
            kursleiter = bool(instanz.get("ist_kursleiter"))
            if kursleiter != konto.ist_kursleiter:
                alt = konto.instanz_id

                def merken(k: Konto) -> None:
                    if k.instanz_id == alt:
                        k.ist_kursleiter = kursleiter

                try:
                    konto = aendern(merken)
                except OSError:
                    konto.ist_kursleiter = kursleiter
        return Zustand(tr("Verbunden mit {server}", server=info["server_name"]), konto, info, verbunden=True)
    except NetzFehler as e:
        return Zustand(tr("Keine Verbindung zum Server"), konto, detail=str(e))
    except ZentraleFehler as e:
        if not e.anmeldung_ungueltig:
            return Zustand(e.meldung, konto, detail=e.meldung)
        _abmelden_wenn_ungueltig(konto, e)
        return Zustand(tr("Anmeldung ungültig, bitte Instanz neu anlegen"), Konto.laden(), detail=e.meldung)
    except KeinBonyNetz as e:
        return Zustand(str(e), konto, detail=str(e))


def server_wechseln(adresse: str) -> Zustand:
    """Neuen Server prüfen und erst dann übernehmen (eine Anmeldung beim alten wird dabei verworfen)."""
    adresse = server_normalisieren(adresse)
    server_info(adresse)
    aendern(lambda k: k.server_wechseln(adresse))
    return zustand()


def instanz_anlegen(server: str) -> Konto:
    """Neue Instanz anlegen und sofort speichern. Hat Bony's Agents inzwischen eine angelegt, gilt diese."""
    konto = Konto.laden()
    if konto.server != server_normalisieren(server):
        raise RuntimeError(tr("Der Server wurde inzwischen geändert – bitte den Bereich neu laden."))
    if konto.angemeldet:
        return konto
    registrierung = _fragen(konto.server, lambda: Zentrale(konto.server).instanz_registrieren())
    konto.uebernehmen(registrierung)
    konto.speichern()
    return konto


def instanz_loeschen() -> Konto:
    """Instanz auf dem Server löschen (mit allen Geräten, Kursen und Teilnahmen), dann abmelden."""
    konto = Konto.laden()
    if konto.angemeldet:
        try:
            _fragen(konto.server, lambda: Zentrale(konto.server, konto.token).instanz_loeschen())
        except ZentraleFehler as e:
            if not e.anmeldung_ungueltig:
                raise
    alt = konto.instanz_id
    return aendern(lambda k: k.abmelden() if k.instanz_id == alt else None)


def _zentrale() -> tuple[Konto, Zentrale]:
    konto = Konto.laden()
    if not konto.angemeldet:
        raise RuntimeError(tr("Es ist keine Instanz angemeldet."))
    return konto, Zentrale(konto.server, konto.token)


def _angemeldet_fragen(fn: Callable[[Konto, Zentrale], object]):
    konto, zentrale = _zentrale()
    try:
        return _fragen(konto.server, lambda: fn(konto, zentrale))
    except ZentraleFehler as e:
        _abmelden_wenn_ungueltig(konto, e)
        raise


def kursleiter_freischalten(code: str) -> Konto:
    """Kursleiter-Code einlösen (``400`` mit Meldung, wenn ungültig) und ``ist_kursleiter`` speichern."""
    code = code.strip()
    alt = _angemeldet_fragen(lambda k, z: (z.kursleiter_freischalten(code), k.instanz_id)[1])

    def merken(k: Konto) -> None:
        if k.instanz_id == alt:
            k.ist_kursleiter = True

    return aendern(merken)


def geraete() -> list[dict]:
    """Geräte der Instanz, nach Name sortiert. Wirft NetzFehler, ZentraleFehler, KeinBonyNetz oder RuntimeError."""
    liste = _angemeldet_fragen(lambda k, z: z.geraete())
    if not isinstance(liste, list):
        raise _kein_server(Konto.laden().server, tr("unerwartete Antwort"))
    return sorted((g for g in liste if isinstance(g, dict) and isinstance(g.get("name"), str)),
                  key=lambda g: g["name"])


def geraet_entfernen(geraet_id: str) -> None:
    try:
        _angemeldet_fragen(lambda k, z: z.geraet_entfernen(str(geraet_id)))
    except KeinBonyNetz:
        pass   # 404: schon weg


def schluessel() -> tuple[str, str]:
    """(login_server, Geräteschlüssel) – einmal nutzbar, 60 Minuten gültig: erst direkt vor dem Verbinden holen."""
    antwort = _angemeldet_fragen(lambda k, z: z.geraeteschluessel())
    konto = Konto.laden()
    try:
        login_server = antwort.get("login_server") or konto.login_server
        key = antwort["schluessel"]
        if not isinstance(login_server, str) or not login_server.startswith("https://") or not isinstance(key, str):
            raise TypeError
    except (AttributeError, KeyError, TypeError):
        raise _kein_server(konto.server, tr("unerwartete Antwort")) from None
    return login_server, key


# ---------------------------------------------------------------- Geräteliste
def zuletzt(wert, now: float | None = None) -> str:
    """„vor 3 min“ aus einem Zeitstempel (Sekunden) oder ISO-Text, sonst „–“."""
    if isinstance(wert, bool) or wert in (None, "", 0):
        return "–"
    ts = None
    if isinstance(wert, (int, float)):
        ts = float(wert)
    elif isinstance(wert, str):
        try:
            ts = datetime.datetime.fromisoformat(wert.strip().replace("Z", "+00:00")).timestamp()
        except ValueError:
            return wert
    return model.fmt_age(int(ts), now) if ts else "–"


@dataclass
class Geraet:
    geraet_id: str
    name: str
    ip: str
    online: bool
    zuletzt_gesehen: object = None
    dieser_rechner: bool = False

    @classmethod
    def aus(cls, d: dict, eigene_ips: set[str] | frozenset = frozenset()) -> Geraet:
        ip = str(d.get("ip") or "")
        return cls(str(d.get("geraet_id", "")), str(d.get("name", "")), ip, bool(d.get("online")),
                   d.get("zuletzt_gesehen"), bool(ip) and ip in eigene_ips)

    def zeile(self, now: float | None = None) -> str:
        teile = [self.ip or "–", tr("online") if self.online else tr("offline")]
        if not self.online:
            teile.append(tr("zuletzt gesehen {wann}", wann=zuletzt(self.zuletzt_gesehen, now)))
        return " · ".join(teile)


# ---------------------------------------------------------------- Tailscale auf diesem Rechner
@dataclass
class Tailscale:
    installiert: bool
    dienst: bool = False            # tailscaled antwortet
    zustand: str = ""               # BackendState: NoState, NeedsLogin, NeedsMachineAuth, Stopped, Starting, Running
    name: str = ""                  # Self.HostName
    ips: list[str] = field(default_factory=list)
    netz: str = ""                  # CurrentTailnet.Name (bei Headscale der Name des Login-Servers)

    @property
    def verbunden(self) -> bool:
        return self.zustand == "Running"

    @property
    def angemeldet(self) -> bool:
        """Ist ein Konto eingerichtet (verbunden oder nur ausgeschaltet)?"""
        return self.zustand in ("Running", "Stopped", "Starting")


def tailscale_status(run=subprocess.run, which=shutil.which) -> Tailscale:
    """Zustand von Tailscale aus ``tailscale status --json`` – ohne root (der Socket ist für alle lesbar)."""
    exe = which("tailscale", path=SYSTEM_PATH)
    if not exe:
        return Tailscale(False)
    try:
        proc = run([exe, "status", "--json"], capture_output=True, text=True, timeout=10,
                   env={"PATH": SYSTEM_PATH, "LC_ALL": "C.UTF-8"}, stdin=subprocess.DEVNULL)
        data = json.loads(proc.stdout or "")
    except (OSError, subprocess.SubprocessError, ValueError):
        return Tailscale(True)      # Dienst läuft nicht (keine JSON-Antwort)
    if not isinstance(data, dict):
        return Tailscale(True)
    selbst = data.get("Self") if isinstance(data.get("Self"), dict) else {}
    netz = data.get("CurrentTailnet") if isinstance(data.get("CurrentTailnet"), dict) else {}
    ips = selbst.get("TailscaleIPs") or data.get("TailscaleIPs") or []
    return Tailscale(True, True, str(data.get("BackendState") or ""), str(selbst.get("HostName") or ""),
                     [str(i) for i in ips if isinstance(i, str)], str(netz.get("Name") or ""))


def rechnername(hostname: str | None = None) -> str:
    """Name dieses Rechners im Netz: ``geraetename("rechner-" + Rechnername)``."""
    return geraetename("rechner-" + (hostname if hostname is not None else socket.gethostname()))


def _host(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower()


def zugehoerigkeit(ts: Tailscale, konto: Konto, liste: list[dict] | None) -> str:
    """Mit welchem Konto ist Tailscale hier angemeldet? Sicher erkennbar ist die eigene Instanz an der Adresse in
    der Geräteliste. Ohne Liste (Server nicht erreichbar) oder ohne Adresse (ausgeschaltet) zählt, ob Bony's VPN
    diesen Rechner selbst mit dieser Instanz und diesem Namen verbunden hat."""
    if not ts.angemeldet:
        return KEINS
    ips = set(ts.ips)
    if liste is not None and ips & {str(g.get("ip")) for g in liste}:
        return EIGEN
    rekord = konto.extra.get(REKORD)
    selbst = (isinstance(rekord, dict) and konto.angemeldet and rekord.get("instanz_id") == konto.instanz_id
              and rekord.get("name") == ts.name and bool(ts.name)
              and (not ts.netz or not konto.login_server or ts.netz.lower() == _host(konto.login_server)))
    if not selbst:
        return FREMD
    return ENTFERNT if liste is not None and ips else EIGEN


def anzeige(ts: Tailscale | None, zug: str) -> tuple[str, str]:
    """(Text, Stil) für „Diesen Rechner verbinden“. Stil: ok, off, warn."""
    if ts is None:
        return "", "off"
    if not ts.installiert:
        return tr("Tailscale ist nicht installiert."), "off"
    if not ts.dienst:
        return tr("Der Tailscale-Dienst läuft nicht."), "off"
    if zug == EIGEN:
        if ts.verbunden:
            return tr("Verbunden als {name} · {ip}", name=ts.name, ip=", ".join(ts.ips[:1]) or "–"), "ok"
        if ts.zustand == "Starting":
            return tr("Verbindet …"), "warn"
        return tr("Getrennt – Tailscale ist ausgeschaltet."), "off"
    if zug == FREMD:
        netz = ts.netz or "Tailscale"
        return (tr("Mit einem anderen Tailscale-Konto verbunden ({netz}).", netz=netz) if ts.verbunden
                else tr("Ein anderes Tailscale-Konto ist angemeldet ({netz}).", netz=netz)), "warn"
    if zug == ENTFERNT:
        return tr("Dieser Rechner wurde aus dem Netz entfernt."), "warn"
    return tr("Nicht mit dem Bony-Netz verbunden."), "off"


Helfer = Callable[..., str]   # wie HelperClient.run(*args, data=…) – Fehler → CliError


def verbinden(helfer: Helfer, ersetzen: bool = False, status=tailscale_status,
              hostname: str | None = None) -> None:
    """„Diesen Rechner verbinden“ einschalten.

    - schon mit dieser Instanz verbunden gewesen → bloßes ``tailscale up`` (kein neuer Schlüssel);
      klappt das nicht, weil der Rechner auf dem Server entfernt wurde, ein neuer Schlüssel
    - nichts angemeldet → Schlüssel holen, ``netz join``
    - anderes Konto angemeldet → ``Rueckfrage``; mit ``ersetzen=True`` ``netz join --replace``
      (neues Tailscale-Profil, das andere bleibt als Profil erhalten)

    Wirft Rueckfrage, CliError (Helfer), NetzFehler/ZentraleFehler/KeinBonyNetz (Zentrale) oder RuntimeError.
    """
    konto = Konto.laden()
    if not konto.angemeldet:
        raise RuntimeError(tr("Es ist keine Instanz angemeldet."))
    ts = status()
    if not ts.installiert:
        raise RuntimeError(tr("Tailscale ist nicht installiert."))
    if not ts.dienst:
        helfer("netz", "start")
        ts = status()
    try:
        liste: list[dict] | None = geraete()
        listenfehler: BaseException | None = None
    except FEHLER as e:
        liste, listenfehler = None, e
    zug = zugehoerigkeit(ts, konto, liste)
    if zug == EIGEN:
        if ts.verbunden:
            return
        try:
            helfer("netz", "up")
            return
        except CliError:
            ts = status()
            if ts.angemeldet and ts.zustand != "NeedsLogin":
                raise
            zug = KEINS   # auf dem Server entfernt: Tailscale wartet auf eine neue Anmeldung
    if zug == FREMD and not ersetzen:
        raise Rueckfrage(ts.netz or "Tailscale")
    if listenfehler is not None:
        raise listenfehler   # ohne Zentrale kein Schlüssel
    login_server, key = schluessel()
    name = rechnername(hostname)
    args = ["netz", "join", "--login-server", login_server, "--hostname", name]
    if zug in (FREMD, ENTFERNT):
        args.append("--replace")
    helfer(*args, data=key.encode("ascii", errors="replace"))
    alt = konto.instanz_id

    def merken(k: Konto) -> None:
        if k.instanz_id == alt:
            k.extra[REKORD] = {"instanz_id": alt, "name": name}

    aendern(merken)


def trennen(helfer: Helfer) -> None:
    """Ausschalten (``tailscale down``) – kein Abmelden; beim Einschalten reicht dann ``tailscale up``."""
    helfer("netz", "down")


# ---------------------------------------------------------------- Tailscale installieren (offizielle Quellen)
def paketquelle(os_release: str) -> tuple[str, str] | None:
    """(Distribution, Codename) für ``pkgs.tailscale.com`` – wie dessen install.sh: Linux Mint und andere
    Ubuntu-Abkömmlinge nehmen das Ubuntu-Paket (UBUNTU_CODENAME), Debian-Abkömmlinge das Debian-Paket."""
    werte = {}
    for zeile in os_release.splitlines():
        k, sep, v = zeile.partition("=")
        if sep:
            werte[k.strip()] = v.strip().strip('"').strip("'")
    os_id, aehnlich = werte.get("ID", "").lower(), werte.get("ID_LIKE", "").lower().split()
    if os_id in ("debian", "ubuntu", "raspbian"):
        wahl = (os_id, werte.get("VERSION_CODENAME", ""))
    elif werte.get("UBUNTU_CODENAME"):
        wahl = ("ubuntu", werte["UBUNTU_CODENAME"])
    elif "debian" in aehnlich and werte.get("DEBIAN_CODENAME"):
        wahl = ("debian", werte["DEBIAN_CODENAME"])
    else:
        return None
    return wahl if re.fullmatch(r"[a-z]+", wahl[1].lower()) else None


def installations_skript(distribution: str, codename: str) -> str:
    """Shell-Skript (als root, über ``updates.run_as_root``): Paketquelle von ``pkgs.tailscale.com`` wie im
    Beitrittsskript des Bausteins, dann ``apt-get install tailscale`` und den Dienst starten."""
    if not (re.fullmatch(r"[a-z]+", distribution) and re.fullmatch(r"[a-z]+", codename)):
        raise ValueError("ungültige Distribution")
    basis = f"{PAKETQUELLE}/{distribution}/{codename}"
    return "\n".join([
        "set -e",
        "export DEBIAN_FRONTEND=noninteractive",
        "command -v curl >/dev/null || { apt-get update -q; apt-get install -y -q curl ca-certificates; }",
        "install -m 0755 -d /usr/share/keyrings",
        f"curl -fsSL {shlex.quote(basis + '.noarmor.gpg')} -o /usr/share/keyrings/tailscale-archive-keyring.gpg",
        "chmod 644 /usr/share/keyrings/tailscale-archive-keyring.gpg",
        f"curl -fsSL {shlex.quote(basis + '.tailscale-keyring.list')} -o /etc/apt/sources.list.d/tailscale.list",
        "chmod 644 /etc/apt/sources.list.d/tailscale.list",
        'apt-get update -q || echo "Warnung: Nicht alle Paketquellen ließen sich aktualisieren."',
        "apt-get install -y -q tailscale",
        "systemctl enable --now tailscaled",
        "tailscale version | head -n1",
        "",
    ])


def windows_installer_laden(ordner: Path, opener=urllib.request.urlopen, limit: int = 300 * 1024 * 1024) -> Path:
    """Windows: offizielles Installationsprogramm von ``pkgs.tailscale.com/stable/`` laden (die Signatur des
    Herstellers zeigt Windows beim Start). Gibt den Pfad zurück; Fehler → OSError."""
    req = urllib.request.Request(WINDOWS_INSTALLER, headers={"User-Agent": "bonys-vpn"})
    with opener(req, timeout=60) as resp:
        ziel = resp.geturl()
        name = ziel.rsplit("/", 1)[-1]
        if (not ziel.startswith("https://") or _host(ziel) != "pkgs.tailscale.com"
                or not re.fullmatch(r"tailscale-setup-[0-9][0-9.]*\.exe", name)):
            raise OSError(f"unerwartete Download-Adresse: {ziel}")
        pfad = Path(ordner) / name
        groesse = 0
        with open(pfad, "wb") as f:
            while chunk := resp.read(1024 * 1024):
                groesse += len(chunk)
                if groesse > limit:
                    raise OSError("Download zu groß")
                f.write(chunk)
    return pfad


def os_release() -> str:
    for pfad in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            with open(pfad, encoding="utf-8") as f:
                return f.read()
        except OSError:
            continue
    return ""

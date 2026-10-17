# SPDX-License-Identifier: GPL-3.0-or-later
"""Bereich „Bony-Netz“ (bonys_vpn.netz) gegen eine kleine Test-Zentrale auf 127.0.0.1 – nie gegen den echten
Server – und mit einem nachgebauten Tailscale (Status, Helfer)."""

import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bony_netz import Konto, NetzFehler, ZentraleFehler
from bonys_vpn import model, netz
from bonys_vpn.cli import CliError


@pytest.fixture(autouse=True)
def german():
    model.set_language("de")
    yield
    model.set_language(None)


class FakeZentrale:
    """Antwortet wie die Zentrale (API.md)."""

    def __init__(self):
        self.instanzen: dict[str, dict] = {}   # token -> {instanz_id, ist_kursleiter}
        self.anfragen: list[str] = []
        self.geraete: list[dict] = []
        self.schluessel: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code, data=None):
                body = json.dumps(data).encode() if data is not None else b""
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _route(self):
                outer.anfragen.append(f"{self.command} {self.path}")
                token = self.headers.get("Authorization", "").removeprefix("Bearer ")
                key = (self.command, self.path)
                if key == ("GET", "/api/v1/info"):
                    return self._send(200, {"server_name": "Test-Zentrale", "version": "1.2.3",
                                            "login_server": "https://vpn.test", "kursraum_url": "wss://k.test"})
                if key == ("POST", "/api/v1/instanzen"):
                    n = len(outer.instanzen) + 1
                    data = {"instanz_id": f"inst{n:04d}abcdefgh", "token": f"bz_geheim{n}",
                            "login_server": "https://vpn.test"}
                    outer.instanzen[data["token"]] = {"instanz_id": data["instanz_id"], "ist_kursleiter": False}
                    return self._send(201, data)
                instanz = outer.instanzen.get(token)
                if instanz is None:
                    return self._send(401, {"detail": "Diese Instanz ist nicht (mehr) angemeldet."})
                if key == ("GET", "/api/v1/instanz"):
                    return self._send(200, {**instanz, "erstellt": 1760000000})
                if key == ("POST", "/api/v1/instanz/kursleiter"):
                    length = int(self.headers.get("Content-Length") or 0)
                    if json.loads(self.rfile.read(length)).get("code") != "KL-RICHTIG":
                        return self._send(400, {"detail": "Der Kursleiter-Code ist ungültig."})
                    instanz["ist_kursleiter"] = True
                    return self._send(200, {"ist_kursleiter": True})
                if key == ("GET", "/api/v1/geraete"):
                    return self._send(200, outer.geraete)
                if key == ("POST", "/api/v1/geraete/schluessel"):
                    outer.schluessel.append(f"hskey-auth-{len(outer.schluessel) + 1:04d}geheim")
                    return self._send(200, {"schluessel": outer.schluessel[-1], "login_server": "https://vpn.test",
                                            "gueltig_minuten": 60})
                if self.command == "DELETE" and self.path.startswith("/api/v1/geraete/"):
                    gid = self.path.rsplit("/", 1)[1]
                    if not any(g["geraet_id"] == gid for g in outer.geraete):
                        return self._send(404, {"detail": "Gerät nicht gefunden."})
                    outer.geraete = [g for g in outer.geraete if g["geraet_id"] != gid]
                    return self._send(204)
                return self._send(404, {"detail": "Nicht gefunden"})

            do_GET = do_POST = do_DELETE = _route

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def zentrale():
    z = FakeZentrale()
    yield z
    z.close()


def geraet(gid, name, ip, online=True):
    return {"geraet_id": gid, "name": name, "ip": ip, "online": online, "zuletzt_gesehen": 1760000000}


def angemeldet(zentrale) -> Konto:
    """Instanz wie aus Bony's Agents: direkt in konto.json, ohne dass Bony's VPN sie anlegt."""
    netz.server_wechseln(zentrale.url)
    return netz.instanz_anlegen(zentrale.url)


# ---------------------------------------------------------------- Server und Instanz
def test_instanz_aus_bonys_agents_wird_uebernommen(zentrale):
    k = Konto(server=zentrale.url)
    k.uebernehmen({"instanz_id": "inst0001abcdefgh", "token": "bz_agents", "login_server": "https://vpn.test"})
    k.extra["aktiv"] = True   # Schalter von Bony's Agents
    k.speichern()
    zentrale.instanzen["bz_agents"] = {"instanz_id": "inst0001abcdefgh", "ist_kursleiter": False}
    z = netz.zustand()
    assert z.verbunden and z.text == "Verbunden mit Test-Zentrale"
    assert z.konto.instanz_id == "inst0001abcdefgh"
    assert "POST /api/v1/instanzen" not in zentrale.anfragen     # ohne Rückfrage und ohne neue Instanz
    assert netz.instanz_anlegen(zentrale.url).instanz_id == "inst0001abcdefgh"
    assert "POST /api/v1/instanzen" not in zentrale.anfragen
    assert "bz_" not in z.text + z.detail


def test_ohne_server():
    k = Konto(server="http://127.0.0.1:9")
    k.uebernehmen({"instanz_id": "i1", "token": "bz_x"})
    k.speichern()
    z = netz.zustand()
    assert not z.verbunden and z.text == "Keine Verbindung zum Server" and z.konto.angemeldet
    with pytest.raises(NetzFehler):
        netz.geraete()


def test_anmeldung_ungueltig_wird_verworfen(zentrale):
    angemeldet(zentrale)
    zentrale.instanzen.clear()
    z = netz.zustand()
    assert z.text == "Anmeldung ungültig, bitte Instanz neu anlegen" and not Konto.laden().angemeldet


def test_kursleiter_code(zentrale):
    angemeldet(zentrale)
    with pytest.raises(ZentraleFehler) as e:
        netz.kursleiter_freischalten("falsch")
    assert e.value.meldung == "Der Kursleiter-Code ist ungültig." and not Konto.laden().ist_kursleiter
    assert netz.kursleiter_freischalten(" KL-RICHTIG ").ist_kursleiter
    assert Konto.laden().ist_kursleiter


def test_konto_extra_von_agents_bleibt(zentrale):
    angemeldet(zentrale)
    netz.aendern(lambda k: k.extra.__setitem__("aktiv", True))
    netz.kursleiter_freischalten("KL-RICHTIG")
    assert Konto.laden().extra["aktiv"] is True


def test_geraete_und_entfernen(zentrale):
    angemeldet(zentrale)
    zentrale.geraete = [geraet("2", "zeta", "100.64.0.9", False), geraet("1", "agent-pc-1", "100.64.0.7")]
    liste = netz.geraete()
    assert [g["name"] for g in liste] == ["agent-pc-1", "zeta"]
    g = netz.Geraet.aus(liste[1], {"100.64.0.9"})
    assert g.dieser_rechner and g.zeile(now=1760000120) == "100.64.0.9 · offline · zuletzt gesehen vor 2 min"
    assert netz.Geraet.aus(liste[0]).zeile() == "100.64.0.7 · online"
    netz.geraet_entfernen("2")
    netz.geraet_entfernen("2")   # schon weg: kein Fehler
    assert [g["name"] for g in netz.geraete()] == ["agent-pc-1"]


@pytest.mark.parametrize("wert, text", [
    (None, "–"), (0, "–"), (1760000000, "vor 2 min"), ("2025-10-09T08:53:20Z", "vor 2 min"), ("kaputt", "kaputt"),
])
def test_zuletzt(wert, text):
    assert netz.zuletzt(wert, now=1760000120) == text


# ---------------------------------------------------------------- Tailscale
def ts_json(state="Running", host="rechner-mint", ips=("100.64.0.5",), netzname="vpn.test"):
    return json.dumps({"BackendState": state, "Self": {"HostName": host, "TailscaleIPs": list(ips)},
                       "CurrentTailnet": {"Name": netzname}})


def fake_run(stdout="", rc=0):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        assert kw["env"]["PATH"] == netz.SYSTEM_PATH
        return subprocess.CompletedProcess(cmd, rc, stdout, "")

    run.calls = calls
    return run


def test_tailscale_status():
    which = lambda name, path=None: "/usr/bin/tailscale"   # noqa: E731
    ts = netz.tailscale_status(fake_run(ts_json()), which)
    assert ts.installiert and ts.dienst and ts.verbunden and ts.angemeldet
    assert ts.name == "rechner-mint" and ts.ips == ["100.64.0.5"] and ts.netz == "vpn.test"
    assert not netz.tailscale_status(fake_run(ts_json("Stopped"), rc=1), which).verbunden
    assert netz.tailscale_status(fake_run(ts_json("Stopped"), rc=1), which).angemeldet
    down = netz.tailscale_status(fake_run("", rc=1), which)   # Dienst läuft nicht
    assert down.installiert and not down.dienst and not down.angemeldet
    assert not netz.tailscale_status(fake_run(), lambda n, path=None: None).installiert


def test_rechnername():
    assert netz.rechnername("Mint-Büro") == "rechner-mint-buero"


def konto_mit(rekord=None) -> Konto:
    k = Konto(login_server="https://vpn.test")
    k.uebernehmen({"instanz_id": "inst1", "token": "bz_x", "login_server": "https://vpn.test"})
    if rekord:
        k.extra[netz.REKORD] = rekord
    return k


def test_zugehoerigkeit():
    ts = netz.Tailscale(True, True, "Running", "rechner-mint", ["100.64.0.5"], "vpn.test")
    liste = [geraet("1", "rechner-mint", "100.64.0.5")]
    assert netz.zugehoerigkeit(ts, konto_mit(), liste) == netz.EIGEN
    assert netz.zugehoerigkeit(ts, konto_mit(), []) == netz.FREMD            # andere Instanz / anderes Konto
    assert netz.zugehoerigkeit(ts, konto_mit(), None) == netz.FREMD          # ohne Liste nicht beweisbar
    rekord = {"instanz_id": "inst1", "name": "rechner-mint"}
    assert netz.zugehoerigkeit(ts, konto_mit(rekord), None) == netz.EIGEN    # offline: selbst verbunden
    assert netz.zugehoerigkeit(ts, konto_mit(rekord), []) == netz.ENTFERNT   # auf dem Server entfernt
    gestoppt = netz.Tailscale(True, True, "Stopped", "rechner-mint", [], "vpn.test")
    assert netz.zugehoerigkeit(gestoppt, konto_mit(rekord), []) == netz.EIGEN
    assert netz.zugehoerigkeit(gestoppt, konto_mit({"instanz_id": "alt", "name": "rechner-mint"}), []) == netz.FREMD
    anderes = netz.Tailscale(True, True, "Running", "rechner-mint", ["100.101.1.2"], "firma.ts.net")
    assert netz.zugehoerigkeit(anderes, konto_mit(rekord), None) == netz.FREMD
    for state in ("NeedsLogin", "NoState", ""):
        assert netz.zugehoerigkeit(netz.Tailscale(True, True, state), konto_mit(rekord), liste) == netz.KEINS


def test_anzeige():
    ts = netz.Tailscale(True, True, "Running", "rechner-mint", ["100.64.0.5"], "vpn.test")
    assert netz.anzeige(ts, netz.EIGEN) == ("Verbunden als rechner-mint · 100.64.0.5", "ok")
    assert netz.anzeige(ts, netz.FREMD) == ("Mit einem anderen Tailscale-Konto verbunden (vpn.test).", "warn")
    assert netz.anzeige(netz.Tailscale(False), netz.KEINS)[0] == "Tailscale ist nicht installiert."
    assert netz.anzeige(netz.Tailscale(True), netz.KEINS)[0] == "Der Tailscale-Dienst läuft nicht."


class Helfer:
    """Nachgebauter Helfer: merkt sich die Aufrufe und ändert den Tailscale-Zustand wie der echte."""

    def __init__(self, ts: netz.Tailscale, zentrale=None, up_fails=False):
        self.ts, self.zentrale, self.up_fails = ts, zentrale, up_fails
        self.calls: list[tuple] = []
        self.data: list[bytes | None] = []

    def status(self):
        return self.ts

    def __call__(self, *args, data=None):
        self.calls.append(args)
        self.data.append(data)
        if args[:2] == ("netz", "start"):
            self.ts = netz.Tailscale(True, True, "NeedsLogin", self.ts.name)
        elif args[:2] == ("netz", "up"):
            if self.up_fails:
                self.ts = netz.Tailscale(True, True, "NeedsLogin", self.ts.name)
                raise CliError("Tailscale hat sich nicht wieder verbunden – eine neue Anmeldung ist nötig.")
            self.ts = netz.Tailscale(True, True, "Running", self.ts.name, self.ts.ips, self.ts.netz)
        elif args[:2] == ("netz", "join"):
            name = args[args.index("--hostname") + 1]
            self.ts = netz.Tailscale(True, True, "Running", name, ["100.64.0.5"], "vpn.test")
            if self.zentrale is not None:
                self.zentrale.geraete.append(geraet("9", name, "100.64.0.5"))
        elif args[:2] == ("netz", "down"):
            self.ts = netz.Tailscale(True, True, "Stopped", self.ts.name, self.ts.ips, self.ts.netz)
        return ""


def verbinden(h, **kw):
    netz.verbinden(h, status=h.status, hostname="Mint", **kw)


def test_verbinden_neu(zentrale):
    angemeldet(zentrale)
    h = Helfer(netz.Tailscale(True, True, "NeedsLogin"), zentrale)
    verbinden(h)
    assert h.calls == [("netz", "join", "--login-server", "https://vpn.test", "--hostname", "rechner-mint")]
    assert h.data[-1] == zentrale.schluessel[-1].encode()     # Schlüssel nur über die Standardeingabe
    assert all(zentrale.schluessel[-1] not in " ".join(c) for c in h.calls)
    assert Konto.laden().extra[netz.REKORD] == {"instanz_id": Konto.laden().instanz_id, "name": "rechner-mint"}
    # Aus und wieder ein: kein neuer Schlüssel
    netz.trennen(h)
    verbinden(h)
    assert h.calls[-2:] == [("netz", "down"), ("netz", "up")] and len(zentrale.schluessel) == 1
    verbinden(h)   # schon verbunden: nichts zu tun
    assert h.calls[-1] == ("netz", "up") and len(h.calls) == 3


def test_verbinden_dienst_aus(zentrale):
    angemeldet(zentrale)
    h = Helfer(netz.Tailscale(True, False), zentrale)
    verbinden(h)
    assert [c[:2] for c in h.calls] == [("netz", "start"), ("netz", "join")]


def test_verbinden_nach_entfernen_neuer_schluessel(zentrale):
    angemeldet(zentrale)
    h = Helfer(netz.Tailscale(True, True, "NeedsLogin"), zentrale)
    verbinden(h)
    netz.trennen(h)
    zentrale.geraete.clear()          # auf dem Server entfernt
    h.ts = netz.Tailscale(True, True, "Stopped", "rechner-mint")
    h.up_fails = True
    verbinden(h)
    assert [c[:2] for c in h.calls[-2:]] == [("netz", "up"), ("netz", "join")]
    assert "--replace" not in h.calls[-1] and len(zentrale.schluessel) == 2


def test_verbinden_anderes_konto_nur_nach_rueckfrage(zentrale):
    angemeldet(zentrale)
    h = Helfer(netz.Tailscale(True, True, "Running", "laptop", ["100.101.1.2"], "firma.ts.net"), zentrale)
    with pytest.raises(netz.Rueckfrage) as e:
        verbinden(h)
    assert e.value.netz == "firma.ts.net" and h.calls == [] and zentrale.schluessel == []
    verbinden(h, ersetzen=True)
    assert h.calls[-1][-1] == "--replace" and h.calls[-1][:2] == ("netz", "join")


def test_verbinden_ohne_server_und_ohne_instanz(zentrale):
    with pytest.raises(RuntimeError):
        verbinden(Helfer(netz.Tailscale(True, True, "NeedsLogin")))
    k = Konto(server="http://127.0.0.1:9")
    k.uebernehmen({"instanz_id": "i1", "token": "bz_x"})
    k.speichern()
    h = Helfer(netz.Tailscale(True, True, "NeedsLogin"))
    with pytest.raises(NetzFehler):
        verbinden(h)
    assert h.calls == []
    # Selbst verbunden gewesen: Einschalten geht auch ohne Server (kein Schlüssel nötig)
    netz.aendern(lambda k: k.extra.__setitem__(netz.REKORD, {"instanz_id": "i1", "name": "rechner-mint"}))
    h.ts = netz.Tailscale(True, True, "Stopped", "rechner-mint")
    verbinden(h)
    assert h.calls == [("netz", "up")]
    with pytest.raises(RuntimeError):
        verbinden(Helfer(netz.Tailscale(False)))


# ---------------------------------------------------------------- Installation aus der offiziellen Quelle
MINT = 'NAME="Linux Mint"\nID=linuxmint\nID_LIKE="ubuntu debian"\nVERSION_CODENAME=zena\nUBUNTU_CODENAME=noble\n'


@pytest.mark.parametrize("text, quelle", [
    (MINT, ("ubuntu", "noble")),
    ("ID=debian\nVERSION_CODENAME=trixie\n", ("debian", "trixie")),
    ("ID=ubuntu\nVERSION_CODENAME=jammy\nUBUNTU_CODENAME=jammy\n", ("ubuntu", "jammy")),
    ("ID=lmde\nID_LIKE=debian\nVERSION_CODENAME=faye\nDEBIAN_CODENAME=bookworm\n", ("debian", "bookworm")),
    ("ID=fedora\nVERSION_ID=40\n", None),
    ("ID=debian\nVERSION_CODENAME='; rm -rf /'\n", None),
])
def test_paketquelle(text, quelle):
    assert netz.paketquelle(text) == quelle


def test_installations_skript():
    skript = netz.installations_skript("ubuntu", "noble")
    assert "https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg" in skript
    assert "https://pkgs.tailscale.com/stable/ubuntu/noble.tailscale-keyring.list" in skript
    assert "apt-get install -y -q tailscale" in skript and "systemctl enable --now tailscaled" in skript
    assert subprocess.run(["bash", "-n"], input=skript, text=True).returncode == 0
    with pytest.raises(ValueError):
        netz.installations_skript("ubuntu", "noble; reboot")


def test_windows_installer(tmp_path):
    class Resp:
        def __init__(self, url):
            self.url, self.chunks = url, [b"MZ", b"..."]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def geturl(self):
            return self.url

        def read(self, n):
            return self.chunks.pop(0) if self.chunks else b""

    ok = lambda req, timeout: Resp("https://pkgs.tailscale.com/stable/tailscale-setup-1.104.1.exe")  # noqa: E731
    assert netz.windows_installer_laden(tmp_path, ok).read_bytes() == b"MZ..."
    fremd = lambda req, timeout: Resp("https://example.com/tailscale-setup-1.0.exe")  # noqa: E731
    with pytest.raises(OSError):
        netz.windows_installer_laden(tmp_path, fremd)

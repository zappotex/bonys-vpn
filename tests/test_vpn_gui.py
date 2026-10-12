# SPDX-License-Identifier: GPL-3.0-or-later
"""Oberfläche von Bony's VPN: Logik ohne GTK (model.py) und ein Smoke-Test der echten GTK-Oberfläche
ohne Bildschirm (Broadway-Backend) – im Agent-PC und auf dem eigenen Rechner."""
import base64
import io
import itertools
import json
import os
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

if sys.platform != "linux":
    pytest.skip("Bony's VPN gibt es nur unter Linux", allow_module_level=True)

from bonys_vpn import conf as wgconf  # noqa: E402
from bonys_vpn import model  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def key(seed: int) -> str:
    return base64.b64encode(bytes([seed]) * 32).decode()


PRIV, PUB, PSK = key(1), key(2), key(3)
CONF = textwrap.dedent(f"""\
    [Interface]
    PrivateKey = {PRIV}
    Address = 10.8.0.2/24
    DNS = 10.8.0.1

    [Peer]
    PublicKey = {PUB}
    PresharedKey = {PSK}
    AllowedIPs = 0.0.0.0/0
    Endpoint = vpn.example.net:51820
    """)


@pytest.fixture(autouse=True)
def german():
    model.set_language("de")
    yield
    model.set_language(None)


def status_json(now=1_000_000, active="fritz", handshake_age=30):
    return {
        "tunnels": [
            {"name": "fritz", "active": active == "fritz", "autostart": True, "managed": True,
             "addresses": ["10.8.0.2/24"], "dns": ["10.8.0.1"],
             "peers": [{"public_key": PUB, "allowed_ips": ["0.0.0.0/0"], "endpoint": "vpn.example.net:51820"}],
             "hooks": [], **({"handshake": now - handshake_age, "rx": 2048, "tx": 1024} if active == "fritz" else {})},
            {"name": "fremd", "active": active == "fremd", "autostart": False, "managed": False,
             "addresses": ["10.2.0.2/32"], "dns": [], "peers": [{"endpoint": None}], "hooks": ["PostUp"]},
        ],
        "active": active,
        "killswitch": {"enabled": True, "active": True, "exceptions": ["10.0.2.0/24"]},
    }


# ---------- Sprache und Formatierung ----------
@pytest.mark.parametrize("env, lang", [
    ({"LANG": "de_DE.UTF-8"}, "de"), ({"LANG": "en_US.UTF-8"}, "en"), ({"LANG": "fr_FR.UTF-8"}, "en"),
    ({"LANGUAGE": "de:en", "LANG": "en_US.UTF-8"}, "de"), ({"LC_ALL": "C", "LANG": "de_AT.UTF-8"}, "de"),
    ({}, "en"), ({"LANG": "C.UTF-8"}, "en"),
])
def test_language(env, lang):
    assert model.language(env) == lang


def test_translation_fallback():
    assert model.tr("Verbinden") == "Verbinden"
    model.set_language("en")
    assert model.tr("Verbinden") == "Connect"
    assert model.tr("Verbunden mit {name}", name="wg0") == "Connected to wg0"
    assert model.tr("nicht übersetzt") == "nicht übersetzt"  # fehlt die Übersetzung, bleibt Deutsch


def test_every_translation_has_the_same_placeholders():
    import re

    for de, en in model.EN.items():
        assert sorted(re.findall(r"{\w+}", de)) == sorted(re.findall(r"{\w+}", en)), de


def test_all_texts_of_the_gui_are_translated():
    """Jeder tr("…")-Text in gui.py und model.py hat eine englische Fassung."""
    import ast

    for name in ("gui.py", "model.py"):
        tree = ast.parse((ROOT / "src/bonys_vpn" / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "tr" and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant):
                    assert arg.value in model.EN, f"{name}: {arg.value!r} fehlt in model.EN"


def test_format_bytes():
    assert model.fmt_bytes(0) == "0 B"
    assert model.fmt_bytes(None) == "0 B"
    assert model.fmt_bytes(1023) == "1023 B"
    assert model.fmt_bytes(1536) == "1,5 KiB"
    assert model.fmt_bytes(5 * 1024**3) == "5,0 GiB"
    assert model.fmt_bytes(1536, "en") == "1.5 KiB"


def test_format_age():
    assert model.fmt_age(None) == "noch keiner"
    assert model.fmt_age(990, now=1000) == "vor 10 s"
    assert model.fmt_age(1000 - 125, now=1000) == "vor 2 min"
    assert model.fmt_age(1000 - 3720, now=1000) == "vor 1 h 2 min"
    assert model.fmt_age(1005, now=1000) == "vor 0 s"  # Uhr leicht verstellt
    model.set_language("en")
    assert model.fmt_age(990, now=1000) == "10 s ago"


# ---------- Statusmodell ----------
def test_status_model():
    st = model.Status.from_helper(status_json())
    assert st.names() == ["fritz", "fremd"]
    a = st.active
    assert a.name == "fritz" and a.endpoints == ["vpn.example.net:51820"] and a.rx == 2048
    assert st.get("fremd").managed is False and st.get("fremd").hooks == ["PostUp"]
    assert st.get("fremd").endpoints == []
    assert st.killswitch_enabled and st.exceptions == ["10.0.2.0/24"]
    assert st.state(now=1_000_000) == "connected"
    assert st.summary(now=1_000_000) == "Verbunden mit fritz"
    rows = dict(a.detail_rows(now=1_000_000))
    assert rows == {"Adresse": "10.8.0.2/24", "DNS": "10.8.0.1", "Endpunkt": "vpn.example.net:51820",
                    "Letzter Handshake": "vor 30 s", "Empfangen": "2,0 KiB", "Gesendet": "1,0 KiB"}
    assert dict(st.get("fremd").detail_rows()) == {"Adresse": "10.2.0.2/32", "DNS": "–", "Endpunkt": "–"}


def test_status_states():
    stale = model.Status.from_helper(status_json(handshake_age=200))
    assert stale.state(now=1_000_000) == "connected"   # alter Handshake, aber nichts gesendet: nur untätig
    stale.active.sent_at = 1_000_000 - 2              # gesendet, kein neuer Handshake → Server stumm
    assert stale.state(now=1_000_000) == "warning"
    assert stale.summary(now=1_000_000) == "Server antwortet nicht"
    blocked = model.Status.from_helper(status_json(active=None))
    assert blocked.state() == "blocked" and blocked.summary() == "Internet gesperrt (Kill-Switch)"
    data = status_json(active=None)
    data["killswitch"]["enabled"] = False
    assert model.Status.from_helper(data).state() == "disconnected"
    assert model.Status().summary() == "Kein Tunnel verbunden"
    # noch nie ein Handshake: (noch) kein Abbruch
    assert not model.Tunnel(name="x", active=True, handshake=None, sent_at=10**9).stale(now=10**9)
    # gesendet, aber noch innerhalb der Zeit für den neuen Handshake (120 s + Spielraum)
    t = model.Tunnel(name="x", active=True, handshake=1000, sent_at=1000 + 130)
    assert not t.stale(now=1000 + 200)
    t.sent_at = 1000 + 140
    assert t.stale(now=1000 + 200)
    assert not model.Tunnel(name="x", active=False, handshake=1000, sent_at=1500).stale(now=2000)


def test_activity_tracks_sending_across_refreshes():
    act = model.Activity()
    act.update({"fritz": (10, 100)}, now=1000)
    assert act.sent_at == {}                    # erster Wert: noch kein Vergleich
    act.update({"fritz": (10, 100)}, now=1002)
    assert act.sent_at == {}
    act.update({"fritz": (10, 150)}, now=1004)
    assert act.sent_at == {"fritz": 1004}
    st = model.Status.from_helper(status_json())  # frischer Stand vom Helfer
    act.apply(st)
    assert st.get("fritz").sent_at == 1004 and st.get("fremd").sent_at is None
    act.update({}, now=1006)                    # getrennt → vergessen
    assert act.sent_at == {} and act.tx == {}


def test_status_never_contains_secrets():
    """Das Modell übernimmt nur nicht geheime Felder – auch wenn ein Helfer zu viel liefern würde."""
    data = status_json()
    data["tunnels"][0]["private_key"] = PRIV
    st = model.Status.from_helper(data)
    assert PRIV not in repr(st)


def test_live_values_from_sys(tmp_path):
    for name, devtype, rx, tx in (("fritz", "wireguard", 500, 300), ("eth0", "", 9, 9), ("wg9", "wireguard", 1, 2)):
        d = tmp_path / name / "statistics"
        d.mkdir(parents=True)
        uevent = f"INTERFACE={name}\nIFINDEX=3\n" + (f"DEVTYPE={devtype}\n" if devtype else "")
        (tmp_path / name / "uevent").write_text(uevent)
        (d / "rx_bytes").write_text(f"{rx}\n")
        (d / "tx_bytes").write_text(f"{tx}\n")
    (tmp_path / "broken").mkdir()
    live = model.read_live(tmp_path)
    assert live == {"fritz": (500, 300), "wg9": (1, 2)}
    assert model.read_live(tmp_path / "fehlt") == {}
    st = model.Status.from_helper(status_json())
    assert st.apply_live(live) is False and st.active.rx == 500
    assert st.apply_live({}) is True          # fritz ist weg → Helfer neu fragen
    assert st.apply_live({"fremd": (1, 1), "fritz": (1, 1)}) is True


# ---------- Abbrüche erkennen ----------
def test_watch_reports_drop_once():
    w = model.Watch()
    now = 1_000_000
    up = model.Status.from_helper(status_json(now=now))
    down = model.Status.from_helper(status_json(now=now, active=None))
    assert w.observe(up, now) == []            # erster Stand: nichts melden
    events = w.observe(down, now + 2)
    assert [(e.kind, e.name) for e in events] == [("dropped", "fritz")]
    assert events[0].title() == "Verbindung abgebrochen" and "fritz" in events[0].body()
    assert w.observe(down, now + 4) == []      # nur einmal


def test_watch_ignores_own_actions_and_deleted_tunnels():
    w = model.Watch()
    now = 1_000_000
    w.observe(model.Status.from_helper(status_json(now=now)), now)
    w.expect(now=now)
    assert w.observe(model.Status.from_helper(status_json(now=now, active=None)), now + 5) == []
    w.observe(model.Status.from_helper(status_json(now=now)), now + 60)
    gone = model.Status.from_helper(status_json(now=now, active=None))
    gone.tunnels = [t for t in gone.tunnels if t.name != "fritz"]
    assert w.observe(gone, now + 61) == []     # gelöscht, nicht abgebrochen


def test_watch_stale_and_restored():
    w = model.Watch()
    now = 1_000_000
    w.observe(model.Status.from_helper(status_json(now=now, handshake_age=10)), now)
    idle = model.Status.from_helper(status_json(now=now, handshake_age=200))
    assert w.observe(idle, now) == []          # nur untätig – keine Meldung
    stale = model.Status.from_helper(status_json(now=now, handshake_age=200))
    stale.active.sent_at = now - 1
    assert [(e.kind, e.name) for e in w.observe(stale, now)] == [("stale", "fritz")]
    assert w.observe(stale, now + 2) == []
    ok = model.Status.from_helper(status_json(now=now, handshake_age=5))
    events = w.observe(ok, now)
    assert [(e.kind, e.name) for e in events] == [("restored", "fritz")]
    assert events[0].title() == "Verbindung wieder da"


def test_rx_watch():
    rw = model.RxWatch(quiet_after=150)
    t = 1000.0
    assert not rw.suspicious({"wg": (100, 50)}, t)
    assert not rw.suspicious({"wg": (100, 60)}, t + 100)
    assert rw.suspicious({"wg": (100, 70)}, t + 160)        # nichts empfangen, aber gesendet
    assert not rw.suspicious({"wg": (100, 80)}, t + 200)    # nur einmal fragen
    assert not rw.suspicious({"wg": (150, 90)}, t + 210)    # wieder Daten → zurückgesetzt
    assert not rw.suspicious({"wg": (150, 90)}, t + 400)    # nichts gesendet: Ruhe, kein Abbruch
    assert not rw.suspicious({}, t + 500)


# ---------- Import, Namen, Editor ----------
@pytest.mark.parametrize("stem, existing, expect", [
    ("wg0", (), "wg0"), ("Fritz Box (Büro)", (), "Fritz_Box__B_ro"), ("-start", (), "start"),
    ("", (), "tunnel"), ("..", (), "tunnel"), ("wg0", ("wg0",), "wg0-2"), ("wg0", ("wg0", "wg0-2"), "wg0-3"),
    ("a" * 20, ("a" * 15,), "a" * 13 + "-2"),
])
def test_suggest_name(stem, existing, expect):
    name = model.suggest_name(stem, existing)
    assert name == expect
    assert wgconf.check_name(name) == name


def test_name_error():
    assert model.name_error("wg0") == ""
    assert model.name_error("a b") and model.name_error("") and model.name_error("x" * 16)
    assert model.name_error("../x") and model.name_error("-x")


def test_editor_check_with_hidden_keys():
    masked = wgconf.mask(CONF)
    assert PRIV not in masked and PSK not in masked and wgconf.HIDDEN in masked
    assert model.check_text(masked).addresses == ["10.8.0.2/24"]
    with pytest.raises(wgconf.ConfError) as e:
        model.check_text(masked.replace("Address = 10.8.0.2/24", "Address = kaputt"))
    assert "Zeile 3" in str(e.value) and "kaputt" not in str(e.value)  # nie den Wert nennen
    # „(verborgen)“ irgendwo anders gilt nicht als Schlüssel
    with pytest.raises(wgconf.ConfError):
        model.check_text(masked.replace(f"PublicKey = {PUB}", f"PublicKey = {wgconf.HIDDEN}"))
    hooked = masked.replace("DNS = 10.8.0.1", "DNS = 10.8.0.1\nPostUp = echo hi")
    assert model.check_text(hooked).hooks == ["PostUp"]


def test_editor_reveal_and_hide_roundtrip():
    masked = wgconf.mask(CONF)
    edited = masked.replace("DNS = 10.8.0.1", "DNS = 9.9.9.9")
    shown = model.reveal(edited, CONF)
    assert PRIV in shown and PSK in shown and "9.9.9.9" in shown  # Änderungen bleiben erhalten
    hidden = model.hide(shown)
    assert PRIV not in hidden and PSK not in hidden and "9.9.9.9" in hidden
    assert hidden == edited


def test_public_ip():
    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    seen = {}

    def opener(req, timeout):
        seen["url"], seen["timeout"] = req.full_url, timeout
        return Resp(b'{"ip":"203.0.113.7"}')

    assert model.public_ip(opener=opener) == "203.0.113.7"
    assert seen["url"].startswith("https://") and model.IP_SERVICE_NAME in seen["url"]
    with pytest.raises(ValueError):
        model.public_ip(opener=lambda req, timeout: Resp(b'{"ip":"<script>"}'))


def test_write_private(tmp_path):
    target = tmp_path / "out.conf"
    target.write_text("alt")
    target.chmod(0o644)
    model.write_private(target, CONF)
    assert target.read_text() == CONF
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert [p.name for p in tmp_path.iterdir()] == ["out.conf"]  # keine Reste


# ---------- Smoke-Test der GTK-Oberfläche ----------
SMOKE = r"""
import json, sys, time
sys.path.insert(0, sys.argv[1])
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk
from bonys_vpn import gui, model, conf as wgconf

CONF = sys.argv[2]
STATUS = json.loads(sys.argv[3])
calls = []

class Fake:
    def __init__(self):
        self.data = STATUS
    def status(self):
        return self.data
    def run(self, *args, data=None):
        calls.append(args)
        if args[0] == "show":
            return json.dumps({"name": args[1], "managed": True, "conf": wgconf.mask(CONF)})
        if args[0] == "export":
            return CONF
        if args[0] == "rename":
            time.sleep(0.3)  # Helfer braucht etwas – die Oberfläche zeichnet inzwischen neu
            for t in self.data["tunnels"]:
                if t["name"] == args[1]:
                    t["name"] = args[2]
        return ""

model.set_language(sys.argv[4])
fake = Fake()
app = gui.App(client=fake, sys_net=gui.Path("/nonexistent"), unique=False, agentpc=True, auto_status=True,
              update_check=False)
errors = []

def check(cond, what):
    if not cond:
        errors.append(what)

def step1():
    w = app.window
    check(w is not None and w.get_visible(), "Fenster nicht sichtbar")
    w.resize(800, 600)
    check(w.stack.get_visible_child_name() == "main", "Tunnelliste fehlt")
    check(len(w.listbox.get_children()) == 2, "zwei Tunnel erwartet")
    check(w.d_name.get_text() == "fritz", "aktiver Tunnel nicht ausgewählt")
    check(w.connect_btn.get_label() == model.tr("Trennen"), "Knopf sollte Trennen heißen")
    check(w.ks_main["switch"].get_active(), "Kill-Switch-Schalter aus")
    check("10.0.2.0/24" in w.ks_main["nets"].get_text(), "Ausnahmen fehlen")
    check(w.auto_switch.get_active(), "Autostart-Schalter aus")
    # Schalter wie ein Klick umlegen: genau ein Aufruf des Helfers, kein Gegenbefehl
    w.ks_main["switch"].set_active(False)
    w.auto_switch.set_active(False)
    GLib.timeout_add(600, step1b)
    return False

def step1b():
    w = app.window
    ks = [c for c in calls if c[0] == "killswitch"]
    auto = [c for c in calls if c[0] == "autostart"]
    check(ks == [("killswitch", "off")], f"Kill-Switch-Aufrufe: {ks}")
    check(auto == [("autostart", "fritz", "off")], f"Autostart-Aufrufe: {auto}")
    # Helfer meldet weiter „an“ → Schalter zeigen nach der Antwort wieder den echten Zustand
    check(w.ks_main["switch"].get_active() and w.ks_empty["switch"].get_active(), "Kill-Switch nicht zurückgesetzt")
    w.listbox.select_row(w.listbox.get_children()[1])
    GLib.timeout_add(200, step2)
    return False

def step2():
    w = app.window
    check(w.d_name.get_text() == "fremd", "Auswahl wechselt nicht")
    check(not w.action_btns["delete"].get_sensitive(), "fremder Tunnel darf nicht löschbar sein")
    check(w.d_hooks.get_visible(), "Hook-Hinweis fehlt")
    # Editor: Schlüssel verborgen, anzeigen, wieder verbergen
    ed = gui.Editor(w, "fritz", json.dumps({"name": "fritz", "managed": True, "conf": wgconf.mask(CONF)}))
    secret = wgconf.secrets_of(CONF)[0]
    check(secret not in ed.text() and wgconf.HIDDEN in ed.text(), "Schlüssel nicht verborgen")
    check(ed.save_btn.get_sensitive(), "gültige Konfiguration nicht speicherbar")
    ed.reveal.set_active(True)
    def step3():
        check(secret in ed.text(), "Schlüssel anzeigen klappt nicht")
        ed.reveal.set_active(False)
        check(secret not in ed.text(), "Schlüssel wieder verbergen klappt nicht")
        ed.view.get_buffer().set_text("[Interface]\nkaputt")
        ed._check()
        check(not ed.save_btn.get_sensitive(), "ungültige Konfiguration speicherbar")
        ed.destroy()
        # Umbenennen: Auswahl landet nach dem Erfolg auf dem neuen Namen, nicht auf dem aktiven Tunnel
        w.listbox.select_row(w.listbox.get_children()[1])
        w.app.action("fremd", None, ("rename", "fremd", "neu"), on_ok=lambda: setattr(w, "wanted", "neu"))
        GLib.timeout_add(1200, step3b)
        return False
    def step3b():
        check(w.d_name.get_text() == "neu", f"nach dem Umbenennen ausgewählt: {w.d_name.get_text()}")
        # leerer Zustand
        fake.data = {"tunnels": [], "killswitch": {"enabled": False, "active": False, "exceptions": []}}
        app.refresh()
        GLib.timeout_add(300, step4)
        return False
    GLib.timeout_add(300, step3)
    return False

def step4():
    check(app.window.stack.get_visible_child_name() == "empty", "leere Ansicht fehlt")
    w = app.window
    w.resize(560, 420)   # kleinste Größe: muss ohne Fehler gehen
    GLib.timeout_add(200, finish)
    return False

def finish():
    check(("export", "fritz") in calls, "Export für 'anzeigen' nicht aufgerufen")
    print("SMOKE-OK" if not errors else "SMOKE-FEHLER: " + "; ".join(errors))
    app.quit()
    return False

START = time.time()

def ready():
    # erst loslegen, wenn das Fenster den Status zeigt (erster GTK-Start kann dauern: Schrift-Cache)
    w = app.window
    if (w is None or not app.status.tunnels) and time.time() - START < 20:
        GLib.timeout_add(200, ready)
        return False
    GLib.timeout_add(300, step1)
    return False

GLib.timeout_add(200, ready)
GLib.timeout_add(40000, lambda: (print("SMOKE-TIMEOUT"), app.quit()))
app.run(["bonys-vpn-gui"])
"""


_DISPLAYS = itertools.count()


def _gtk_python():
    """System-Python mit python3-gi (GTK 3) und broadwayd – sonst wird der Smoke-Test übersprungen."""
    for py in ("/usr/bin/python3", sys.executable):
        if Path(py).exists() and subprocess.run(
                [py, "-c", "import gi; gi.require_version('Gtk', '3.0'); from gi.repository import Gtk"],
                capture_output=True).returncode == 0:
            return py
    return None


@pytest.fixture
def broadway(tmp_path_factory):
    py = _gtk_python()
    if not py or not shutil.which("broadwayd"):
        pytest.skip("GTK 3 für Python (python3-gi) oder broadwayd fehlt")
    display = f":{40 + (os.getpid() + next(_DISPLAYS)) % 50}"
    runtime = tmp_path_factory.mktemp("xdg-runtime")
    runtime.chmod(0o700)
    env = dict(os.environ, XDG_RUNTIME_DIR=str(runtime))
    proc = subprocess.Popen(["broadwayd", display], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    import time

    time.sleep(0.8)
    yield py, display, runtime
    proc.terminate()
    proc.wait(5)


@pytest.mark.parametrize("lang", ["de", "en"])
def test_gui_smoke_offscreen(broadway, lang, tmp_path):
    py, display, runtime = broadway
    env = dict(os.environ, GDK_BACKEND="broadway", BROADWAY_DISPLAY=display, NO_AT_BRIDGE="1",
               XDG_RUNTIME_DIR=str(runtime))
    env.pop("DBUS_SESSION_BUS_ADDRESS", None)
    proc = subprocess.run([py, "-c", SMOKE, str(ROOT / "src"), CONF, json.dumps(status_json(active="fritz")), lang],
                          capture_output=True, text=True, env=env, timeout=60)
    assert "SMOKE-OK" in proc.stdout, proc.stdout + proc.stderr
    assert PRIV not in proc.stdout + proc.stderr


SMOKE_HOST = r"""
import json, sys, time
sys.path.insert(0, sys.argv[1])
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk
from bonys_vpn import gui, host, model, updates

STATUS = json.loads(sys.argv[2])
calls = []

class Fake:
    def status(self):
        return STATUS
    def run(self, *args, data=None):
        calls.append(args)
        return ""

model.set_language("de")
answers = {"killswitch": False, "whole": False}
gui.Window.confirm_killswitch = lambda self: answers["killswitch"]
gui.Window.confirm_whole_computer = lambda self: answers["whole"]
host.nm_wireguard = lambda *a: {"nm-tunnel"}
errors = []

def check(cond, what):
    if not cond:
        errors.append(what)

app = gui.App(client=Fake(), sys_net=gui.Path("/nonexistent"), unique=False, agentpc=False, auto_status=False,
              update_check=False)

def step1():
    w = app.window
    check(set(w.menu_items) == {"updates", "agents", "exceptions", "help", "about"}, f"Menü: {set(w.menu_items)}")
    # Symbol in der Leiste bei jedem Start: Schließen versteckt nur. Ohne Symbol (wie hier unter Broadway
    # nach der Prüfung) beendet Schließen das Programm – kein unsichtbarer Prozess
    check(app.tray is not None or app.tray_failed, "kein Versuch, ein Symbol anzulegen")
    if app.tray is not None:
        check(app.keep_running() and app.holding, "läuft mit Symbol nicht weiter")
        app.tray_unavailable()
    check(not app.keep_running() and not app.holding, "läuft ohne Symbol unsichtbar weiter")
    app.agentpc = True   # Agent-PC: Menü ohne Bony's Agents
    w._menu_button()
    check("agents" not in w.menu_items and "exceptions" not in w.menu_items, "Agent-PC zeigt Bony's Agents")
    app.agentpc = False
    # Kill-Switch auf dem eigenen Rechner: abgelehnte Warnung → Schalter zurück, kein Aufruf
    w.ks_empty["switch"].set_active(True)
    check(not w.ks_empty["switch"].get_active(), "Schalter nach Ablehnen nicht zurückgesetzt")
    check(not [c for c in calls if c[0] == "killswitch"], "Kill-Switch trotz Ablehnen geschaltet")
    answers["killswitch"] = True
    app.pending_exceptions = ["192.168.178.0/24"]
    w.ks_empty["switch"].set_active(True)
    # Verbinden: ohne Bestätigung des Hinweises kein Aufruf
    w.selected = "fritz"
    w._on_connect(None)
    check(not [c for c in calls if c[0] == "up"], "verbunden ohne Hinweis")
    answers["whole"] = True
    w._on_connect(None)   # NetworkManager-Tunnel aktiv → Bitte um Trennen, kein Aufruf
    check(not [c for c in calls if c[0] == "up"], "verbunden trotz NetworkManager-Tunnel")
    check("NetworkManager" in w.info_label.get_text(), "Hinweis NetworkManager fehlt")
    host.nm_wireguard = lambda *a: set()
    w._on_connect(None)
    # Bony's Agents: nicht installiert → „installieren“, installiert → „öffnen“
    host.agents_command = lambda home=None: None
    d = gui.AgentsDialog(w)
    check(d.main_btn.get_label() == "Bony's Agents installieren …", f"Knopf: {d.main_btn.get_label()}")
    d.destroy()
    host.agents_command = lambda home=None: ["/opt/bonys-agents/BonysAgents"]
    d = gui.AgentsDialog(w)
    check(d.main_btn.get_label() == "Bony's Agents öffnen", f"Knopf: {d.main_btn.get_label()}")
    d.destroy()
    # Update-Leiste
    w.show_update(updates.Release("9.9.9", "v9.9.9"))
    check(w.updatebar.get_visible() and "9.9.9" in w.update_label.get_text(), "Update-Leiste fehlt")
    # Fortschrittsfenster: Protokoll, Balken, Erfolg
    def work(log, progress):
        log("Lade bonys-agents_0.15.0_amd64.deb …")
        progress(50, 100)
    t = gui.TaskDialog(w, "Test", work, done_text="Fertig.")
    t.start()
    GLib.timeout_add(600, lambda: step2(t))
    return False

def step2(t):
    check(t.ok is True and t.close_btn.get_sensitive(), "Fortschrittsfenster nicht fertig")
    buf = t.view.get_buffer()
    check("bonys-agents" in buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False), "Protokoll fehlt")
    t.destroy()
    def fail(log, progress):
        raise updates.UpdateError("Signaturprüfung fehlgeschlagen")
    t2 = gui.TaskDialog(app.window, "Test", fail, done_text="Fertig.")
    t2.start()
    GLib.timeout_add(600, lambda: step3(t2))
    return False

def step3(t2):
    check(t2.ok is False and "Signaturprüfung" in t2.headline.get_text(), "Fehler nicht verständlich angezeigt")
    t2.destroy()
    ks = [c for c in calls if c[0] == "killswitch"]
    check(ks == [("killswitch", "on", "--exception", "192.168.178.0/24")], f"Kill-Switch-Aufrufe: {ks}")
    check([c for c in calls if c[0] == "up"] == [("up", "fritz")], f"Verbinden: {calls}")
    print("SMOKE-OK" if not errors else "SMOKE-FEHLER: " + "; ".join(errors))
    app.quit()
    return False

START = time.time()

def ready():
    # erst loslegen, wenn das Fenster den Status zeigt (erster GTK-Start kann dauern: Schrift-Cache)
    w = app.window
    if (w is None or not app.status.tunnels) and time.time() - START < 20:
        GLib.timeout_add(200, ready)
        return False
    GLib.timeout_add(300, step1)
    return False

GLib.timeout_add(200, ready)
GLib.timeout_add(40000, lambda: (print("SMOKE-TIMEOUT"), app.quit()))
app.run(["bonys-vpn-gui"])
"""


def test_gui_smoke_host(broadway, tmp_path):
    """Eigener Rechner: Menü mit Bony's Agents, Warnungen, NetworkManager, Update-Leiste, Fortschritt."""
    py, display, runtime = broadway
    env = dict(os.environ, GDK_BACKEND="broadway", BROADWAY_DISPLAY=display, NO_AT_BRIDGE="1",
               XDG_RUNTIME_DIR=str(runtime), XDG_CONFIG_HOME=str(tmp_path))
    env.pop("DBUS_SESSION_BUS_ADDRESS", None)
    status = status_json(active=None)
    status["killswitch"] = {"enabled": False, "active": False, "exceptions": []}
    proc = subprocess.run([py, "-c", SMOKE_HOST, str(ROOT / "src"), json.dumps(status)],
                          capture_output=True, text=True, env=env, timeout=60)
    assert "SMOKE-OK" in proc.stdout, proc.stdout + proc.stderr

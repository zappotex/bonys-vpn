# SPDX-License-Identifier: GPL-3.0-or-later
"""Bereich „Bony-Netz“ im Fenster von Bony's VPN (GTK 3) – Logik in ``netz.py``.

Server und Instanz wie in Bony's Agents (gemeinsame ``konto.json``), Liste der Geräte der Instanz, Kursleiter-Code
und der Schalter „Diesen Rechner verbinden“ (Tailscale). Jeder Aufruf der Zentrale und des Helfers läuft in einem
Hintergrund-Thread. Die Zentrale wird nur gefragt, solange der Bereich sichtbar ist – alle 30 Sekunden – und nur,
wenn schon eine Instanz angemeldet ist oder der Benutzer es auslöst („Prüfen“, „Instanz anlegen“).
Die WireGuard-Tunnel bleiben davon unberührt.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
from collections.abc import Callable

from gi.repository import GLib, Gtk, Pango

from bony_netz import STANDARD_SERVER, Konto
from bony_netz.konto import server_normalisieren

from . import host, model, netz, updates
from .gui import AgentsDialog, TaskDialog, ask, label, set_quietly, styled
from .model import tr

EN = {
    "Über das Bony-Netz erreichst du deine Agent-PCs aus Bony's Agents übers Internet. Bony's Agents benutzt "
    "dieselbe Anmeldung – eine dort angelegte Instanz gilt hier sofort.":
        "The Bony-Netz lets you reach your agent PCs from Bony's Agents over the internet. Bony's Agents uses the "
        "same sign-in – an instance created there applies here right away.",
    "Server": "Server",
    "Instanz": "Instance",
    "Instanz-ID: {id} · Kursleiter: {kl}": "Instance ID: {id} · Course leader: {kl}",
    "ja": "yes",
    "nein": "no",
    "Noch keine Instanz angelegt.": "No instance created yet.",
    "Eine Instanz ist deine Anmeldung beim Server – einmal pro Rechner.":
        "An instance is your sign-in at the server – one per computer.",
    "Erst den neuen Server mit „Prüfen“ übernehmen.": "First apply the new server with “Check”.",
    "Instanz anlegen": "Create instance",
    "Instanz löschen …": "Delete instance …",
    "Instanz löschen": "Delete instance",
    "Kursleiter-Code": "Course leader code",
    "Einlösen": "Redeem",
    "Kursleiter-Code eingelöst.": "Course leader code redeemed.",
    "Diesen Rechner verbinden": "Connect this computer",
    "Tailscale installieren …": "Install Tailscale …",
    "Ist auf diesem Rechner ein anderes Tailscale-Konto angemeldet, wird es beim Einschalten ersetzt – vorher "
    "kommt eine Rückfrage.":
        "If another Tailscale account is signed in on this computer, it is replaced when you turn this on – you "
        "will be asked first.",
    "Der Kill-Switch ist an: Ohne die Ausnahme {net} (Menü → Kill-Switch-Ausnahmen …) ist das Bony-Netz gesperrt.":
        "The kill switch is on: without the exception {net} (menu → Kill switch exceptions …) the Bony-Netz is "
        "blocked.",
    "Agenten-PCs": "Agent PCs",
    "Noch keine Geräte im Netz.": "No devices in the network yet.",
    "Entfernen …": "Remove …",
    "„{name}“ aus dem Netz entfernen?": "Remove “{name}” from the network?",
    "Das Gerät verliert sofort die Verbindung zum Bony-Netz und wird aus allen Kursen genommen. Für einen neuen "
    "Beitritt braucht es einen neuen Geräteschlüssel.":
        "The device immediately loses its connection to the Bony-Netz and is removed from all courses. Joining again "
        "needs a new device key.",
    "Entfernen": "Remove",
    "„{name}“ wurde entfernt.": "“{name}” was removed.",
    "Frage den Server …": "Asking the server …",
    "Mit „Prüfen“ den Server ansehen oder gleich eine Instanz anlegen.":
        "Use “Check” to look at the server, or create an instance right away.",
    "Server wechseln": "Change server",
    "Beim bisherigen Server ist eine Instanz angemeldet. Mit dem Wechsel wird diese Anmeldung hier und in "
    "Bony's Agents verworfen – die Instanz bleibt auf dem alten Server bestehen, ist von hier aus aber nicht "
    "mehr erreichbar.\n\nServer trotzdem wechseln?":
        "An instance is signed in at the current server. Changing the server discards this sign-in here and in "
        "Bony's Agents – the instance stays on the old server but can no longer be reached from here.\n\n"
        "Change the server anyway?",
    "Der Server wurde nicht übernommen: {error}": "The server was not applied: {error}",
    "Instanz {id} auf dem Server löschen?": "Delete instance {id} on the server?",
    "Auf dem Server werden dabei alle deine Agent-PCs und dieser Rechner aus dem Bony-Netz entfernt und alle deine "
    "Kurse und Kursteilnahmen gelöscht – auch für Bony's Agents. Das lässt sich nicht rückgängig machen.":
        "This removes all your agent PCs and this computer from the Bony-Netz on the server and deletes all your "
        "courses and course memberships – for Bony's Agents too. This cannot be undone.",
    "Instanz gelöscht.": "Instance deleted.",
    "Anderes Tailscale-Konto ersetzen?": "Replace other Tailscale account?",
    "Auf diesem Rechner ist ein anderes Tailscale-Konto angemeldet ({netz}). Beim Verbinden mit dem Bony-Netz "
    "wird es ersetzt: Seine Verbindung endet. Es bleibt als Profil gespeichert und lässt sich mit "
    "„tailscale switch“ wieder einschalten.\n\nDein Rechner fragt nach dem Administrator-Passwort.":
        "Another Tailscale account is signed in on this computer ({netz}). Connecting to the Bony-Netz replaces "
        "it: its connection ends. It stays saved as a profile and can be turned back on with "
        "“tailscale switch”.\n\nYour computer asks for the administrator password.",
    "Ersetzen und verbinden": "Replace and connect",
    "Dieser Rechner ist jetzt im Bony-Netz.": "This computer is now in the Bony-Netz.",
    "Dieser Rechner ist vom Bony-Netz getrennt.": "This computer is disconnected from the Bony-Netz.",
    "Erst eine Instanz anlegen.": "Create an instance first.",
    "Tailscale installieren?": "Install Tailscale?",
    "Tailscale verbindet diesen Rechner mit dem Bony-Netz. Es wird aus der offiziellen Paketquelle von Tailscale "
    "({url}) installiert. Dein Rechner fragt nach dem Administrator-Passwort.":
        "Tailscale connects this computer to the Bony-Netz. It is installed from Tailscale's official package "
        "repository ({url}). Your computer asks for the administrator password.",
    "Tailscale installieren": "Install Tailscale",
    "Tailscale ist installiert.": "Tailscale is installed.",
    "Das offizielle Installationsprogramm von Tailscale wird geladen und gestartet ({url}).":
        "Tailscale's official installer is downloaded and started ({url}).",
    "Bitte Tailscale von der offiziellen Seite installieren: {url}":
        "Please install Tailscale from the official page: {url}",
    "Bony's Agents legt die Agent-PCs an, die hier erscheinen.": "Bony's Agents creates the agent PCs listed here.",
    "Bony's Agents installieren …": "Install Bony's Agents …",
    "Bony's Agents öffnen": "Open Bony's Agents",
}
model.EN.update(EN)


def _panel(title: str) -> tuple[Gtk.Box, Gtk.Box]:
    box = styled(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8), "bvpn-panel")
    head = Gtk.Box(spacing=10)
    head.pack_start(label(title, "bvpn-name"), True, True, 0)
    box.pack_start(head, False, False, 0)
    return box, head


def _set_class(widget, cls: str) -> None:
    ctx = widget.get_style_context()
    for c in ("bvpn-ok", "bvpn-off", "bvpn-warn", "bvpn-bad", "bvpn-muted"):
        ctx.remove_class(c)
    ctx.add_class(cls)


class NetzBereich(Gtk.ScrolledWindow):
    def __init__(self, win):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.win, self.app = win, win.app
        self.konto: Konto | None = None       # erst beim ersten Anzeigen laden
        self.busy = 0
        self.status_text = ""
        self.ts: netz.Tailscale | None = None
        self.zug = netz.KEINS
        self.liste: list[dict] | None = None
        self.switch_busy = False
        self.timer = 0
        self.refreshing = False
        self.refresh_again = False

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=14)
        self.add(body)
        body.pack_start(label(tr("Über das Bony-Netz erreichst du deine Agent-PCs aus Bony's Agents übers Internet. "
                                 "Bony's Agents benutzt dieselbe Anmeldung – eine dort angelegte Instanz gilt hier "
                                 "sofort."), "bvpn-muted", wrap=True), False, False, 0)

        # ----- Server
        panel, _ = _panel(tr("Server"))
        row = Gtk.Box(spacing=8)
        self.server = Gtk.Entry(placeholder_text=STANDARD_SERVER, activates_default=False)
        self.server.connect("activate", lambda *_: self._check())
        self.server.connect("changed", lambda *_: self.update())
        self.check_btn = Gtk.Button(label=tr("Prüfen"))
        self.check_btn.connect("clicked", lambda *_: self._check())
        row.pack_start(self.server, True, True, 0)
        row.pack_start(self.check_btn, False, False, 0)
        panel.pack_start(row, False, False, 0)
        self.server_info = label("", "bvpn-muted", "bvpn-small", wrap=True)
        panel.pack_start(self.server_info, False, False, 0)
        body.pack_start(panel, False, False, 0)

        # ----- Instanz
        panel, _ = _panel(tr("Instanz"))
        self.instance = label("", wrap=True, selectable=True)
        panel.pack_start(self.instance, False, False, 0)
        self.instance_hint = label("", "bvpn-muted", "bvpn-small", wrap=True)
        panel.pack_start(self.instance_hint, False, False, 0)
        row = Gtk.Box(spacing=8)
        self.create_btn = styled(Gtk.Button(label=tr("Instanz anlegen")), "bvpn-accent")
        self.create_btn.connect("clicked", lambda *_: self._create())
        self.delete_btn = Gtk.Button(label=tr("Instanz löschen …"))
        self.delete_btn.connect("clicked", lambda *_: self._delete())
        row.pack_start(self.create_btn, False, False, 0)
        row.pack_start(self.delete_btn, False, False, 0)
        panel.pack_start(row, False, False, 0)
        self.code_row = Gtk.Box(spacing=8)
        self.code = Gtk.Entry(placeholder_text=tr("Kursleiter-Code"))
        self.code.connect("activate", lambda *_: self._redeem())
        self.code.connect("changed", lambda *_: self.update())
        self.code_btn = Gtk.Button(label=tr("Einlösen"))
        self.code_btn.connect("clicked", lambda *_: self._redeem())
        self.code_row.pack_start(self.code, True, True, 0)
        self.code_row.pack_start(self.code_btn, False, False, 0)
        panel.pack_start(self.code_row, False, False, 0)
        self.status = label("", wrap=True)
        panel.pack_start(self.status, False, False, 0)
        body.pack_start(panel, False, False, 0)

        # ----- Dieser Rechner
        panel, head = _panel(tr("Diesen Rechner verbinden"))
        self.switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.switch_handler = self.switch.connect("notify::active", self._on_switch)
        head.pack_end(self.switch, False, False, 0)
        self.ts_state = label("", wrap=True)
        panel.pack_start(self.ts_state, False, False, 0)
        self.install_btn = Gtk.Button(label=tr("Tailscale installieren …"), halign=Gtk.Align.START)
        self.install_btn.connect("clicked", lambda *_: self.install_tailscale())
        self.install_btn.set_no_show_all(True)
        panel.pack_start(self.install_btn, False, False, 0)
        panel.pack_start(label(tr("Ist auf diesem Rechner ein anderes Tailscale-Konto angemeldet, wird es beim "
                                  "Einschalten ersetzt – vorher kommt eine Rückfrage."),
                               "bvpn-muted", "bvpn-small", wrap=True), False, False, 0)
        self.ks_hint = label("", "bvpn-warn", "bvpn-small", wrap=True)
        self.ks_hint.set_no_show_all(True)
        panel.pack_start(self.ks_hint, False, False, 0)
        body.pack_start(panel, False, False, 0)

        # ----- Geräte
        panel, _ = _panel(tr("Agenten-PCs"))
        self.devices = styled(Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE), "bvpn-list")
        panel.pack_start(self.devices, False, False, 0)
        self.devices_empty = label(tr("Noch keine Geräte im Netz."), "bvpn-muted", "bvpn-small", wrap=True)
        panel.pack_start(self.devices_empty, False, False, 0)
        self.agents_row = Gtk.Box(spacing=8)
        self.agents_row.pack_start(label(tr("Bony's Agents legt die Agent-PCs an, die hier erscheinen."),
                                         "bvpn-muted", "bvpn-small", wrap=True), True, True, 0)
        self.agents_btn = Gtk.Button(label=tr("Bony's Agents installieren …"))
        self.agents_btn.connect("clicked", lambda *_: AgentsDialog(self.win).run_dialog())
        self.agents_row.pack_start(self.agents_btn, False, False, 0)
        panel.pack_start(self.agents_row, False, False, 0)
        body.pack_start(panel, False, False, 0)

    # ---------------------------------------------------------------- Sichtbarkeit
    def shown(self) -> None:
        """Bereich wird angezeigt: Anmeldung laden, sofort und dann alle 30 Sekunden aktualisieren."""
        if self.konto is None:
            self.konto = Konto.laden()
            self.server.set_text(self.konto.server)
        self.update()
        self.refresh()
        if not self.timer:
            self.timer = GLib.timeout_add_seconds(netz.ABFRAGE_SEKUNDEN, self._tick)

    def _tick(self) -> bool:
        if self.win.area() != "netz":
            self.timer = 0
            return False   # Bereich verlassen: keine Abfragen mehr, bis er wieder angezeigt wird
        if self.win.get_visible():
            self.refresh()
        return True

    # ---------------------------------------------------------------- Hintergrund
    def _bg(self, fn: Callable[[], object], done: Callable[[object, BaseException | None], None],
            busy: bool = True) -> None:
        if busy:
            self.busy += 1
            self.update()

        def work():
            try:
                result, error = fn(), None
            except Exception as e:  # noqa: BLE001 – Fehler gehen an die Oberfläche
                result, error = None, e
            GLib.idle_add(finish, result, error)

        def finish(result, error) -> bool:
            if busy:
                self.busy -= 1
            done(result, error)
            self.update()
            return False

        threading.Thread(target=work, daemon=True).start()

    def refresh(self) -> None:
        """Tailscale immer (lokal), die Zentrale nur mit angemeldeter Instanz."""
        if self.refreshing:
            self.refresh_again = True
            return
        self.refreshing = True
        with_server = bool(self.konto and self.konto.angemeldet)

        def work():
            ts = netz.tailscale_status()
            if not with_server:
                return None, None, None, ts
            z = netz.zustand()
            liste, fehler = None, None
            if z.verbunden and z.konto.angemeldet:
                try:
                    liste = netz.geraete()
                except netz.FEHLER as e:
                    fehler = e
            return z, liste, fehler, ts

        def done(result, error) -> None:
            self.refreshing = False
            if self.refresh_again:
                self.refresh_again = False
                GLib.idle_add(lambda: self.refresh() and False)
            if error is not None:
                self.status_text = netz.fehlertext(error)
                return
            z, liste, fehler, ts = result
            self.ts = ts
            if z is not None:
                self._show_zustand(z)
                self.liste = liste
                if fehler is not None:
                    self.status_text = netz.fehlertext(fehler)
            else:
                self.konto = Konto.laden()
                self.liste = None
                if not self.konto.angemeldet:
                    self.status_text = ""
            self.zug = netz.zugehoerigkeit(ts, self.konto, self.liste)
            self._fill_devices()

        self._bg(work, done, busy=False)

    # ---------------------------------------------------------------- Anzeige
    def _server_changed(self) -> bool:
        return self.konto is not None and server_normalisieren(self.server.get_text()) != self.konto.server

    def update(self) -> None:
        konto = self.konto or Konto()
        idle = not self.busy
        signed_in = konto.angemeldet
        self.server.set_sensitive(idle)
        self.check_btn.set_sensitive(idle)
        if signed_in:
            self.instance.set_text(tr("Instanz-ID: {id} · Kursleiter: {kl}", id=netz.kurz_id(konto.instanz_id),
                                      kl=tr("ja") if konto.ist_kursleiter else tr("nein")))
            self.instance_hint.set_text("")
        else:
            self.instance.set_text(tr("Noch keine Instanz angelegt."))
            self.instance_hint.set_text(tr("Erst den neuen Server mit „Prüfen“ übernehmen.") if self._server_changed()
                                        else tr("Eine Instanz ist deine Anmeldung beim Server – einmal pro Rechner."))
        self.instance_hint.set_visible(bool(self.instance_hint.get_text()))
        self.create_btn.set_visible(not signed_in)
        self.create_btn.set_sensitive(idle and not self._server_changed())
        self.delete_btn.set_visible(signed_in)
        self.delete_btn.set_sensitive(idle)
        self.code_row.set_visible(signed_in and not konto.ist_kursleiter)
        self.code_btn.set_sensitive(idle and bool(self.code.get_text().strip()))
        if self.busy:
            text = tr("Frage den Server …")
        elif self.status_text:
            text = self.status_text
        elif not signed_in and not self.server_info.get_text():
            text = tr("Mit „Prüfen“ den Server ansehen oder gleich eine Instanz anlegen.")
        else:
            text = ""
        self.status.set_text(text)
        self.status.set_visible(bool(text))

        # Dieser Rechner
        ts = self.ts
        text, style = netz.anzeige(ts, self.zug)
        self.ts_state.set_text(text)
        _set_class(self.ts_state, f"bvpn-{style}")
        self.install_btn.set_visible(ts is not None and not ts.installiert)
        if not self.switch_busy:
            set_quietly(self.switch, self.switch_handler,
                        bool(ts and ts.verbunden and self.zug == netz.EIGEN))
        self.switch.set_sensitive(not self.switch_busy and signed_in and ts is not None and ts.installiert)
        status = self.app.status
        ks = status.killswitch_enabled and netz.TAILNET not in status.exceptions
        self.ks_hint.set_text(tr("Der Kill-Switch ist an: Ohne die Ausnahme {net} (Menü → Kill-Switch-Ausnahmen …) "
                                 "ist das Bony-Netz gesperrt.", net=netz.TAILNET) if ks else "")
        self.ks_hint.set_visible(ks)
        agents = host.agents_command() is not None
        self.agents_btn.set_label(tr("Bony's Agents öffnen") if agents else tr("Bony's Agents installieren …"))
        self.agents_row.set_visible(not agents)

    def _show_zustand(self, z: netz.Zustand) -> None:
        self.konto = z.konto
        self.status_text = "" if z.verbunden else z.text
        self.status.set_tooltip_text(z.detail or None)
        if z.info:
            version = z.info.get("version")
            self.server_info.set_text(z.info["server_name"] + (f" · Version {version}" if version else ""))
        else:
            self.server_info.set_text(z.detail)

    def _fill_devices(self) -> None:
        for child in self.devices.get_children():
            self.devices.remove(child)
        own = set(self.ts.ips) if self.ts else set()
        geraete = [netz.Geraet.aus(g, own) for g in self.liste or []]
        for g in geraete:
            row = Gtk.ListBoxRow(activatable=False, selectable=False)
            box = Gtk.Box(spacing=10)
            texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            title = label(g.name + (f" ({tr('dieser Rechner')})" if g.dieser_rechner else ""), selectable=True)
            title.set_ellipsize(Pango.EllipsizeMode.END)
            sub = label(g.zeile(), "bvpn-small", "bvpn-ok" if g.online else "bvpn-off", selectable=True)
            texts.pack_start(title, False, False, 0)
            texts.pack_start(sub, False, False, 0)
            box.pack_start(texts, True, True, 0)
            btn = Gtk.Button(label=tr("Entfernen …"), valign=Gtk.Align.CENTER)
            btn.connect("clicked", lambda _b, g=g: self._remove(g))
            box.pack_end(btn, False, False, 0)
            row.add(box)
            row.geraet = g
            self.devices.add(row)
        self.devices.show_all()
        self.devices.set_visible(bool(geraete))
        self.devices_empty.set_visible(not geraete and self.liste is not None)

    # ---------------------------------------------------------------- Server und Instanz
    def _check(self) -> None:
        if self.busy or self.konto is None:
            return
        address = server_normalisieren(self.server.get_text())
        self.server.set_text(address)
        if address == self.konto.server:
            self._bg(netz.zustand, self._after_zustand)
            return
        if self.konto.angemeldet and not self._confirm(
                tr("Server wechseln"),
                tr("Beim bisherigen Server ist eine Instanz angemeldet. Mit dem Wechsel wird diese Anmeldung hier und "
                   "in Bony's Agents verworfen – die Instanz bleibt auf dem alten Server bestehen, ist von hier aus "
                   "aber nicht mehr erreichbar.\n\nServer trotzdem wechseln?"), tr("Server wechseln")):
            self.server.set_text(self.konto.server)
            return

        def done(z, error) -> None:
            if error is not None:   # der alte Server bleibt
                self.server_info.set_text(netz.fehlertext(error))
                self.status_text = tr("Der Server wurde nicht übernommen: {error}", error=netz.fehlertext(error))
                return
            self._after_zustand(z, None)

        self._bg(lambda: netz.server_wechseln(address), done)

    def _after_zustand(self, z, error) -> None:
        if error is not None:
            self.status_text = netz.fehlertext(error)
            return
        self._show_zustand(z)
        self.refresh()

    def _create(self) -> None:
        server = self.konto.server

        def done(konto, error) -> None:
            if error is not None:
                self.status_text = netz.fehlertext(error)
                return
            self.konto = konto
            self.refresh()

        self._bg(lambda: netz.instanz_anlegen(server), done)

    def _delete(self) -> None:
        if not self._confirm(tr("Instanz {id} auf dem Server löschen?", id=netz.kurz_id(self.konto.instanz_id)),
                             tr("Auf dem Server werden dabei alle deine Agent-PCs und dieser Rechner aus dem "
                                "Bony-Netz entfernt und alle deine Kurse und Kursteilnahmen gelöscht – auch für "
                                "Bony's Agents. Das lässt sich nicht rückgängig machen."),
                             tr("Instanz löschen"), danger=True):
            return

        def done(konto, error) -> None:
            if error is not None:
                self.win.message(netz.fehlertext(error), error=True)
                return
            self.konto = konto
            self.liste = None
            self.status_text = tr("Instanz gelöscht.")
            self.refresh()

        self._bg(netz.instanz_loeschen, done)

    def _redeem(self) -> None:
        code = self.code.get_text().strip()
        if not code or self.busy:
            return

        def done(konto, error) -> None:
            if error is not None:
                self.win.message(netz.fehlertext(error), error=True)
                return
            self.konto = konto
            self.code.set_text("")
            self.win.message(tr("Kursleiter-Code eingelöst."))

        self._bg(lambda: netz.kursleiter_freischalten(code), done)

    def _remove(self, g: netz.Geraet) -> None:
        if not self._confirm(tr("„{name}“ aus dem Netz entfernen?", name=g.name),
                             tr("Das Gerät verliert sofort die Verbindung zum Bony-Netz und wird aus allen Kursen "
                                "genommen. Für einen neuen Beitritt braucht es einen neuen Geräteschlüssel."),
                             tr("Entfernen"), danger=True):
            return

        def done(_r, error) -> None:
            if error is not None:
                self.win.message(netz.fehlertext(error), error=True)
            else:
                self.win.message(tr("„{name}“ wurde entfernt.", name=g.name))
            self.refresh()

        self._bg(lambda: netz.geraet_entfernen(g.geraet_id), done)

    def _confirm(self, title: str, text: str, ok_text: str, danger: bool = False) -> bool:
        dlg = Gtk.MessageDialog(transient_for=self.win, modal=True,
                                message_type=Gtk.MessageType.WARNING if danger else Gtk.MessageType.QUESTION,
                                text=title)
        dlg.format_secondary_text(text)
        dlg.add_button(tr("Abbrechen"), Gtk.ResponseType.CANCEL)
        dlg.add_button(ok_text, Gtk.ResponseType.OK)
        dlg.set_default_response(Gtk.ResponseType.CANCEL if danger else Gtk.ResponseType.OK)
        ok = dlg.run() == Gtk.ResponseType.OK
        dlg.destroy()
        return ok

    # ---------------------------------------------------------------- Diesen Rechner verbinden
    def _on_switch(self, sw, _param) -> None:
        on = sw.get_active()
        if on and not (self.konto and self.konto.angemeldet):
            set_quietly(sw, self.switch_handler, False)
            self.win.message(tr("Erst eine Instanz anlegen."), error=True)
            return
        if on and self.ts is not None and not self.ts.installiert:
            set_quietly(sw, self.switch_handler, False)
            self.install_tailscale()
            return
        self.switch_busy = True
        self._switch(on, replace=False)

    def _switch(self, on: bool, replace: bool) -> None:
        run = self.app.client.run

        def work():
            if on:
                netz.verbinden(run, ersetzen=replace)
            else:
                netz.trennen(run)

        def done(_r, error) -> None:
            if isinstance(error, netz.Rueckfrage):
                if self._confirm(tr("Anderes Tailscale-Konto ersetzen?"),
                                 tr("Auf diesem Rechner ist ein anderes Tailscale-Konto angemeldet ({netz}). Beim "
                                    "Verbinden mit dem Bony-Netz wird es ersetzt: Seine Verbindung endet. Es bleibt "
                                    "als Profil gespeichert und lässt sich mit „tailscale switch“ wieder "
                                    "einschalten.\n\nDein Rechner fragt nach dem Administrator-Passwort.",
                                    netz=error.netz),
                                 tr("Ersetzen und verbinden"), danger=True):
                    self._switch(True, replace=True)
                    return
                error = None
            elif error is not None:
                self.win.message(netz.fehlertext(error), error=True)
            else:
                self.win.message(tr("Dieser Rechner ist jetzt im Bony-Netz.") if on
                                 else tr("Dieser Rechner ist vom Bony-Netz getrennt."))
            self.switch_busy = False
            self.refresh()

        self._bg(work, done, busy=False)

    def install_tailscale(self) -> None:
        """Tailscale aus der offiziellen Quelle: Linux über die Paketquelle (root wie bei WireGuard über
        ``updates.run_as_root``), Windows das Installationsprogramm, macOS die Download-Seite."""
        if sys.platform == "darwin":
            self.win.message(tr("Bitte Tailscale von der offiziellen Seite installieren: {url}", url=netz.DOWNLOAD_MAC))
            host.open_url(netz.DOWNLOAD_MAC)
            return
        if sys.platform.startswith("win"):
            if not ask(self.win, tr("Tailscale installieren?"),
                       tr("Das offizielle Installationsprogramm von Tailscale wird geladen und gestartet ({url}).",
                          url=netz.WINDOWS_INSTALLER), tr("Installieren")):
                return

            def work_win(log, _progress):
                path = netz.windows_installer_laden(tempfile.mkdtemp(prefix="bonys-vpn-tailscale-"))
                log(str(path))
                os.startfile(str(path))  # noqa: S606 – Windows fragt selbst nach (Benutzerkontensteuerung)

            TaskDialog(self.win, tr("Tailscale installieren"), work_win, done_text=tr("Fertig."),
                       on_done=self.refresh).start()
            return
        quelle = netz.paketquelle(netz.os_release())
        if quelle is None:
            self.win.message(tr("Tailscale gibt es aus der offiziellen Paketquelle nur für Debian, Ubuntu und davon "
                                "abgeleitete Systeme. Bitte von {url} installieren.", url=netz.DOWNLOAD), error=True)
            return
        if not ask(self.win, tr("Tailscale installieren?"),
                   tr("Tailscale verbindet diesen Rechner mit dem Bony-Netz. Es wird aus der offiziellen Paketquelle "
                      "von Tailscale ({url}) installiert. Dein Rechner fragt nach dem Administrator-Passwort.",
                      url=netz.PAKETQUELLE), tr("Installieren")):
            return
        script = netz.installations_skript(*quelle)

        def work(log, _progress):
            updates._check_rc(updates.run_as_root(script, log))

        TaskDialog(self.win, tr("Tailscale installieren"), work, done_text=tr("Tailscale ist installiert."),
                   on_done=self.refresh).start()

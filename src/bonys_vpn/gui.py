# SPDX-License-Identifier: GPL-3.0-or-later
"""bonys-vpn-gui – grafische Oberfläche von Bony's VPN (GTK 3 über python3-gi aus Debian).

Läuft als normaler Benutzer. Alles, was root braucht, geht wie bei der Kommandozeile über
``bonys-vpn-helper`` (pkexec). Die Logik ohne GTK steht in ``model.py``.

    bonys-vpn-gui            Fenster öffnen (läuft schon eine Instanz, zeigt sie ihr Fenster)
    bonys-vpn-gui --tray     nur das Symbol in der Leiste (Autostart nach der Anmeldung)

Eigener Rechner und Agent-PC unterscheiden sich nur in Hinweisen: Auf dem eigenen Rechner kommt vor dem
ersten Verbinden der Hinweis „gilt für den ganzen Rechner“, vor dem Kill-Switch eine Warnung, und das
Menü bietet Bony's Agents an. Im Agent-PC (Paket bonys-vpn-agentpc) entfällt das.

Tray: StatusNotifierItem über Ayatana-AppIndicator (Cinnamon, Xfce, KDE, MATE, LXQt), sonst das
klassische Infobereich-Symbol (XEmbed). Zeigt der Desktop keine Statussymbole (GNOME ohne
Erweiterung), läuft der Hintergrund ohne Symbol weiter und meldet nur Abbrüche.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator  # noqa: E402
except (ValueError, ImportError):
    AppIndicator = None

from . import APP_NAME, COPYRIGHT, REPO_URL, YOUTUBE_CHANNEL_URL, __version__  # noqa: E402
from . import cli, host, model, updates  # noqa: E402
from . import conf as wgconf  # noqa: E402
from .model import tr  # noqa: E402

APP_ID = "io.github.zappotex.BonysVPN"
model.EN.setdefault(host.INTRO, host.INTRO_EN)
model.EN.setdefault(host.KILLSWITCH_WARNING, host.KILLSWITCH_WARNING_EN)
HELP_URL = f"{REPO_URL}#readme"
DATA = Path(__file__).resolve().parent / "data"
ICON_DIR = DATA / "icons"
APP_ICON = DATA / "bonys-vpn.png"
TICK = 2            # Sekunden: Live-Werte aus /sys
DETAIL_REFRESH = 10  # Sekunden: Handshake/Endpunkt vom Helfer, solange das Fenster offen ist

# Farben von Bony's Agents (wie gui/style.py der Desktop-App)
CSS = """
.bvpn-window { background-color: #0d141d; color: #e8eef5; }
.bvpn-header { background-color: #162130; border-bottom: 1px solid #2b3c52; padding: 8px 12px; }
.bvpn-title { font-size: 15pt; font-weight: bold; color: #e8eef5; }
.bvpn-muted { color: #93a4b8; }
.bvpn-small { font-size: 9pt; }
.bvpn-panel { background-color: #162130; border: 1px solid #2b3c52; border-radius: 8px; padding: 12px; }
.bvpn-name { font-size: 14pt; font-weight: bold; }
.bvpn-key { color: #93a4b8; }
.bvpn-ok { color: #4cc38a; font-weight: bold; }
.bvpn-off { color: #93a4b8; font-weight: bold; }
.bvpn-warn { color: #ffd166; font-weight: bold; }
.bvpn-bad { color: #ef6b6b; font-weight: bold; }
.bvpn-list, .bvpn-list row { background-color: #162130; color: #e8eef5; }
.bvpn-list row { padding: 8px 10px; border-bottom: 1px solid #1f2d40; }
.bvpn-list row:selected { background-color: #1f2d40; box-shadow: inset 3px 0 #3fa9f5; }
.bvpn-window button { background-image: none; background-color: #1f2d40; color: #e8eef5;
                      border: 1px solid #2b3c52; box-shadow: none; text-shadow: none; }
.bvpn-window button:hover { background-color: #2b3c52; }
.bvpn-window button:disabled, .bvpn-window button:disabled label { color: #5d6e82; }
.bvpn-window button label { color: #e8eef5; }
.bvpn-window button.bvpn-accent { background-image: none; background-color: #f5a623; color: #0d141d;
                     border: 1px solid #d98c0f; font-weight: bold; padding: 6px 16px; }
.bvpn-window button.bvpn-accent:hover { background-color: #ffb840; }
.bvpn-window button.bvpn-accent label { color: #0d141d; }
.bvpn-window button.bvpn-danger { background-image: none; background-color: #ef6b6b; color: #0d141d;
                     border: 1px solid #c94f4f; font-weight: bold; padding: 6px 16px; }
.bvpn-window button.bvpn-danger label { color: #0d141d; }
.bvpn-drop { border: 2px dashed #3fa9f5; border-radius: 10px; padding: 24px; }
.bvpn-mono { font-family: monospace; }
/* Dialoge: manche Themes (Mint-Y) färben Texte in Dialogen grau – hier fest die helle Schrift */
.bvpn-window label.bvpn-text { color: #e8eef5; }
"""


class HelperClient:
    """Aufrufe des Root-Helfers (über pkexec, wie die Kommandozeile). Fehler → ``cli.CliError``."""

    def status(self) -> dict:
        return cli.call_json(["status"])

    def run(self, *args: str, data: bytes | None = None) -> str:
        return cli.call(list(args), data)


def label(text: str = "", *classes: str, xalign: float = 0.0, wrap: bool = False, selectable: bool = False):
    w = Gtk.Label(label=text, xalign=xalign)
    w.set_line_wrap(wrap)
    if wrap:
        w.set_max_width_chars(60)
    w.set_selectable(selectable)
    for c in classes:
        w.get_style_context().add_class(c)
    return w


def default_size(want=(820, 560)) -> tuple[int, int]:
    """Startgröße, höchstens so groß wie der freie Bereich des Bildschirms (ohne Leisten) – bei
    800×600 passt das Fenster sonst nicht ganz hinein."""
    w, h = want
    display = Gdk.Display.get_default()
    monitor = display and (display.get_primary_monitor() or display.get_monitor(0))
    if monitor is not None:
        area = monitor.get_workarea()
        w, h = min(w, area.width - 16), min(h, area.height - 48)  # Platz für Rahmen und Titelleiste
    return max(w, 560), max(h, 420)


def set_quietly(switch, handler: int, value: bool) -> None:
    """Schalter setzen, ohne seinen Handler auszulösen (sonst sähe es wie ein Klick aus)."""
    if switch.get_active() != value:
        switch.handler_block(handler)
        switch.set_active(value)
        switch.handler_unblock(handler)


def styled(widget, *classes: str):
    for c in classes:
        widget.get_style_context().add_class(c)
    return widget


# ---------------------------------------------------------------- Tray
def sni_available() -> bool:
    """Gibt es einen StatusNotifierWatcher (SNI-Symbole) auf dem Sitzungsbus?"""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        res = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                            "NameHasOwner", GLib.Variant("(s)", ("org.kde.StatusNotifierWatcher",)),
                            GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, 2000, None)
        return bool(res.unpack()[0])
    except GLib.Error:
        return False


class Tray:
    """Symbol in der Leiste mit Status und Schnellwahl. ``kind``: "sni", "xembed" oder None."""

    def __init__(self, app: App):
        self.app = app
        self.kind: str | None = None
        self.indicator = None
        self.icon = None
        self.menu = Gtk.Menu()
        if AppIndicator is not None and sni_available():
            ind = AppIndicator.Indicator.new("bonys-vpn", "bonys-vpn-disconnected",
                                             AppIndicator.IndicatorCategory.COMMUNICATIONS)
            ind.set_icon_theme_path(str(ICON_DIR))
            ind.set_title(tr("Bony's VPN"))
            ind.set_status(AppIndicator.IndicatorStatus.ACTIVE)
            ind.set_menu(self.menu)
            self.indicator, self.kind = ind, "sni"
        else:
            with _no_deprecation_warnings():
                icon = Gtk.StatusIcon.new_from_file(str(ICON_DIR / "bonys-vpn-disconnected.svg"))
                icon.set_title(tr("Bony's VPN"))
                icon.connect("activate", lambda *_: app.toggle_window())
                icon.connect("popup-menu", self._popup)
            self.icon, self.kind = icon, "xembed"
            GLib.timeout_add_seconds(3, self._check_embedded)
        self.update(model.Status())

    def _check_embedded(self) -> bool:
        with _no_deprecation_warnings():
            if self.icon is not None and not self.icon.is_embedded():
                self.icon.set_visible(False)
                self.icon, self.kind = None, None
                self.app.tray_unavailable()
        return False

    def _popup(self, icon, button, activate_time) -> None:
        self.menu.popup(None, None, Gtk.StatusIcon.position_menu, icon, button, activate_time)

    def update(self, status: model.Status) -> None:
        state = status.state()
        text = status.summary()
        title = tr("Bony's VPN")
        if self.indicator is not None:
            self.indicator.set_icon_full(f"bonys-vpn-{state}", text)
            self.indicator.set_title(f"{title} – {text}")
        if self.icon is not None:
            with _no_deprecation_warnings():
                self.icon.set_from_file(str(ICON_DIR / f"bonys-vpn-{state}.svg"))
                self.icon.set_tooltip_text(f"{title}\n{text}")
        self._build_menu(status, text)

    def _build_menu(self, status: model.Status, text: str) -> None:
        for child in self.menu.get_children():
            self.menu.remove(child)
        head = Gtk.MenuItem(label=text)
        head.set_sensitive(False)
        self.menu.append(head)
        self.menu.append(Gtk.SeparatorMenuItem())
        # Schnellwahl: ein Klick verbindet (trennt dabei einen anderen) bzw. trennt den aktiven Tunnel
        for t in status.tunnels:
            item = Gtk.CheckMenuItem(label=t.name)
            item.set_draw_as_radio(True)
            item.set_active(t.active)
            item.set_sensitive(not self.app.busy)
            item.connect("toggled", self._on_pick, t.name)
            self.menu.append(item)
        if status.tunnels:
            self.menu.append(Gtk.SeparatorMenuItem())
        show = Gtk.MenuItem(label=tr("Fenster öffnen"))
        show.connect("activate", lambda *_: self.app.show_window())
        self.menu.append(show)
        quit_ = Gtk.MenuItem(label=tr("Beenden"))
        quit_.connect("activate", lambda *_: self.app.quit())
        self.menu.append(quit_)
        self.menu.show_all()

    def _on_pick(self, item, name: str) -> None:
        if item.get_active():
            self.app.connect_tunnel(name)
        else:
            self.app.disconnect(name)


class _no_deprecation_warnings:
    """Gtk.StatusIcon ist in GTK 3 als veraltet markiert, aber der einzige Weg zum XEmbed-Infobereich."""

    def __enter__(self):
        import warnings

        self._ctx = warnings.catch_warnings()
        self._ctx.__enter__()
        warnings.simplefilter("ignore", DeprecationWarning)
        return self

    def __exit__(self, *exc):
        return self._ctx.__exit__(*exc)


# ---------------------------------------------------------------- Fenster
class Window(Gtk.ApplicationWindow):
    def __init__(self, app: App):
        super().__init__(application=app, title=tr("Bony's VPN"))
        self.app = app
        self.set_default_size(*default_size())
        self.set_size_request(560, 420)
        styled(self, "bvpn-window")
        if APP_ICON.exists():
            self.set_icon_from_file(str(APP_ICON))
        self.selected: str | None = None
        self.wanted: str | None = None
        self.updating = False
        self.ip_text = ""
        self.ip_for: str | None = None  # aktiver Tunnel, als die öffentliche IP abgefragt wurde

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root)
        root.pack_start(self._header(), False, False, 0)

        self.infobar = Gtk.InfoBar(show_close_button=True)
        self.infobar.connect("response", lambda bar, _r: bar.hide())
        self.info_label = label("", wrap=True)
        self.infobar.get_content_area().add(self.info_label)
        self.infobar.set_no_show_all(True)
        root.pack_start(self.infobar, False, False, 0)

        # Fehlende Pakete (eigener Rechner, wenn wireguard-tools/nftables nur empfohlen waren)
        self.setupbar = Gtk.InfoBar(message_type=Gtk.MessageType.WARNING)
        self.setup_label = label("", wrap=True)
        self.setupbar.get_content_area().add(self.setup_label)
        self.setupbar.add_button(tr("Installieren …"), 1)
        self.setupbar.connect("response", lambda *_: self.install_tools())
        self.setupbar.set_no_show_all(True)
        root.pack_start(self.setupbar, False, False, 0)

        # Neue Version
        self.updatebar = Gtk.InfoBar(message_type=Gtk.MessageType.INFO, show_close_button=True)
        self.update_label = label("", wrap=True)
        self.updatebar.get_content_area().add(self.update_label)
        self.updatebar.add_button(tr("Aktualisieren …"), 1)
        self.updatebar.connect("response", self._on_update_response)
        self.updatebar.set_no_show_all(True)
        root.pack_start(self.updatebar, False, False, 0)
        self.update_release: updates.Release | None = None

        self.stack = Gtk.Stack()
        root.pack_start(self.stack, True, True, 0)
        self.stack.add_named(self._empty_page(), "empty")
        self.stack.add_named(self._main_page(), "main")

        self.drag_dest_set(Gtk.DestDefaults.ALL, [Gtk.TargetEntry.new("text/uri-list", 0, 0)], Gdk.DragAction.COPY)
        self.connect("drag-data-received", self._on_drop)
        self.connect("delete-event", self._on_close)
        self.connect("destroy", lambda *_: setattr(app, "window", None))

    # ----- Aufbau
    def _header(self):
        box = styled(Gtk.Box(spacing=10), "bvpn-header")
        if APP_ICON.exists():
            pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(APP_ICON), 40, 40, True)
            box.pack_start(Gtk.Image.new_from_pixbuf(pix), False, False, 0)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        titles.pack_start(label(tr("Bony's VPN"), "bvpn-title"), False, False, 0)
        self.summary = label(tr("WireGuard-Tunnel verwalten"), "bvpn-muted")
        self.summary.set_ellipsize(Pango.EllipsizeMode.END)
        titles.pack_start(self.summary, False, False, 0)
        box.pack_start(titles, True, True, 0)
        imp = styled(Gtk.Button(label=tr("Importieren …")), "bvpn-accent")
        imp.connect("clicked", lambda *_: self.choose_import())
        box.pack_end(imp, False, False, 0)
        reload_ = Gtk.Button.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
        reload_.set_tooltip_text(tr("Neu laden"))
        reload_.connect("clicked", lambda *_: self.app.refresh())
        box.pack_end(reload_, False, False, 0)
        box.pack_end(self._menu_button(), False, False, 0)
        return box

    def _menu_button(self):
        btn = Gtk.MenuButton()
        btn.set_image(Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON))
        btn.set_tooltip_text(tr("Menü"))
        menu = Gtk.Menu()
        self.menu_items = {}
        entries = [("updates", tr("Nach Updates suchen …"), lambda: self.app.check_updates(manual=True))]
        if not self.app.agentpc:  # im Agent-PC ergibt Bony's Agents keinen Sinn
            entries += [("agents", tr("Bony's Agents – virtuelle Agent-PCs …"), self.show_agents),
                        ("exceptions", tr("Kill-Switch-Ausnahmen …"), self.edit_exceptions)]
        entries += [("help", tr("Hilfe"), lambda: host.open_url(HELP_URL)),
                    ("about", tr("Über Bony's VPN"), self.show_about)]
        for key, text, fn in entries:
            item = Gtk.MenuItem(label=text)
            item.connect("activate", lambda _i, f=fn: f())
            menu.append(item)
            self.menu_items[key] = item
        menu.show_all()
        btn.set_popup(menu)
        return btn

    def _empty_page(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        drop = styled(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10), "bvpn-drop")
        drop.pack_start(label(tr("Noch keine Tunnel"), "bvpn-name", xalign=0.5), False, False, 0)
        drop.pack_start(label(tr("WireGuard-Konfiguration (.conf oder .zip) hierher ziehen oder „Importieren …“ "
                                 "wählen."), "bvpn-muted", xalign=0.5, wrap=True), False, False, 0)
        btn = styled(Gtk.Button(label=tr("Importieren …"), halign=Gtk.Align.CENTER), "bvpn-accent")
        btn.connect("clicked", lambda *_: self.choose_import())
        drop.pack_start(btn, False, False, 6)
        outer.pack_start(drop, False, False, 0)
        self.ks_empty = self._killswitch_panel()
        outer.pack_start(self.ks_empty["box"], False, False, 16)
        return outer

    def _main_page(self):
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, position=210)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.listbox = styled(Gtk.ListBox(), "bvpn-list")
        self.listbox.connect("row-selected", self._on_row)
        scroll.add(self.listbox)
        scroll.set_size_request(170, -1)
        paned.pack1(scroll, False, False)

        right = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=14)
        right.add(body)
        paned.pack2(right, True, False)

        # Kopf: Name, Status, Verbinden
        top = Gtk.Box(spacing=10)
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.d_name = label("", "bvpn-name")
        self.d_name.set_ellipsize(Pango.EllipsizeMode.END)
        self.d_state = label("")
        names.pack_start(self.d_name, False, False, 0)
        names.pack_start(self.d_state, False, False, 0)
        top.pack_start(names, True, True, 0)
        self.connect_btn = Gtk.Button(label=tr("Verbinden"), valign=Gtk.Align.CENTER)
        self.connect_btn.connect("clicked", self._on_connect)
        top.pack_end(self.connect_btn, False, False, 0)
        body.pack_start(top, False, False, 0)

        # Aktionen – FlowBox bricht bei schmalem Fenster um
        self.actions = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4,
                                   column_spacing=6, row_spacing=6, homogeneous=False)
        self.action_btns = {}
        for key, text, fn in (("edit", tr("Bearbeiten …"), self.edit), ("rename", tr("Umbenennen …"), self.rename),
                              ("export", tr("Exportieren …"), self.export), ("delete", tr("Löschen …"), self.delete)):
            b = Gtk.Button(label=text)
            b.connect("clicked", lambda _b, f=fn: self.selected and f(self.selected))
            self.action_btns[key] = b
            self.actions.add(b)
        body.pack_start(self.actions, False, False, 0)
        self.d_note = label("", "bvpn-muted", "bvpn-small", wrap=True)
        body.pack_start(self.d_note, False, False, 0)

        # Details
        panel = styled(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8), "bvpn-panel")
        self.grid = Gtk.Grid(column_spacing=14, row_spacing=6)
        self.keys = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)  # Spalte der Feldnamen bündig
        panel.pack_start(self.grid, False, False, 0)
        ip_row = Gtk.Box(spacing=14)
        ip_key = label(tr("Öffentliche IP"), "bvpn-key")
        self.keys.add_widget(ip_key)
        ip_row.pack_start(ip_key, False, False, 0)
        self.ip_value = label("–", selectable=True)
        ip_row.pack_start(self.ip_value, True, True, 0)
        self.ip_btn = Gtk.Button(label=tr("Prüfen"))
        self.ip_btn.set_tooltip_text(tr("fragt {service}", service=model.IP_SERVICE_NAME))
        self.ip_btn.connect("clicked", self._on_ip)
        ip_row.pack_end(self.ip_btn, False, False, 0)
        panel.pack_start(ip_row, False, False, 0)
        panel.pack_start(label(tr("fragt {service}", service=model.IP_SERVICE_NAME), "bvpn-muted", "bvpn-small"),
                         False, False, 0)
        self.d_hooks = label("", "bvpn-warn", wrap=True)
        panel.pack_start(self.d_hooks, False, False, 0)
        body.pack_start(panel, False, False, 0)

        # Autostart
        auto = styled(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4), "bvpn-panel")
        row = Gtk.Box(spacing=10)
        row.pack_start(label(tr("Automatisch verbinden"), "bvpn-name"), True, True, 0)
        self.auto_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.auto_handler = self.auto_switch.connect("notify::active", self._on_autostart)
        row.pack_end(self.auto_switch, False, False, 0)
        auto.pack_start(row, False, False, 0)
        auto.pack_start(label(tr("Verbindet diesen Tunnel beim Hochfahren des Rechners (immer nur ein Tunnel)."),
                              "bvpn-muted", "bvpn-small", wrap=True), False, False, 0)
        body.pack_start(auto, False, False, 0)

        self.ks_main = self._killswitch_panel()
        body.pack_start(self.ks_main["box"], False, False, 0)
        return paned

    def _killswitch_panel(self) -> dict:
        box = styled(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4), "bvpn-panel")
        row = Gtk.Box(spacing=10)
        row.pack_start(label(tr("Kill-Switch"), "bvpn-name"), True, True, 0)
        sw = Gtk.Switch(valign=Gtk.Align.CENTER)
        handler = sw.connect("notify::active", self._on_killswitch)
        row.pack_end(sw, False, False, 0)
        box.pack_start(row, False, False, 0)
        box.pack_start(label(tr("Ohne verbundenen Tunnel kommt kein Programm ins Internet – auch nicht, wenn der "
                                "Tunnel abbricht oder nach einem Neustart."), "bvpn-muted", "bvpn-small", wrap=True),
                       False, False, 0)
        nets = label("", "bvpn-muted", "bvpn-small", wrap=True)
        box.pack_start(nets, False, False, 0)
        return {"box": box, "switch": sw, "nets": nets, "handler": handler}

    # ----- Anzeige
    def show_status(self, status: model.Status) -> None:
        was, self.updating = self.updating, True
        try:
            active = status.active.name if status.active else None
            if active != self.ip_for:  # Verbindung gewechselt: alte öffentliche IP gilt nicht mehr
                self.ip_text, self.ip_for = "", active
            self.summary.set_text(status.summary())
            for ks in (self.ks_empty, self.ks_main):
                if "killswitch" not in self.app.pending:
                    set_quietly(ks["switch"], ks["handler"], status.killswitch_enabled)
                ks["nets"].set_text(tr("Immer erreichbar: {nets}", nets=", ".join(status.exceptions))
                                    if status.exceptions else "")
            if not status.tunnels:
                self.stack.set_visible_child_name("empty")
                self.selected = None
                return
            self.stack.set_visible_child_name("main")
            names = status.names()
            if self.wanted in names:  # neu importiert/umbenannt: erst zeigen, wenn es ihn gibt
                self.selected, self.wanted = self.wanted, None
            elif self.selected not in names:
                self.selected = status.active.name if status.active else names[0]
            self._fill_list(status)
            self.show_details(status)
        finally:
            self.updating = was

    def _fill_list(self, status: model.Status) -> None:
        rows = {r.tunnel: r for r in self.listbox.get_children()}
        if list(rows) != status.names():
            for r in self.listbox.get_children():
                self.listbox.remove(r)
            rows = {}
            for t in status.tunnels:
                r = Gtk.ListBoxRow()
                r.tunnel = t.name
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                r.title = label(t.name)
                r.title.set_ellipsize(Pango.EllipsizeMode.END)
                r.sub = label("", "bvpn-small")
                r.sub.set_ellipsize(Pango.EllipsizeMode.END)  # sonst schiebt ein langer Text die Spalte breiter
                box.pack_start(r.title, False, False, 0)
                box.pack_start(r.sub, False, False, 0)
                r.add(box)
                self.listbox.add(r)
                rows[t.name] = r
            self.listbox.show_all()
        for t in status.tunnels:
            r = rows[t.name]
            busy = self.app.busy.get(t.name)
            text, cls = (busy, "bvpn-warn") if busy else self._state_text(t)
            if t.autostart and not busy:
                text += " · " + tr("Automatisch verbinden").lower()
            r.sub.set_text(text)
            for c in ("bvpn-ok", "bvpn-off", "bvpn-warn", "bvpn-bad"):
                r.sub.get_style_context().remove_class(c)
            r.sub.get_style_context().add_class(cls)
            if t.name == self.selected and self.listbox.get_selected_row() is not r:
                self.listbox.select_row(r)

    def _state_text(self, t: model.Tunnel) -> tuple[str, str]:
        if t.error:
            return tr("Fehler"), "bvpn-bad"
        if t.active:
            return (tr("Server antwortet nicht"), "bvpn-warn") if t.stale() else (tr("verbunden"), "bvpn-ok")
        return tr("getrennt"), "bvpn-off"

    def show_details(self, status: model.Status) -> None:
        t = status.get(self.selected)
        if t is None:
            return
        was, self.updating = self.updating, True
        try:
            self.d_name.set_text(t.name)
            busy = self.app.busy.get(t.name)
            text, cls = (busy, "bvpn-warn") if busy else self._state_text(t)
            self.d_state.set_text(text)
            for c in ("bvpn-ok", "bvpn-off", "bvpn-warn", "bvpn-bad"):
                self.d_state.get_style_context().remove_class(c)
            self.d_state.get_style_context().add_class(cls)
            ctx = self.connect_btn.get_style_context()
            ctx.remove_class("bvpn-accent")
            ctx.remove_class("bvpn-danger")
            if t.active:
                self.connect_btn.set_label(tr("Trennen"))
                ctx.add_class("bvpn-danger")
            else:
                self.connect_btn.set_label(tr("Verbinden"))
                ctx.add_class("bvpn-accent")
            nm = t.name in self.app.nm_active
            self.connect_btn.set_sensitive(not busy and not t.error and not (nm and t.active))
            for key in ("rename", "export", "delete"):
                self.action_btns[key].set_sensitive(t.managed and not busy)
            self.action_btns["edit"].set_sensitive(not busy)
            notes = []
            if not t.managed:
                notes.append(tr("nicht von Bony's VPN angelegt – nur verbinden und trennen"))
            if t.error:
                notes.append(tr("Fehler in der Konfiguration: {error}", error=t.error))
            if t.name in self.app.nm_active:
                notes.append(tr("Wird vom NetworkManager verwaltet – trennen über das Netzwerk-Symbol der Leiste."))
            if t.dns and self.app.no_resolvconf:
                notes.append(tr("Für das DNS-Feld fehlt „resolvconf“ (kommt mit systemd-resolved oder openresolv)."))
            self.d_note.set_text("\n".join(notes))
            self.d_note.set_visible(bool(notes))
            self.d_hooks.set_text(tr("Achtung: führt beim Verbinden Befehle als root aus ({hooks}).",
                                     hooks=", ".join(t.hooks)) if t.hooks else "")
            self.d_hooks.set_visible(bool(t.hooks))
            if "autostart" not in self.app.pending:
                set_quietly(self.auto_switch, self.auto_handler, t.autostart)
            self.auto_switch.set_sensitive(not busy and "autostart" not in self.app.pending)
            self._fill_grid(t)
            self.ip_value.set_text(self.ip_text or "–")
        finally:
            self.updating = was

    def _fill_grid(self, t: model.Tunnel) -> None:
        rows = t.detail_rows()
        children = self.grid.get_children()
        if len(children) != 2 * len(rows):
            for c in children:
                self.grid.remove(c)
            for i, (k, v) in enumerate(rows):
                key_label = label(k, "bvpn-key")
                self.keys.add_widget(key_label)
                self.grid.attach(key_label, 0, i, 1, 1)
                val = label(v, selectable=True)
                val.set_line_wrap(True)
                self.grid.attach(val, 1, i, 1, 1)
            self.grid.show_all()
        else:
            for i, (k, v) in enumerate(rows):
                self.grid.get_child_at(0, i).set_text(k)
                self.grid.get_child_at(1, i).set_text(v)

    def message(self, text: str, error: bool = False) -> None:
        self.infobar.set_message_type(Gtk.MessageType.ERROR if error else Gtk.MessageType.INFO)
        self.info_label.set_text(text)
        self.info_label.show()
        self.infobar.show()
        if not error:
            shown = text

            def hide():
                if self.info_label.get_text() == shown:
                    self.infobar.hide()
                return False

            GLib.timeout_add_seconds(6, hide)

    # ----- Ereignisse
    def _on_close(self, *_):
        if self.app.keep_running():
            self.hide()
            if self.app.tray is not None and not updates.load_settings().get("tray_hint_seen"):
                updates.set_setting("tray_hint_seen", True)
                self.app.notify(tr("Bony's VPN läuft weiter"),
                                tr("Das Symbol in der Leiste zeigt den Status. Beenden über das Symbol → „Beenden“."))
            return True
        return False

    def _on_row(self, _lb, row) -> None:
        if row is None or self.updating:
            return
        if row.tunnel != self.selected:
            self.selected = row.tunnel
            self.ip_text = ""
            self.show_details(self.app.status)

    def _on_connect(self, _btn) -> None:
        t = self.app.status.get(self.selected)
        if t is None:
            return
        if t.active:
            self.app.disconnect(t.name)
        elif self.confirm_whole_computer() and self.nm_free():
            self.app.connect_tunnel(t.name)

    def confirm_whole_computer(self) -> bool:
        """Eigener Rechner: Hinweis „gilt für den ganzen Rechner“ – bis „Nicht mehr anzeigen“ angehakt wurde."""
        if self.app.agentpc or updates.load_settings().get("intro_seen"):
            return True
        dlg = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.INFO,
                                text=tr("Bony's VPN auf diesem Rechner"))
        dlg.format_secondary_text(tr(host.INTRO))
        again = Gtk.CheckButton(label=tr("Nicht mehr anzeigen"))
        dlg.get_message_area().pack_start(again, False, False, 0)
        dlg.add_button(tr("Abbrechen"), Gtk.ResponseType.CANCEL)
        dlg.add_button(tr("Weiter"), Gtk.ResponseType.OK)
        dlg.set_default_response(Gtk.ResponseType.OK)
        dlg.show_all()
        ok = dlg.run() == Gtk.ResponseType.OK
        if ok and again.get_active():
            updates.set_setting("intro_seen", True)
        dlg.destroy()
        return ok

    def nm_free(self) -> bool:
        """Läuft ein WireGuard-Tunnel des NetworkManager, bissen sich beide (gleiche Routing-Tabelle 51820)."""
        self.app.nm_active = host.nm_wireguard() if not self.app.agentpc else set()
        others = sorted(self.app.nm_active)
        if not others:
            return True
        self.message(tr("Gerade ist „{names}“ über den NetworkManager verbunden. Bitte zuerst dort trennen "
                        "(Netzwerk-Symbol in der Leiste) – zwei Tunnel für den ganzen Rechner stören sich.",
                        names=", ".join(others)), error=True)
        return False

    def _on_autostart(self, sw, _param) -> None:
        if self.updating or not self.selected:
            return
        name, on = self.selected, sw.get_active()
        self.app.action(name, None, ("autostart", name, "on" if on else "off"), pending="autostart")

    def _on_killswitch(self, sw, _param) -> None:
        if self.updating:
            return
        on = sw.get_active()
        if on and not self.app.agentpc and not self.confirm_killswitch():
            for ks in (self.ks_empty, self.ks_main):
                set_quietly(ks["switch"], ks["handler"], False)
            return
        for ks in (self.ks_empty, self.ks_main):  # beide Schalter (leere und volle Ansicht) gleich
            if ks["switch"] is not sw:
                set_quietly(ks["switch"], ks["handler"], on)
        self.app.action(None, tr("Kill-Switch an: Ohne Tunnel ist das Internet jetzt gesperrt.") if on
                        else tr("Kill-Switch aus."), self.app.killswitch_args(on), pending="killswitch")

    def confirm_killswitch(self) -> bool:
        dlg = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.WARNING,
                                text=tr("Kill-Switch"))
        dlg.format_secondary_text(tr(host.KILLSWITCH_WARNING))
        dlg.add_button(tr("Abbrechen"), Gtk.ResponseType.CANCEL)
        dlg.add_button(tr("Einschalten"), Gtk.ResponseType.OK)
        dlg.set_default_response(Gtk.ResponseType.CANCEL)
        ok = dlg.run() == Gtk.ResponseType.OK
        dlg.destroy()
        return ok

    def edit_exceptions(self) -> None:
        """Netze, die trotz Kill-Switch erreichbar bleiben (Drucker, NAS im Heimnetz)."""
        dlg = Gtk.Dialog(title=tr("Kill-Switch-Ausnahmen"), transient_for=self, modal=True)
        dlg.add_button(tr("Abbrechen"), Gtk.ResponseType.CANCEL)
        dlg.add_button(tr("Speichern"), Gtk.ResponseType.OK)
        dlg.set_default_response(Gtk.ResponseType.OK)
        box = dlg.get_content_area()
        box.set_spacing(6)
        box.set_border_width(12)
        box.add(label(tr("Netze oder Adressen, die auch ohne Tunnel erreichbar bleiben, durch Komma getrennt "
                         "(z. B. 192.168.178.0/24 für das Heimnetz). Leer = keine."), wrap=True))
        entry = Gtk.Entry(text=", ".join(self.app.status.exceptions), activates_default=True)
        box.add(entry)
        hint = label("", "bvpn-bad", "bvpn-small", wrap=True)
        box.add(hint)
        dlg.show_all()
        nets = None
        while dlg.run() == Gtk.ResponseType.OK:
            try:
                nets = host.parse_exceptions(entry.get_text())
                break
            except ValueError as e:
                hint.set_text(str(e))
        dlg.destroy()
        if nets is None:
            return
        if self.app.status.killswitch_enabled:
            args = ["killswitch", "on", *[x for n in nets for x in ("--exception", n)]] if nets \
                else ["killswitch", "on", "--no-exceptions"]
            self.app.action(None, tr("Ausnahmen gespeichert."), tuple(args), pending="killswitch")
        else:
            # Ausnahmen gelten erst mit dem Kill-Switch – gespeichert werden sie beim Einschalten
            self.app.pending_exceptions = nets
            self.message(tr("Die Ausnahmen gelten, sobald der Kill-Switch an ist."))

    # ----- Menü
    def show_about(self) -> None:
        dlg = Gtk.AboutDialog(transient_for=self, modal=True)
        dlg.set_program_name(APP_NAME)
        dlg.set_version(__version__)
        dlg.set_comments(tr("WireGuard-Tunnel verwalten – mit Kill-Switch, für den eigenen Rechner und die "
                            "Agent-PCs von Bony's Agents."))
        dlg.set_website(REPO_URL)
        dlg.set_website_label("GitHub: zappotex/bonys-vpn")
        dlg.set_copyright(COPYRIGHT)
        dlg.set_license_type(Gtk.License.GPL_3_0)
        if APP_ICON.exists():
            dlg.set_logo(GdkPixbuf.Pixbuf.new_from_file_at_scale(str(APP_ICON), 96, 96, True))
        dlg.run()
        dlg.destroy()

    def show_agents(self) -> None:
        AgentsDialog(self).run_dialog()

    def show_update(self, rel: updates.Release) -> None:
        self.update_release = rel
        how = (tr("Sie kommt auch über die Aktualisierungsverwaltung.") if updates.apt_source_configured()
               else tr("Das Paket wird vor der Installation auf Signatur und Prüfsumme geprüft."))
        self.update_label.set_text(tr("Bony's VPN {new} ist da (installiert: {old}). {how}",
                                      new=rel.version, old=__version__, how=how))
        self.update_label.show()
        self.updatebar.show()

    def _on_update_response(self, bar, response) -> None:
        bar.hide()
        rel = self.update_release
        if response != 1 or rel is None:
            if rel is not None and response == Gtk.ResponseType.CLOSE:
                updates.set_setting("update_skip", rel.version)  # diese Version nicht mehr melden
            return
        if not ask(self, tr("Bony's VPN aktualisieren?"),
                   tr("Version {v} wird installiert – aus der Paketquelle, sonst aus dem offiziellen Release "
                      "auf GitHub (Signatur und Prüfsumme werden geprüft). Dein Rechner fragt nach dem "
                      "Administrator-Passwort. Tunnel und Einstellungen bleiben erhalten.", v=rel.version),
                   tr("Aktualisieren")):
            return
        TaskDialog(self, tr("Bony's VPN aktualisieren"),
                   lambda log, progress: updates.install_package(updates.VPN, log, rel=rel, upgrade=True,
                                                                 progress=progress),
                   done_text=tr("Fertig. Die neue Version gilt nach einem Neustart von Bony's VPN."),
                   restart=True).start()

    def install_tools(self) -> None:
        missing = host.missing_tools()
        if not missing:
            self.setupbar.hide()
            return
        if not ask(self, tr("WireGuard installieren?"),
                   tr("Installiert {pkgs} aus den Paketquellen deines Systems. Dein Rechner fragt nach dem "
                      "Administrator-Passwort.", pkgs=", ".join(missing)), tr("Installieren")):
            return

        def work(log, _progress):
            rc = updates.run_as_root(host.install_tools_script(missing), log)
            updates._check_rc(rc)

        TaskDialog(self, tr("WireGuard installieren"), work, done_text=tr("Fertig."),
                   on_done=lambda: (self.update_setup(), self.app.refresh())).start()

    def update_setup(self) -> None:
        missing = host.missing_tools()
        if missing:
            self.setup_label.set_text(tr("Für Bony's VPN fehlen noch Pakete: {pkgs}.", pkgs=", ".join(missing)))
            self.setup_label.show()
            self.setupbar.show()
        else:
            self.setupbar.hide()

    def _on_ip(self, _btn) -> None:
        self.ip_text = tr("wird abgefragt …")
        self.ip_value.set_text(self.ip_text)
        self.ip_btn.set_sensitive(False)

        def done(ip: str | None) -> None:
            self.ip_text = ip or tr("nicht erreichbar")
            self.ip_value.set_text(self.ip_text)
            self.ip_btn.set_sensitive(True)

        def work():
            try:
                ip = model.public_ip()
            except (OSError, ValueError):
                ip = None
            GLib.idle_add(done, ip)

        threading.Thread(target=work, daemon=True).start()

    def _on_drop(self, _w, _ctx, _x, _y, data, _info, _time) -> None:
        paths = [Gio.File.new_for_uri(u).get_path() for u in data.get_uris() or []]
        self.import_paths([Path(p) for p in paths if p])

    # ----- Import
    def choose_import(self) -> None:
        dlg = Gtk.FileChooserNative.new(tr("Konfigurationen importieren"), self, Gtk.FileChooserAction.OPEN,
                                        tr("Öffnen"), tr("Abbrechen"))
        dlg.set_select_multiple(True)
        dlg.set_current_folder(GLib.get_home_dir())  # nicht das Arbeitsverzeichnis (beim Autostart „/“)
        flt = Gtk.FileFilter()
        flt.set_name(tr("WireGuard-Konfigurationen") + " (.conf, .zip)")
        for pat in ("*.conf", "*.CONF", "*.zip", "*.ZIP"):
            flt.add_pattern(pat)
        dlg.add_filter(flt)
        if dlg.run() == Gtk.ResponseType.ACCEPT:
            self.import_paths([Path(f.get_path()) for f in dlg.get_files() if f.get_path()])
        dlg.destroy()

    def import_paths(self, paths: list[Path]) -> None:
        if paths and not self.confirm_whole_computer():
            return
        items: list[tuple[str, bytes]] = []
        errors = []
        for p in paths:
            try:
                items += cli.confs_from(p)
            except cli.CliError as e:
                errors.append(str(e))
        if errors:
            self.message("\n".join(errors), error=True)
        self._import_next(items, [], errors)

    def _import_next(self, items: list[tuple[str, bytes]], done: list[str], errors: list[str]) -> None:
        if not items:
            if done:
                self.wanted = done[-1]  # zuletzt importierten Tunnel zeigen
                self.app.refresh()
                if not errors:
                    self.message(", ".join(tr("„{name}“ importiert.", name=n) for n in done))
            return
        stem, data = items[0]
        rest = items[1:]
        try:
            parsed = wgconf.parse(wgconf.decode(data))
        except wgconf.ConfError as e:
            errors.append(f"{stem}: {e}")
            self.message("\n".join(errors), error=True)
            return self._import_next(rest, done, errors)
        existing = self.app.status.names() + done
        name = model.suggest_name(stem)
        replace = False
        if name in existing:
            choice = self._ask_collision(name, self.app.status.get(name))
            if choice == "skip":
                return self._import_next(rest, done, errors)
            if choice == "replace":
                replace = True
            else:
                name = self.ask_name(tr("Name des Tunnels"), model.suggest_name(stem, existing), existing)
                if not name:
                    return self._import_next(rest, done, errors)
        allow_hooks = False
        if parsed.hooks:
            if not self.confirm_hooks(name, parsed.hooks):
                return self._import_next(rest, done, errors)
            allow_hooks = True
        args = ["import", name] + (["--allow-hooks"] if allow_hooks else []) + (["--replace"] if replace else [])

        def ok(_out):
            self._import_next(rest, done + [name], errors)

        def fail(msg):
            errors.append(f"{name}: {msg}")
            self.message("\n".join(errors), error=True)
            self._import_next(rest, done, errors)

        self.app.watch.expect()
        self.app.call(args, data, ok, fail)

    def _ask_collision(self, name: str, existing: model.Tunnel | None) -> str:
        dlg = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                text=tr("Den Tunnel „{name}“ gibt es schon.", name=name))
        dlg.add_button(tr("Überspringen"), 1)
        dlg.add_button(tr("Anderer Name"), 2)
        if existing is None or existing.managed:
            dlg.add_button(tr("Ersetzen"), 3)
        r = dlg.run()
        dlg.destroy()
        return {2: "rename", 3: "replace"}.get(r, "skip")

    def ask_name(self, title: str, initial: str, taken: list[str], ok_text: str | None = None) -> str | None:
        dlg = Gtk.Dialog(title=title, transient_for=self, modal=True)
        dlg.add_button(tr("Abbrechen"), Gtk.ResponseType.CANCEL)
        okb = dlg.add_button(ok_text or tr("Speichern"), Gtk.ResponseType.OK)
        dlg.set_default_response(Gtk.ResponseType.OK)
        box = dlg.get_content_area()
        box.set_spacing(6)
        box.set_border_width(12)
        box.add(label(tr("Name des Tunnels")))
        entry = Gtk.Entry(text=initial, activates_default=True, max_length=15)
        box.add(entry)
        hint = label(tr("1–15 Zeichen: A–Z, a–z, 0–9 und _ = + . -"), "bvpn-small", wrap=True)
        box.add(hint)

        def check(*_):
            n = entry.get_text()
            err = model.name_error(n) or (tr("Den Tunnel „{name}“ gibt es schon.", name=n) if n in taken else "")
            hint.set_text(err or tr("1–15 Zeichen: A–Z, a–z, 0–9 und _ = + . -"))
            okb.set_sensitive(not err)

        entry.connect("changed", check)
        check()
        dlg.show_all()
        result = None
        while dlg.run() == Gtk.ResponseType.OK:
            if okb.get_sensitive():
                result = entry.get_text()
                break
        dlg.destroy()
        return result

    def confirm_hooks(self, name: str, hooks: list[str]) -> bool:
        dlg = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.WARNING,
                                text=tr("Befehle in der Konfiguration"))
        dlg.format_secondary_text(tr(
            "„{name}“ enthält {hooks}. Diese Zeilen sind Shell-Befehle, die beim Verbinden und Trennen "
            "als root laufen. Eine fremde Datei kann damit den ganzen Rechner übernehmen.\n\n"
            "Nur übernehmen, wenn du der Datei vertraust.", name=name, hooks=", ".join(hooks)))
        dlg.add_button(tr("Abbrechen"), Gtk.ResponseType.CANCEL)
        dlg.add_button(tr("Trotzdem übernehmen"), Gtk.ResponseType.OK)
        dlg.set_default_response(Gtk.ResponseType.CANCEL)
        r = dlg.run()
        dlg.destroy()
        return r == Gtk.ResponseType.OK

    # ----- Tunnel-Aktionen
    def rename(self, name: str) -> None:
        t = self.app.status.get(name)
        if t and t.active:
            self.message(tr("Bitte zuerst trennen."), error=True)
            return
        new = self.ask_name(tr("„{name}“ umbenennen", name=name), name,
                            [n for n in self.app.status.names() if n != name], tr("Umbenennen"))
        if new and new != name:
            # Auswahl erst nach Erfolg wechseln – vorher gibt es den neuen Namen in der Liste noch nicht
            self.app.action(name, None, ("rename", name, new), on_ok=lambda: setattr(self, "wanted", new))

    def delete(self, name: str) -> None:
        dlg = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.WARNING,
                                text=tr("„{name}“ löschen?", name=name))
        dlg.format_secondary_text(tr("Die Konfiguration mit dem privaten Schlüssel wird gelöscht. "
                                     "Das lässt sich nicht rückgängig machen."))
        dlg.add_button(tr("Abbrechen"), Gtk.ResponseType.CANCEL)
        dlg.add_button(tr("Löschen"), Gtk.ResponseType.OK)
        dlg.set_default_response(Gtk.ResponseType.CANCEL)
        r = dlg.run()
        dlg.destroy()
        if r == Gtk.ResponseType.OK:
            self.app.action(name, None, ("delete", name))

    def export(self, name: str) -> None:
        dlg = Gtk.FileChooserNative.new(tr("„{name}“ exportieren", name=name), self, Gtk.FileChooserAction.SAVE,
                                        tr("Speichern"), tr("Abbrechen"))
        dlg.set_current_folder(GLib.get_home_dir())
        dlg.set_current_name(f"{name}.conf")
        dlg.set_do_overwrite_confirmation(True)
        path = Path(dlg.get_filename()) if dlg.run() == Gtk.ResponseType.ACCEPT and dlg.get_filename() else None
        dlg.destroy()
        if path is None:
            return

        def ok(text: str) -> None:
            try:
                model.write_private(path, text)
            except OSError as e:
                self.message(f"{path}: {e.strerror}", error=True)
                return
            self.message(tr("Exportiert nach {path} (nur für dich lesbar – enthält den privaten Schlüssel).",
                            path=path))

        self.app.call(["export", name], None, ok, lambda m: self.message(m, error=True))

    def edit(self, name: str) -> None:
        self.app.call(["show", name], None, lambda out: Editor(self, name, out).present(),
                      lambda m: self.message(m, error=True))


class Editor(Gtk.Dialog):
    """Konfiguration bearbeiten: Schlüssel verborgen (Knopf „anzeigen“), Prüfung vor dem Speichern."""

    def __init__(self, win: Window, name: str, show_json: str):
        super().__init__(title=tr("„{name}“ bearbeiten", name=name), transient_for=win, modal=True)

        data = json.loads(show_json)
        self.win, self.name = win, name
        self.managed = bool(data.get("managed"))
        self.set_default_size(640, 480)
        self.add_button(tr("Abbrechen") if self.managed else tr("Schließen"), Gtk.ResponseType.CANCEL)
        self.save_btn = self.add_button(tr("Speichern"), Gtk.ResponseType.OK) if self.managed else None
        box = self.get_content_area()
        box.set_spacing(8)
        box.set_border_width(10)
        box.pack_start(label(tr("Private Schlüssel sind verborgen. Unverändert gelassenes „(verborgen)“ behält "
                                "den gespeicherten Schlüssel.") if self.managed
                             else tr("Nur ansehen – der Tunnel wurde nicht mit Bony's VPN angelegt."),
                             "bvpn-small", wrap=True), False, False, 0)
        scroll = Gtk.ScrolledWindow()
        self.view = styled(Gtk.TextView(monospace=True, editable=self.managed, wrap_mode=Gtk.WrapMode.NONE),
                           "bvpn-mono")
        self.view.get_buffer().set_text(data.get("conf", ""))
        scroll.add(self.view)
        box.pack_start(scroll, True, True, 0)
        bottom = Gtk.Box(spacing=10)
        self.reveal = Gtk.CheckButton(label=tr("Schlüssel anzeigen"))
        self.reveal.set_sensitive(self.managed)  # anzeigen = Export, geht nur bei eigenen Tunneln
        self.reveal.connect("toggled", self._on_reveal)
        bottom.pack_start(self.reveal, False, False, 0)
        self.check_label = label("", "bvpn-small", wrap=True)
        bottom.pack_start(self.check_label, True, True, 0)
        box.pack_start(bottom, False, False, 0)
        self.view.get_buffer().connect("changed", self._on_changed)
        self._pending = 0
        self._check()
        self.connect("response", self._on_response)
        self.show_all()

    def text(self) -> str:
        buf = self.view.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

    def _on_changed(self, _buf) -> None:
        if self._pending:
            GLib.source_remove(self._pending)
        self._pending = GLib.timeout_add(400, self._check)

    def _check(self) -> bool:
        self._pending = 0
        try:
            model.check_text(self.text())
            self.check_label.set_text(tr("Die Konfiguration ist in Ordnung."))
            self.check_label.get_style_context().remove_class("bvpn-bad")
            ok = True
        except wgconf.ConfError as e:
            self.check_label.set_text(str(e))
            self.check_label.get_style_context().add_class("bvpn-bad")
            ok = False
        if self.save_btn is not None:
            self.save_btn.set_sensitive(ok)
        return False

    def _on_reveal(self, btn) -> None:
        if not btn.get_active():
            self.view.get_buffer().set_text(model.hide(self.text()))
            return
        btn.set_sensitive(False)

        def ok(full: str) -> None:
            btn.set_sensitive(True)
            try:
                self.view.get_buffer().set_text(model.reveal(self.text(), full))
            except wgconf.ConfError as e:
                self.check_label.set_text(str(e))

        def fail(msg: str) -> None:
            btn.set_sensitive(True)
            btn.set_active(False)
            self.check_label.set_text(msg)

        self.win.app.call(["export", self.name], None, ok, fail)

    def _on_response(self, _dlg, response) -> None:
        if response != Gtk.ResponseType.OK:
            self.destroy()
            return
        text = self.text()
        try:
            parsed = model.check_text(text)
        except wgconf.ConfError as e:
            self.check_label.set_text(str(e))
            return
        if parsed.hooks and not self.win.confirm_hooks(self.name, parsed.hooks):
            return
        args = ["import", self.name, "--replace"] + (["--allow-hooks"] if parsed.hooks else [])
        self.save_btn.set_sensitive(False)

        def ok(_out) -> None:
            self.win.message(tr("Gespeichert."))
            self.win.app.refresh()
            self.destroy()

        def fail(msg: str) -> None:
            self.save_btn.set_sensitive(True)
            self.check_label.set_text(msg)

        self.win.app.call(args, text.encode("utf-8"), ok, fail)


def ask(parent, title: str, text: str, ok_text: str) -> bool:
    dlg = Gtk.MessageDialog(transient_for=parent, modal=True, message_type=Gtk.MessageType.QUESTION, text=title)
    dlg.format_secondary_text(text)
    dlg.add_button(tr("Abbrechen"), Gtk.ResponseType.CANCEL)
    dlg.add_button(ok_text, Gtk.ResponseType.OK)
    dlg.set_default_response(Gtk.ResponseType.OK)
    ok = dlg.run() == Gtk.ResponseType.OK
    dlg.destroy()
    return ok


class TaskDialog(Gtk.Dialog):
    """Installation mit Fortschritt: Zeile für Zeile Protokoll, Balken beim Download, verständlicher Fehler."""

    def __init__(self, win: Window, title: str, work, done_text: str, restart: bool = False, on_done=None):
        super().__init__(title=title, transient_for=win, modal=True)
        self.win, self.work, self.done_text, self.on_done = win, work, done_text, on_done
        self.set_default_size(560, 360)
        box = self.get_content_area()
        box.set_spacing(8)
        box.set_border_width(12)
        self.headline = label(tr("Bitte warten …"), "bvpn-name", wrap=True)
        box.pack_start(self.headline, False, False, 0)
        self.bar = Gtk.ProgressBar(show_text=False)
        box.pack_start(self.bar, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        self.view = styled(Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR),
                           "bvpn-mono")
        scroll.add(self.view)
        box.pack_start(scroll, True, True, 0)
        self.restart_btn = self.add_button(tr("Neu starten"), 2) if restart else None
        self.close_btn = self.add_button(tr("Schließen"), Gtk.ResponseType.CLOSE)
        self.close_btn.set_sensitive(False)
        if self.restart_btn is not None:
            self.restart_btn.set_sensitive(False)
        self.connect("response", self._on_response)
        self.connect("delete-event", lambda *_: not self.close_btn.get_sensitive())
        self._pulse = 0
        self.ok = None

    def start(self) -> None:
        self.show_all()
        self._pulse = GLib.timeout_add(150, self._do_pulse)
        threading.Thread(target=self._run, daemon=True).start()

    def _do_pulse(self) -> bool:
        if self.ok is not None:
            return False
        if self.bar.get_fraction() == 0 or self._pulsing:
            self.bar.pulse()
        return True

    _pulsing = True

    def log(self, line: str) -> None:
        GLib.idle_add(self._append, line)

    def _append(self, line: str) -> bool:
        buf = self.view.get_buffer()
        buf.insert(buf.get_end_iter(), line + "\n")
        self.view.scroll_to_iter(buf.get_end_iter(), 0, False, 0, 1)
        return False

    def progress(self, done: int, total: int) -> None:
        if total:
            GLib.idle_add(self._set_fraction, done / total)

    def _set_fraction(self, f: float) -> bool:
        self._pulsing = False
        self.bar.set_fraction(min(1.0, f))
        return False

    def _run(self) -> None:
        try:
            self.work(self.log, self.progress)
        except (updates.UpdateError, OSError) as e:
            GLib.idle_add(self._finish, False, str(e))
        else:
            GLib.idle_add(self._finish, True, self.done_text)

    def _finish(self, ok: bool, text: str) -> bool:
        self.ok = ok
        self.bar.set_fraction(1.0 if ok else 0.0)
        self.headline.set_text(text)
        for c in ("bvpn-ok", "bvpn-bad"):
            self.headline.get_style_context().remove_class(c)
        self.headline.get_style_context().add_class("bvpn-ok" if ok else "bvpn-bad")
        self.close_btn.set_sensitive(True)
        if self.restart_btn is not None:
            self.restart_btn.set_sensitive(ok)
        if ok and self.on_done is not None:
            self.on_done()
        return False

    def _on_response(self, _dlg, response) -> None:
        if not self.close_btn.get_sensitive():
            return
        self.destroy()
        if response == 2:
            self.win.app.restart()


class AgentsDialog(Gtk.Dialog):
    """Bony's Agents vorstellen – öffnen, wenn installiert, sonst aus der offiziellen Quelle installieren."""

    def __init__(self, win: Window):
        super().__init__(title=tr("Bony's Agents – virtuelle Agent-PCs"), transient_for=win, modal=True)
        self.win = win
        self.set_default_size(520, -1)
        box = self.get_content_area()
        box.set_spacing(10)
        box.set_border_width(16)
        box.pack_start(label(tr("Bony's Agents – virtuelle Agent-PCs"), "bvpn-name", "bvpn-text", wrap=True),
                       False, False, 0)
        box.pack_start(label(tr(
            "Bony's Agents erstellt per Mausklick eigene virtuelle Rechner (Debian mit Desktop), in denen "
            "KI-Agenten, Browser und Messenger fertig eingerichtet sind – sauber getrennt von deinem "
            "eigentlichen System. Bony's VPN ist dort schon dabei."), "bvpn-text", wrap=True), False, False, 0)
        yt = Gtk.LinkButton.new_with_label(YOUTUBE_CHANNEL_URL, tr("Videos auf YouTube: @BonysAgents"))
        yt.set_halign(Gtk.Align.START)
        box.pack_start(yt, False, False, 0)
        self.command = host.agents_command()
        if self.command:
            self.status = label(tr("Bony's Agents ist installiert."), "bvpn-ok")
            self.main_btn = styled(Gtk.Button(label=tr("Bony's Agents öffnen")), "bvpn-accent")
        else:
            self.status = label(tr("Bony's Agents ist noch nicht installiert."), "bvpn-muted")
            self.main_btn = styled(Gtk.Button(label=tr("Bony's Agents installieren …")), "bvpn-accent")
        box.pack_start(self.status, False, False, 0)
        self.main_btn.set_halign(Gtk.Align.START)
        self.main_btn.connect("clicked", self._on_main)
        box.pack_start(self.main_btn, False, False, 0)
        self.add_button(tr("Schließen"), Gtk.ResponseType.CLOSE)
        styled(self, "bvpn-window")

    def run_dialog(self) -> None:
        self.show_all()
        self.run()
        self.destroy()

    def _on_main(self, _btn) -> None:
        if self.command:
            host.open_agents()
            self.response(Gtk.ResponseType.CLOSE)
            return
        via_apt = updates.apt_source_configured()
        source = (tr("aus der eingerichteten Paketquelle (apt install bonys-agents)") if via_apt else
                  tr("als .deb aus dem offiziellen Release auf GitHub (zappotex/BonysAgents). Signatur und "
                     "Prüfsumme werden vor der Installation geprüft"))
        if not ask(self, tr("Bony's Agents installieren?"),
                   tr("Bony's Agents wird {source}. Das Paket ist etwa 200 MB groß und bringt QEMU mit. "
                      "Dein Rechner fragt nach dem Administrator-Passwort.", source=source),
                   tr("Installieren")):
            return
        self.response(Gtk.ResponseType.CLOSE)
        TaskDialog(self.win, tr("Bony's Agents installieren"),
                   lambda log, progress: updates.install_package(updates.AGENTS, log, progress=progress),
                   done_text=tr("Bony's Agents ist installiert – du findest es im Startmenü.")).start()


# ---------------------------------------------------------------- Anwendung
class App(Gtk.Application):
    def __init__(self, client: HelperClient | None = None, sys_net: Path = model.SYS_NET, unique: bool = True,
                 agentpc: bool | None = None, auto_status: bool | None = None, update_check: bool = True,
                 use_tray: bool = True):
        flags = Gio.ApplicationFlags.HANDLES_COMMAND_LINE
        if not unique:  # Tests: ohne D-Bus-Anmeldung
            flags |= Gio.ApplicationFlags.NON_UNIQUE
        super().__init__(application_id=APP_ID, flags=flags)
        self.client = client or HelperClient()
        self.sys_net = sys_net
        self.status = model.Status()
        self.watch = model.Watch()
        self.rxwatch = model.RxWatch()
        self.activity = model.Activity()
        self.busy: dict[str, str] = {}
        self.pending: set[str] = set()   # Schalter mit laufender Aktion („autostart“, „killswitch“)
        self.window: Window | None = None
        self.tray: Tray | None = None
        self.background = False   # mit --tray gestartet: läuft ohne Fenster weiter
        self.refreshing = False
        self.refresh_again = False
        self.last_refresh = 0.0
        self.started = False
        self.agentpc = host.is_agentpc() if agentpc is None else agentpc
        # Ohne polkit-Regel für „status“ (polkit 0.105) fragt jede Abfrage nach dem Passwort – dann
        # nur bei Aktionen und auf Knopfdruck fragen, Verbunden/Getrennt kommt aus /sys
        self.auto_status = host.status_without_password() if auto_status is None else auto_status
        self.update_check = update_check
        self.nm_active: set[str] = set()   # WireGuard-Verbindungen des NetworkManager (nur eigener Rechner)
        self.no_resolvconf = host.resolvconf_missing()
        self.pending_exceptions: list[str] | None = None
        self.use_tray = use_tray
        self.tray_failed = False
        self.holding = False

    # ----- Lebenszyklus
    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        settings = Gtk.Settings.get_default()
        if settings is not None:
            settings.set_property("gtk-application-prefer-dark-theme", True)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode("utf-8"))
        screen = Gdk.Screen.get_default()
        if screen is not None:
            Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        if APP_ICON.exists():
            Gtk.Window.set_default_icon_from_file(str(APP_ICON))

    def do_command_line(self, cmdline) -> int:
        args = cmdline.get_arguments()[1:]
        if not self.started:
            self.started = True
            GLib.timeout_add_seconds(TICK, self.tick)
            self.refresh()
        if self.use_tray and self.tray is None and not self.tray_failed:
            # Symbol in der Leiste bei jedem Start: Schließen versteckt das Fenster nur
            self.tray = Tray(self)
            if self.tray is not None and not self.holding:
                self.hold()
                self.holding = True
        if "--tray" in args:
            if not self.background:
                self.background = True   # Autostart: ohne Fenster, meldet Abbrüche auch ohne Symbol
                if not self.holding:
                    self.hold()
                    self.holding = True
        else:
            self.show_window()
        return 0

    def keep_running(self) -> bool:
        """Läuft nach dem Schließen des Fensters weiter – mit Symbol in der Leiste oder im Autostart."""
        return self.background or self.tray is not None

    def tray_unavailable(self) -> None:
        print(tr("Kein Symbol in der Leiste – dieser Desktop zeigt keine Statussymbole."), file=sys.stderr)
        self.tray = None
        self.tray_failed = True
        if self.holding and not self.background:
            # ohne Symbol bliebe nach dem Schließen ein unsichtbares Programm übrig
            self.release()
            self.holding = False

    def show_window(self) -> None:
        first = self.window is None
        if first:
            self.window = Window(self)
            self.window.show_all()
            self.window.infobar.hide()
            self.window.update_setup()
            self.window.show_status(self.status)
        self.window.present()
        self.refresh()
        if first and self.update_check and updates.auto_check_due():
            self.check_updates(manual=False)

    def check_updates(self, manual: bool) -> None:
        skip = None if manual else updates.load_settings().get("update_skip")

        def work():
            rel = updates.check(skip=skip)
            updates.set_setting("update_last_check", time.time())
            GLib.idle_add(done, rel)

        def done(rel) -> bool:
            if self.window is None:
                return False
            if rel is not None:
                self.window.show_update(rel)
            elif manual:
                self.window.message(tr("Bony's VPN ist aktuell (Version {v}).", v=__version__))
            return False

        if manual and self.window is not None:
            self.window.message(tr("Suche nach Updates …"))
        threading.Thread(target=work, daemon=True).start()

    def restart(self) -> None:
        """Nach einem Update: neue Version starten, diese beenden (eine Instanz je Sitzung)."""
        gui = "/usr/bin/bonys-vpn-gui"
        if os.path.exists(gui):
            subprocess.Popen(["sh", "-c", f"sleep 1; exec {gui}"], start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.quit()

    def toggle_window(self) -> None:
        if self.window is not None and self.window.get_visible():
            self.window.hide()
        else:
            self.show_window()

    # ----- Helfer im Hintergrund aufrufen
    def call(self, args: list[str], data: bytes | None, ok, fail) -> None:
        def work():
            try:
                out = self.client.run(*args, data=data)
            except cli.CliError as e:
                GLib.idle_add(fail, str(e))
            else:
                GLib.idle_add(ok, out)

        threading.Thread(target=work, daemon=True).start()

    def action(self, name: str | None, success: str | None, args: tuple[str, ...], busy_text: str = "",
               pending: str = "", on_ok=None) -> None:
        """Ändernde Aktion: Tunnel als beschäftigt markieren, Helfer aufrufen, danach neu laden.

        ``pending``: Schalter, der bis zur Antwort so bleibt, wie der Benutzer ihn gestellt hat.
        Neu gezeichnet wird erst nach dem auslösenden Ereignis (idle) – nie mitten im Klick.
        """
        if name and busy_text:
            self.busy[name] = busy_text
        if pending:
            self.pending.add(pending)
        self.watch.expect()
        GLib.idle_add(self._idle_update)

        def done():
            self.busy.pop(name, None)
            self.pending.discard(pending)

        def ok(_out):
            done()
            if on_ok is not None:
                on_ok()
            if success and self.window:
                self.window.message(success)
            self.refresh()

        def fail(msg):
            done()
            if self.window:
                self.window.message(msg, error=True)
            else:
                self.notify(tr("Fehler"), msg)
            self.refresh()

        self.call(list(args), None, ok, fail)

    def connect_tunnel(self, name: str) -> None:
        if self.busy:
            return
        self.action(name, None, ("up", name), tr("verbindet …"))

    def killswitch_args(self, on: bool) -> tuple[str, ...]:
        if not on or self.pending_exceptions is None:
            return ("killswitch", "on" if on else "off")
        nets, self.pending_exceptions = self.pending_exceptions, None
        return ("killswitch", "on", *[x for n in nets for x in ("--exception", n)]) if nets \
            else ("killswitch", "on", "--no-exceptions")

    def disconnect(self, name: str) -> None:
        if self.busy:
            return
        self.action(name, None, ("down", name), tr("trennt …"))

    # ----- Status
    def refresh(self) -> None:
        if self.refreshing:
            self.refresh_again = True
            return
        self.refreshing = True

        def work():
            try:
                data, err = self.client.status(), None
            except cli.CliError as e:
                data, err = None, str(e)
            GLib.idle_add(self._refreshed, data, err)

        threading.Thread(target=work, daemon=True).start()

    def _refreshed(self, data: dict | None, err: str | None) -> bool:
        self.refreshing = False
        self.last_refresh = time.time()
        if data is not None:
            if not self.agentpc:
                self.nm_active = host.nm_wireguard()
            self.status = model.Status.from_helper(data)
            self.status.apply_live(model.read_live(self.sys_net))
            self.activity.apply(self.status)
            for ev in self.watch.observe(self.status):
                self.notify(ev.title(), ev.body())
        elif err and self.window:
            self.window.message(err, error=True)
        self.update_views()
        if self.refresh_again:
            self.refresh_again = False
            self.refresh()
        return False

    def _idle_update(self) -> bool:
        self.update_views()
        return False

    def update_views(self) -> None:
        if self.window is not None:
            self.window.show_status(self.status)
        if self.tray is not None:
            self.tray.update(self.status)

    def tick(self) -> bool:
        live = model.read_live(self.sys_net)
        changed = self.status.apply_live(live)
        self.activity.update(live)
        self.activity.apply(self.status)
        visible = self.window is not None and self.window.get_visible()
        now = time.time()
        if changed:
            self.refresh()
        elif self.auto_status and visible and self.status.active and now - self.last_refresh > DETAIL_REFRESH:
            self.refresh()
        elif self.auto_status and self.rxwatch.suspicious(live, now):
            self.refresh()   # keine Daten mehr vom Server? → Handshake beim Helfer nachsehen
        elif visible and not self.busy:
            self.window.show_details(self.status)
        return True

    def notify(self, title: str, body: str) -> None:
        n = Gio.Notification.new(title)
        n.set_body(body)
        if APP_ICON.exists():
            n.set_icon(Gio.FileIcon.new(Gio.File.new_for_path(str(APP_ICON))))
        self.send_notification("bonys-vpn-connection", n)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    GLib.set_prgname("bonys-vpn")
    GLib.set_application_name(tr("Bony's VPN"))
    return App().run(argv)


if __name__ == "__main__":
    sys.exit(main())

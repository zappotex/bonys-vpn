#!/usr/bin/env bash
# Baut die Debian-Pakete von Bony's VPN:
#
#   packaging/build-deb.sh   -> dist/bonys-vpn_<version>_all.deb
#                               dist/bonys-vpn-agentpc_<version>_all.deb
#
# Reiner Python-Code mit GTK 3 aus den Paketquellen (python3-gi) – nichts Kompiliertes, nichts
# Gebündeltes. Deshalb „Architecture: all“: dasselbe Paket läuft auf amd64 und arm64
# (Debian 12/13, Ubuntu 22.04+, Linux Mint 21+). Braucht nur dpkg-deb, gpg und python3.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' src/bonys_vpn/__init__.py)"
HOMEPAGE="https://github.com/$(sed -n 's/^REPO = "\(.*\)"/\1/p' src/bonys_vpn/__init__.py)"
MAINTAINER="Bony <bonys-agents@users.noreply.github.com>"
mkdir -p dist build

copyright() {
  {
    echo "Bony's VPN"
    echo "Copyright (C) 2026 Bony"
    echo "Lizenz: GPL-3.0-or-later (vollständiger Text unten)."
    echo "Quelltext: $HOMEPAGE"
    echo
    cat LICENSE
  } > "$1/copyright"
  chmod 644 "$1/copyright"
}

finish() {  # Rechte setzen und Paket bauen
  local stage="$1" out="$2"
  find "$stage" -type d -exec chmod 755 {} +
  SIZE_KB="$(du -sk --exclude=DEBIAN "$stage" | cut -f1)"
  sed -i "s/@SIZE@/$SIZE_KB/" "$stage/DEBIAN/control"
  dpkg-deb --root-owner-group -Zxz --build "$stage" "$out" >/dev/null
  echo "==> Fertig: $out ($(du -h "$out" | cut -f1))"
}

# ======================================================================== bonys-vpn
PKG=bonys-vpn
STAGE="build/deb/${PKG}_${VERSION}_all"
rm -rf "$STAGE"
install -d "$STAGE/DEBIAN" "$STAGE/usr/share/doc/$PKG" "$STAGE/usr/share/$PKG" "$STAGE/usr/share/keyrings"
python3 -I src/bonys_vpn/install.py --package bonys-vpn --root "$STAGE"
copyright "$STAGE/usr/share/doc/$PKG"
install -m644 README.md CHANGELOG.md "$STAGE/usr/share/doc/$PKG/"
install -m644 docs/cli.md "$STAGE/usr/share/doc/$PKG/cli.md"
install -m644 docs/sicherheit.md "$STAGE/usr/share/doc/$PKG/sicherheit.md"

# Paketquelle für Updates (dieselbe wie bei Bony's Agents, eigene Suite – siehe updates.py)
gpg --dearmor < packaging/bonys-vpn-archive-keyring.asc > "$STAGE/usr/share/keyrings/$PKG.gpg"
chmod 644 "$STAGE/usr/share/keyrings/$PKG.gpg"
PYTHONPATH=src python3 -c "from bonys_vpn import updates; print(updates.apt_sources_text(), end='')" \
  > "$STAGE/usr/share/$PKG/$PKG.sources"
chmod 644 "$STAGE/usr/share/$PKG/$PKG.sources"

cat > "$STAGE/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Section: net
Priority: optional
Architecture: all
Maintainer: $MAINTAINER
Installed-Size: @SIZE@
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-3.0, wireguard-tools, nftables, pkexec | policykit-1
Recommends: gir1.2-ayatanaappindicator3-0.1
Suggests: bonys-agents
Replaces: bonys-agents (<< 0.15)
Homepage: $HOMEPAGE
Description: WireGuard-VPN mit Kill-Switch – einfach per Mausklick
 Bony's VPN verwaltet WireGuard-Tunnel: importieren (auch per Drag&Drop
 und ZIP), mit einem Klick verbinden und trennen, Live-Werte, Kill-Switch
 (ohne Tunnel kein Internet, auch nach einem Neustart), Autostart und
 Symbol in der Leiste. Root-Rechte bekommt nur ein kleiner Helfer über
 polkit. Auch die Kommandozeile bonys-vpn ist dabei. Dazu der Bereich
 „Bony-Netz“: Agent-PCs von Bony's Agents übers Internet erreichen.
EOF

cat > "$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
  if [ -d /run/systemd/system ]; then systemctl daemon-reload >/dev/null 2>&1 || true; fi
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true
  command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t /usr/share/icons/hicolor || true
  # Alte Installation unter /usr/local (Bony's Agents bis 0.14 im Agent-PC) entfernen.
  # Tunnel in /etc/wireguard und der Kill-Switch-Zustand bleiben.
  python3 -IB /usr/lib/bonys-vpn/bonys_vpn/install.py --remove-legacy || true
  # Paketquelle für Updates (Aktualisierungsverwaltung). Hat der Nutzer sie mit „Enabled: no“
  # abgeschaltet, bleibt das so.
  SRC=/usr/share/bonys-vpn/bonys-vpn.sources
  DST=/etc/apt/sources.list.d/bonys-vpn.sources
  if [ -f "$DST" ] && grep -qiE '^[[:space:]]*Enabled:[[:space:]]*no' "$DST"; then
    :
  else
    install -m644 "$SRC" "$DST"
  fi
fi
exit 0
EOF
cat > "$STAGE/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
# Beim Entfernen (nicht beim Update) den Kill-Switch abschalten – sonst bliebe der Rechner ohne Tunnel
# offline, ohne dass es noch ein Programm zum Abschalten gäbe. Tunnel in /etc/wireguard bleiben.
if [ "$1" = "remove" ] && [ -x /usr/sbin/bonys-vpn-helper ]; then
  /usr/sbin/bonys-vpn-helper killswitch off >/dev/null 2>&1 || true
fi
exit 0
EOF
cat > "$STAGE/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
case "$1" in
  # Ohne Paket fehlt der Schlüssel unter /usr/share/keyrings – „apt update“ meldete sonst einen Fehler
  remove|purge) rm -f /etc/apt/sources.list.d/bonys-vpn.sources ;;
esac
# Vollständig entfernen: Einstellungen (Kill-Switch-Zustand, keine Schlüssel). /etc/wireguard bleibt immer.
if [ "$1" = "purge" ]; then rm -rf /etc/bonys-vpn; fi
if [ -d /run/systemd/system ]; then systemctl daemon-reload >/dev/null 2>&1 || true; fi
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true
exit 0
EOF
chmod 755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/prerm" "$STAGE/DEBIAN/postrm"
finish "$STAGE" "dist/${PKG}_${VERSION}_all.deb"

# ======================================================================== bonys-vpn-agentpc
PKG=bonys-vpn-agentpc
STAGE="build/deb/${PKG}_${VERSION}_all"
rm -rf "$STAGE"
install -d "$STAGE/DEBIAN" "$STAGE/usr/share/doc/$PKG"
python3 -I src/bonys_vpn/install.py --package bonys-vpn-agentpc --root "$STAGE"
copyright "$STAGE/usr/share/doc/$PKG"
echo "/etc/xdg/autostart/bonys-vpn-agentpc-tray.desktop" > "$STAGE/DEBIAN/conffiles"

cat > "$STAGE/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Section: net
Priority: optional
Architecture: all
Maintainer: $MAINTAINER
Installed-Size: @SIZE@
Depends: bonys-vpn (= $VERSION), passwd
Recommends: gir1.2-ayatanaappindicator3-0.1
Homepage: $HOMEPAGE
Description: Bony's VPN im Agent-PC von Bony's Agents (nicht für den eigenen Rechner)
 Nur für die virtuellen Agent-PCs von Bony's Agents – Bony's Agents
 installiert es dort selbst. Mitglieder der Gruppe bonys-vpn steuern
 Bony's VPN ohne Passwort, das Symbol startet nach der Anmeldung in der
 Leiste, und der Kill-Switch lässt das QEMU-Netz (10.0.2.0/24) durch.
 .
 Auf dem eigenen Rechner NICHT installieren: Dort soll jede Änderung am
 VPN nach dem Administrator-Passwort fragen.
EOF

cat > "$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
  getent group bonys-vpn >/dev/null || groupadd --system bonys-vpn
  # Alte Regel „Benutzer agent ohne Passwort“ (Bony's Agents bis 0.14) → Gruppe bonys-vpn
  python3 -IB /usr/lib/bonys-vpn/bonys_vpn/install.py --migrate-agent-rule || true
  # Kill-Switch: Steuerung über das QEMU-User-Netz bleibt erlaubt (nur beim ersten Mal anlegen)
  if [ ! -e /etc/bonys-vpn/killswitch.json ]; then
    install -d -m755 /etc/bonys-vpn
    printf '{\n  "enabled": false,\n  "exceptions": ["10.0.2.0/24"]\n}\n' > /etc/bonys-vpn/killswitch.json
    chmod 644 /etc/bonys-vpn/killswitch.json
  fi
fi
exit 0
EOF
cat > "$STAGE/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "purge" ]; then groupdel bonys-vpn >/dev/null 2>&1 || true; fi
exit 0
EOF
chmod 755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm"
finish "$STAGE" "dist/${PKG}_${VERSION}_all.deb"

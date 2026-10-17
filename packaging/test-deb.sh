#!/usr/bin/env bash
# Installiert die Pakete in einem frischen System (Container) und prüft sie – läuft als root.
#
#   packaging/test-deb.sh DIST-ORDNER
#
# Prüft: Abhängigkeiten aus den offiziellen Paketquellen, Kommandozeile und JSON-Schnittstelle,
# Helfer als root, Oberfläche startet (Xvfb), Umstieg von der alten Installation unter /usr/local
# (Tunnel bleiben), Agent-PC-Paket (Gruppe, polkit-Regel), Entfernen und Purge.
set -euxo pipefail
DIST="$(cd "$1" && pwd)"
export DEBIAN_FRONTEND=noninteractive
. /etc/os-release
echo "==> $PRETTY_NAME ($(dpkg --print-architecture))"

apt-get update -qq
apt-get install -y -qq xvfb xauth dbus-x11 python3 > /dev/null

# ---------- alte Installation wie von Bony's Agents 0.14 im Agent-PC (install.py --variant agent)
useradd -m agent
mkdir -p /usr/local/lib/bonys-vpn/bonys_vpn /usr/local/sbin /usr/local/bin /usr/local/share/applications \
  /etc/xdg/autostart /etc/systemd/system /etc/polkit-1/rules.d /etc/wireguard /etc/bonys-vpn
echo "# alt" > /usr/local/lib/bonys-vpn/bonys_vpn/cli.py
for f in /usr/local/sbin/bonys-vpn-helper /usr/local/bin/bonys-vpn /usr/local/bin/bonys-vpn-gui \
         /usr/local/share/applications/io.github.bonys_agents.vpn.desktop /etc/xdg/autostart/bonys-vpn-tray.desktop \
         /etc/systemd/system/bonys-vpn-killswitch.service; do echo alt > "$f"; done
cat > /etc/polkit-1/rules.d/50-bonys-vpn.rules <<'EOF'
polkit.addRule(function(action, subject) {
    if (action.id == "io.github.bonys-agents.vpn" && subject.user == "agent") {
        return polkit.Result.YES;
    }
});
EOF
# ein „Tunnel“ (Platzhalter, kein echter Schlüssel) und der Kill-Switch-Zustand bleiben unverändert
printf '[Interface]\n# Platzhalter\n' > /etc/wireguard/alt.conf
chmod 600 /etc/wireguard/alt.conf
printf '{"enabled": false, "exceptions": ["10.0.2.0/24"]}\n' > /etc/bonys-vpn/killswitch.json
SUM_BEFORE="$(sha256sum /etc/wireguard/alt.conf /etc/bonys-vpn/killswitch.json)"

# ---------- Installation (Abhängigkeiten aus den offiziellen Paketquellen)
apt-get install -y -qq "$DIST"/bonys-vpn_*_all.deb > /dev/null
bonys-vpn --version
bonys-vpn version --json | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["schema"] == 1 and d["helper"] == "/usr/sbin/bonys-vpn-helper", d'
bonys-vpn-helper status | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["schema"] == 1 and "killswitch" in d, d'
bonys-vpn status --json | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["schema"] == 1 and d["version"], d'
head -1 /usr/sbin/bonys-vpn-helper | grep -qx '#!/usr/bin/python3 -IB'
test "$(stat -c %U:%a /usr/sbin/bonys-vpn-helper)" = "root:755"
grep -q 'exec.path">/usr/sbin/bonys-vpn-helper<' /usr/share/polkit-1/actions/io.github.bonys-agents.vpn.policy
grep -q 'command_line") == "/usr/sbin/bonys-vpn-helper status"' /usr/share/polkit-1/rules.d/50-bonys-vpn.rules
systemctl cat bonys-vpn-killswitch.service > /dev/null 2>&1 || test -f /usr/lib/systemd/system/bonys-vpn-killswitch.service
grep -q "^Suites: bonys-vpn" /etc/apt/sources.list.d/bonys-vpn.sources
test -s /usr/share/keyrings/bonys-vpn.gpg
desktop-file-validate /usr/share/applications/bonys-vpn.desktop 2>/dev/null || true
# Umstieg: alte Dateien weg, Tunnel und Kill-Switch-Zustand unverändert, alte polkit-Regel noch da
test ! -e /usr/local/lib/bonys-vpn && test ! -e /usr/local/sbin/bonys-vpn-helper && test ! -e /usr/local/bin/bonys-vpn-gui
test ! -e /etc/systemd/system/bonys-vpn-killswitch.service && test ! -e /etc/xdg/autostart/bonys-vpn-tray.desktop
test "$(sha256sum /etc/wireguard/alt.conf /etc/bonys-vpn/killswitch.json)" = "$SUM_BEFORE"
test -e /etc/polkit-1/rules.d/50-bonys-vpn.rules
test ! -e /usr/share/bonys-vpn/agentpc
# Baustein des Bony-Netzes liegt neben bonys_vpn und lässt sich mit dem System-Python laden
python3 -IB -c 'import sys; sys.path.insert(0, "/usr/lib/bonys-vpn"); import bony_netz, bonys_vpn.netz; print("bony_netz", bony_netz.__version__)'
# Kein Bytecode-Cache im Paketordner (dpkg würde ihn beim Entfernen liegen lassen)
test -z "$(find /usr/lib/bonys-vpn -name __pycache__)"

# ---------- Oberfläche startet (ohne Bildschirm, 8 s laufen lassen = kein Absturz)
set +e
dbus-run-session -- xvfb-run -a timeout 8 bonys-vpn-gui > /tmp/gui.log 2>&1
rc=$?
set -e
cat /tmp/gui.log
test "$rc" -eq 124
! grep -q Traceback /tmp/gui.log

# ---------- Agent-PC-Paket: Gruppe statt Benutzer, alte Regel übernommen
apt-get install -y -qq "$DIST"/bonys-vpn-agentpc_*_all.deb > /dev/null
getent group bonys-vpn
id -nG agent | tr ' ' '\n' | grep -qx bonys-vpn
test ! -e /etc/polkit-1/rules.d/50-bonys-vpn.rules
grep -q 'isInGroup("bonys-vpn")' /usr/share/polkit-1/rules.d/49-bonys-vpn-agentpc.rules
grep -q "Exec=/usr/bin/bonys-vpn-gui --tray" /etc/xdg/autostart/bonys-vpn-agentpc-tray.desktop
test -e /usr/share/bonys-vpn/agentpc
test "$(sha256sum /etc/wireguard/alt.conf /etc/bonys-vpn/killswitch.json)" = "$SUM_BEFORE"

# ---------- Entfernen: Tunnel bleiben, Purge löscht nur die eigenen Einstellungen
apt-get purge -y -qq bonys-vpn-agentpc > /dev/null
! getent group bonys-vpn
test ! -e /usr/share/polkit-1/rules.d/49-bonys-vpn-agentpc.rules
apt-get remove -y -qq bonys-vpn > /dev/null
test ! -e /usr/sbin/bonys-vpn-helper && test ! -e /etc/apt/sources.list.d/bonys-vpn.sources
test -e /etc/wireguard/alt.conf && test -e /etc/bonys-vpn/killswitch.json
apt-get purge -y -qq bonys-vpn > /dev/null
test ! -e /etc/bonys-vpn && test -e /etc/wireguard/alt.conf
test ! -e /usr/lib/bonys-vpn
echo "==> OK: $PRETTY_NAME"

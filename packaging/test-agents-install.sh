#!/usr/bin/env bash
# „Bony's Agents installieren“ wie der Knopf im Menü – in einem frischen System (Container), als root:
# neuestes Release von zappotex/BonysAgents laden, minisign-Signatur und SHA-256 prüfen, mit apt installieren.
#
#   packaging/test-agents-install.sh DIST-ORDNER
set -euxo pipefail
DIST="$(cd "$1" && pwd)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq "$DIST"/bonys-vpn_*_all.deb > /dev/null
python3 -IB - <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, "/usr/lib/bonys-vpn")
from bonys_vpn import host, updates
assert host.agents_command() is None
# ohne Paketquelle: also der Weg über das geprüfte .deb aus dem Release
version = updates.install_package(updates.AGENTS, print, sources_dir=Path("/nonexistent"))
print("installiert:", version)
assert host.agents_command(), "Bony's Agents nach der Installation nicht erkannt"
PY
bonys-agents --version

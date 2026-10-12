# SPDX-License-Identifier: GPL-3.0-or-later
"""Update-Suche, geprüfte Downloads, Installation von Bony's Agents, Erkennung des Rechners, Paketskripte."""
import hashlib
import io
import json
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

if sys.platform != "linux":
    pytest.skip("Bony's VPN gibt es nur unter Linux", allow_module_level=True)

from bonys_vpn import UPDATE_PUBLIC_KEY, __version__, host, install, signing, updates  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
VPN_DL = f"https://github.com/{updates.VPN.repo}/releases/download"
AGENTS_DL = f"https://github.com/{updates.AGENTS.repo}/releases/download"
SECRET, KEY_ID = b"\x11" * 32, b"BONYS-01"
PUBLIC = signing.PublicKey(KEY_ID, signing.ed25519_public_key(SECRET)).text()


class _Resp(io.BytesIO):
    def __init__(self, data: bytes, url: str):
        super().__init__(data)
        self.url = url
        self.headers = {"Content-Length": str(len(data))}

    def geturl(self):
        return self.url


def _opener(routes: dict):
    def opener(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        assert timeout, "ohne Zeitlimit"
        if url not in routes:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        value = routes[url]
        if isinstance(value, Exception):
            raise value
        return _Resp(value if isinstance(value, bytes) else json.dumps(value).encode(), url)
    return opener


def _release_json(repo: str, tag: str, assets=(), prerelease=False):
    return {"tag_name": tag, "name": tag, "body": "- neu", "prerelease": prerelease, "draft": False,
            "html_url": f"https://github.com/{repo}/releases/tag/{tag}",
            "assets": [{"name": n, "browser_download_url": u, "size": 1} for n, u in assets]}


def signed(product: updates.Product, version: str, files: dict[str, bytes], title: str | None = None):
    """Release mit Dateien, SHA256SUMS und minisign-Signatur – wie aus der Pipeline."""
    dl = f"https://github.com/{product.repo}/releases/download/v{version}"
    sums = "".join(f"{hashlib.sha256(d).hexdigest()}  {n}\n" for n, d in files.items()).encode()
    sig = signing.sign(sums, SECRET, KEY_ID, f"{title or product.title} v{version}").encode()
    routes = {f"{dl}/{n}": d for n, d in files.items()}
    routes[f"{dl}/SHA256SUMS"], routes[f"{dl}/SHA256SUMS.minisig"] = sums, sig
    rel = updates._release(_release_json(product.repo, f"v{version}", [(u.rsplit("/", 1)[1], u) for u in routes]))
    return rel, routes


# ---------------------------------------------------------------- Update-Suche
def test_versions():
    v = updates.Version.parse
    assert v("v1.0.0") < v("1.0.1") < v("1.1.0") < v("2.0.0")
    assert v("1.0.0-rc.1") < v("1.0.0")
    assert updates.is_newer("1.0.1", "1.0.0") and not updates.is_newer("1.0.0", "1.0.0")
    assert v("kaputt") is None and not updates.is_newer("kaputt", "1.0.0")


def test_update_check():
    latest = f"https://api.github.com/repos/{updates.VPN.repo}/releases/latest"
    rel = updates.check("1.0.0", opener=_opener({latest: _release_json(updates.VPN.repo, "v1.1.0")}))
    assert rel.version == "1.1.0" and rel.notes == "- neu"
    assert updates.check("1.1.0", opener=_opener({latest: _release_json(updates.VPN.repo, "v1.1.0")})) is None
    assert updates.check("1.0.0", skip="1.1.0",
                         opener=_opener({latest: _release_json(updates.VPN.repo, "v1.1.0")})) is None
    assert updates.check("1.0.0", opener=_opener({latest: _release_json(updates.VPN.repo, "v2.0.0-rc.1",
                                                                        prerelease=True)})) is None


@pytest.mark.parametrize("value", [urllib.error.URLError("offline"), TimeoutError("Zeit"), b"<html>",
                                   {"message": "Not Found"}])
def test_no_update_when_github_unreachable(value):
    latest = f"https://api.github.com/repos/{updates.VPN.repo}/releases/latest"
    assert updates.check("0.1.0", opener=_opener({latest: value})) is None


def test_auto_check_once_per_day(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert updates.auto_check_due(now=1_000_000)
    updates.set_setting("update_last_check", 1_000_000)
    assert not updates.auto_check_due(now=1_000_000 + 3600)
    assert updates.auto_check_due(now=1_000_000 + 25 * 3600)
    updates.set_setting("update_auto_check", False)
    assert not updates.auto_check_due(now=1_000_000 + 25 * 3600)
    assert json.loads((tmp_path / "bonys-vpn/settings.json").read_text())["update_auto_check"] is False


def test_same_signing_key_as_bonys_agents():
    assert UPDATE_PUBLIC_KEY == "RWSbJYUNae7BsW6nDlNspFi16gc1nsM4hGbdmXq9noaPLpeVMtvDdIM6"
    assert signing.PublicKey.parse(UPDATE_PUBLIC_KEY)


# ---------------------------------------------------------------- Geprüfter Download
def test_download_verified(tmp_path):
    deb = b"paket" * 1000
    rel, routes = signed(updates.VPN, "1.1.0", {"bonys-vpn_1.1.0_all.deb": deb})
    got = []
    path = updates.download_verified(updates.VPN, rel, "bonys-vpn_1.1.0_all.deb", tmp_path, public_key=PUBLIC,
                                     opener=_opener(routes), progress=lambda d, t: got.append((d, t)))
    assert path.read_bytes() == deb and got[-1] == (len(deb), len(deb))
    assert oct(path.stat().st_mode)[-3:] == "644"


def test_download_rejects_wrong_checksum_signature_and_version(tmp_path):
    name = "bonys-vpn_1.1.0_all.deb"
    rel, routes = signed(updates.VPN, "1.1.0", {name: b"echt"})
    bad = dict(routes)
    bad[f"{VPN_DL}/v1.1.0/{name}"] = b"manipuliert"
    with pytest.raises(updates.UpdateError, match="Prüfsumme"):
        updates.download_verified(updates.VPN, rel, name, tmp_path, public_key=PUBLIC, opener=_opener(bad))
    assert not list(tmp_path.glob("*.deb*"))
    other = signing.PublicKey(KEY_ID, signing.ed25519_public_key(b"\x22" * 32)).text()
    with pytest.raises(updates.UpdateError, match="Signatur"):
        updates.download_verified(updates.VPN, rel, name, tmp_path, public_key=other, opener=_opener(routes))
    # echte, aber alte Signatur (andere Version im signierten Kommentar)
    old = dict(routes)
    old[f"{VPN_DL}/v1.1.0/SHA256SUMS.minisig"] = signing.sign(routes[f"{VPN_DL}/v1.1.0/SHA256SUMS"], SECRET,
                                                              KEY_ID, "Bony's VPN v1.0.0").encode()
    with pytest.raises(updates.UpdateError, match="anderen Version"):
        updates.download_verified(updates.VPN, rel, name, tmp_path, public_key=PUBLIC, opener=_opener(old))


def test_download_only_from_official_release(tmp_path):
    name = "bonys-vpn_1.1.0_all.deb"
    rel, routes = signed(updates.VPN, "1.1.0", {name: b"echt"})
    rel.assets[name] = updates.Asset(name, "https://example.com/bonys-vpn.deb")
    with pytest.raises(updates.UpdateError, match="offiziellen Release"):
        updates.download_verified(updates.VPN, rel, name, tmp_path, public_key=PUBLIC, opener=_opener(routes))
    unsigned = updates.Release("1.1.0", "v1.1.0", assets={name: updates.Asset(name, f"{VPN_DL}/v1.1.0/{name}")})
    with pytest.raises(updates.UpdateError, match="nicht signiert"):
        updates.download_verified(updates.VPN, unsigned, name, tmp_path, public_key=PUBLIC, opener=_opener(routes))


def test_deb_names():
    assert updates.VPN.deb_name("1.0.0", "arm64") == "bonys-vpn_1.0.0_all.deb"
    assert updates.AGENTS.deb_name("0.15.0", "arm64") == "bonys-agents_0.15.0_arm64.deb"
    assert updates.AGENTS.deb_name("0.15.0", "amd64") == "bonys-agents_0.15.0_amd64.deb"


# ---------------------------------------------------------------- Installieren (Bony's Agents, Updates)
def test_install_agents_from_release_when_no_apt_source(tmp_path, monkeypatch):
    name = f"bonys-agents_0.15.0_{updates.deb_arch()}.deb"
    rel, routes = signed(updates.AGENTS, "0.15.0", {name: b"agents"})
    latest = f"https://api.github.com/repos/{updates.AGENTS.repo}/releases/latest"
    raw = _release_json(updates.AGENTS.repo, "v0.15.0", [(u.rsplit("/", 1)[1], u) for u in routes])
    routes[latest] = raw
    monkeypatch.setattr(updates, "download_dir", lambda: tmp_path)
    monkeypatch.setattr(updates, "installed_version", lambda pkg: None)
    scripts, log = [], []

    def runner(script, log_fn):
        scripts.append(script)
        return 0

    v = updates.install_package(updates.AGENTS, log.append, opener=_opener(routes), sources_dir=tmp_path / "keine",
                                runner=runner, public_key=PUBLIC)
    assert v == "0.15.0"
    assert len(scripts) == 1 and f"apt-get install -y {tmp_path / name}" in scripts[0]
    assert not (tmp_path / name).exists()  # Download danach aufgeräumt
    assert any("Signatur und Prüfsumme in Ordnung" in line for line in log)


def test_install_uses_apt_source_when_configured(tmp_path, monkeypatch):
    src = tmp_path / "sources.list.d"
    src.mkdir()
    (src / "bonys-agents.sources").write_text("Types: deb\nURIs: https://x/apt/\nSuites: stable\n")
    monkeypatch.setattr(updates, "installed_version", lambda pkg: "0.15.0")
    scripts = []
    v = updates.install_package(updates.AGENTS, print, sources_dir=src,
                                runner=lambda s, log: scripts.append(s) or 0)
    assert v == "0.15.0" and len(scripts) == 1 and "apt-get install -y bonys-agents" in scripts[0]
    # Abbruch der Anmeldung: verständlicher Fehler
    with pytest.raises(updates.UpdateError, match="abgebrochen"):
        updates.install_package(updates.AGENTS, print, sources_dir=src, runner=lambda s, log: 126)


def test_apt_source_detection_and_text(tmp_path):
    assert not updates.apt_source_configured(tmp_path)
    (tmp_path / "bonys-vpn.sources").write_text(updates.apt_sources_text())
    assert updates.apt_source_configured(tmp_path)
    (tmp_path / "bonys-vpn.sources").write_text(updates.apt_sources_text() + "Enabled: no\n")
    assert not updates.apt_source_configured(tmp_path)
    fields = dict(line.split(": ", 1) for line in updates.apt_sources_text().splitlines()
                  if line and not line.startswith("#"))
    # gleiche Paketquelle wie Bony's Agents, eigene Suite (sonst „Conflicting values set for option Signed-By“)
    assert fields == {"Types": "deb", "URIs": "https://zappotex.github.io/BonysAgents/apt/", "Suites": "bonys-vpn",
                      "Components": "main", "Signed-By": "/usr/share/keyrings/bonys-vpn.gpg"}


# ---------------------------------------------------------------- Rechner
def test_agentpc_detection(tmp_path):
    marker, legacy = tmp_path / "agentpc", tmp_path / "50-bonys-vpn.rules"
    assert not host.is_agentpc(marker, legacy)
    legacy.write_text((ROOT / "tests/data/legacy-agent.rules").read_text())
    assert host.is_agentpc(marker, legacy)
    legacy.unlink()
    marker.write_text("x")
    assert host.is_agentpc(marker, legacy)


def test_polkit_rules_support():
    def fake(out):
        return lambda *a, **kw: subprocess.CompletedProcess(a, 0, out, "")
    assert host.rules_supported(fake("pkaction version 124\n"))
    assert host.rules_supported(fake("pkaction version 0.120\n"))
    assert not host.rules_supported(fake("pkaction version 0.105\n"))  # Ubuntu 22.04 / Mint 21


def test_status_without_password(tmp_path):
    def fake(*a, **kw):
        return subprocess.CompletedProcess(a, 0, "pkaction version 124\n", "")
    rules = tmp_path / "50-bonys-vpn.rules"
    assert not host.status_without_password((rules,), fake)
    install.build("bonys-vpn", tmp_path / "pkg")
    assert host.status_without_password((tmp_path / "pkg" / install.RULES,), fake)


def test_parse_exceptions():
    assert host.parse_exceptions("192.168.178.0/24, 10.0.0.5\n") == ["192.168.178.0/24", "10.0.0.5/32"]
    with pytest.raises(ValueError, match="kein Netz"):
        host.parse_exceptions("heimnetz")


def test_nm_wireguard_ignores_external(monkeypatch):
    monkeypatch.setattr(host.shutil, "which", lambda name, **kw: f"/usr/bin/{name}")

    def run(cmd, **kw):
        if cmd[0] == "nmcli":
            return subprocess.CompletedProcess(cmd, 0, "nm-tunnel:wireguard:/org/freedesktop/NetworkManager/A/1\n"
                                                       "wg0:wireguard:/org/freedesktop/NetworkManager/A/2\n"
                                                       "WLAN:802-11-wireless:/org/freedesktop/NetworkManager/A/3\n", "")
        flags = "u 129" if cmd[3].endswith("/2") else "u 1"  # wg0: von wg-quick, NetworkManager hat ihn nur übernommen
        return subprocess.CompletedProcess(cmd, 0, flags, "")
    assert host.nm_wireguard(run) == {"nm-tunnel"}


def test_agents_command(tmp_path, monkeypatch):
    monkeypatch.setattr(host, "AGENTS_COMMANDS", (tmp_path / "opt/BonysAgents",))
    assert host.agents_command(tmp_path) is None
    local = tmp_path / ".local/share/bonys-agents/app/bin/bonys-agents-gui"
    local.parent.mkdir(parents=True)
    local.write_text("#!/bin/sh\n")
    local.chmod(0o755)
    assert host.agents_command(tmp_path) == [str(local)]


def test_open_url_only_https(monkeypatch):
    with pytest.raises(ValueError):
        host.open_url("file:///etc/shadow")
    started = []
    monkeypatch.setattr(host.shutil, "which", lambda name: "/usr/bin/xdg-open" if name == "xdg-open" else None)
    monkeypatch.setattr(host.subprocess, "Popen", lambda cmd, **kw: started.append(cmd))
    host.open_url("https://www.youtube.com/@BonysAgents")
    assert started == [["/usr/bin/xdg-open", "https://www.youtube.com/@BonysAgents"]]


# ---------------------------------------------------------------- Paketskripte
def test_build_script():
    sh = (ROOT / "packaging/build-deb.sh").read_text()
    assert "Architecture: all" in sh
    for dep in ("python3-gi", "gir1.2-gtk-3.0", "wireguard-tools", "nftables", "pkexec | policykit-1"):
        assert dep in sh
    assert "Replaces: bonys-agents (<< 0.15)" in sh            # übernimmt Helfer, polkit-Aktion usw.
    assert "openresolv" not in sh.split("Description:")[0]      # nie automatisch (zerstört systemd-resolved)
    assert "--remove-legacy" in sh and "--migrate-agent-rule" in sh
    assert "Depends: bonys-vpn (= $VERSION)" in sh
    assert "/etc/apt/sources.list.d/bonys-vpn.sources" in sh
    assert "pyside" not in sh.lower() and "qt" not in sh.lower().replace("quelltext", "")


def test_keyring_is_the_apt_key_of_bonys_agents():
    key = (ROOT / "packaging/bonys-vpn-archive-keyring.asc").read_text()
    assert key.startswith("-----BEGIN PGP PUBLIC KEY BLOCK-----") and "PRIVATE" not in key


def test_version_constants():
    assert updates.Version.parse(__version__) is not None
    assert f"## {__version__}" in (ROOT / "CHANGELOG.md").read_text()

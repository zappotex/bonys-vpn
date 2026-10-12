# SPDX-License-Identifier: GPL-3.0-or-later
"""Nach neuen Versionen suchen und Pakete sicher installieren – für Bony's VPN selbst und für den
Knopf „Bony's Agents installieren“.

- Quelle: GitHub-Releases (API ohne Token, mit Zeitlimit). Ist GitHub nicht erreichbar, gibt es
  still „keine Updates“.
- Sicherheit: nur HTTPS und nur Dateien aus dem offiziellen Release. Jedes Release enthält
  SHA256SUMS mit minisign-Signatur (derselbe Schlüssel wie bei Bony's Agents). Vor jeder
  Installation werden Signatur, Version im signierten Kommentar und Prüfsumme geprüft.
- Linux: Ist die APT-Paketquelle eingerichtet (``bonys-agents.sources`` oder ``bonys-vpn.sources``),
  installiert ``apt-get`` aus ihr – sonst das geprüfte .deb aus dem Release. Beides über pkexec.
- Installiert wird nie ohne Rückfrage: Das macht die Oberfläche vorher.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import AGENTS_REPO, REPO, UPDATE_PUBLIC_KEY, __version__, signing

API_URL = "https://api.github.com"
TIMEOUT = 10                 # Sekunden für die Versionsabfrage
CHECK_INTERVAL = 24 * 3600   # automatische Suche höchstens einmal pro Tag
USER_AGENT = f"bonys-vpn/{__version__}"
SUMS_NAME = "SHA256SUMS"
SIG_NAME = "SHA256SUMS.minisig"
SOURCES_DIR = Path("/etc/apt/sources.list.d")
# Beide Projekte teilen sich eine Paketquelle (GitHub Pages von Bony's Agents). Bony's Agents trägt
# die Suite „stable“ ein, Bony's VPN die Suite „bonys-vpn“ – gleicher Inhalt, aber eigener Eintrag,
# damit apt nicht über zwei verschiedene „Signed-By“ für dieselbe Quelle stolpert.
APT_SOURCES = ("bonys-agents.sources", "bonys-vpn.sources")
APT_URL = "https://zappotex.github.io/BonysAgents/apt/"
APT_SUITE = "bonys-vpn"
APT_KEYRING = "/usr/share/keyrings/bonys-vpn.gpg"
_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+$")
SAFE_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"

Opener = Callable[..., object]  # wie urllib.request.urlopen (für Tests austauschbar)
Log = Callable[[str], None]


class UpdateError(RuntimeError):
    """Nicht möglich – mit verständlicher Meldung (Signatur, Prüfsumme, Download, Abbruch)."""


@dataclass(frozen=True)
class Product:
    package: str      # Debian-Paket
    repo: str         # GitHub „Besitzer/Name“
    title: str        # steht im signierten Kommentar („Bony's VPN v1.0.0“)
    arch_all: bool    # ein Paket für alle Architekturen?

    def deb_name(self, version: str, arch: str) -> str:
        return f"{self.package}_{version}_{'all' if self.arch_all else arch}.deb"


VPN = Product("bonys-vpn", REPO, "Bony's VPN", arch_all=True)
AGENTS = Product("bonys-agents", AGENTS_REPO, "Bony's Agents", arch_all=False)


def clean_env() -> dict[str, str]:
    env = dict(os.environ)
    for var in ("LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONPATH", "PYTHONHOME"):
        env.pop(var, None)
    return env


# ---------------------------------------------------------------- Versionen (Semantic Versioning)
_SEMVER = re.compile(r"^[vV]?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    pre: tuple[str, ...] = ()

    @classmethod
    def parse(cls, text: str) -> Version | None:
        m = _SEMVER.match(text.strip())
        if not m:
            return None
        pre = tuple(m.group(4).split(".")) if m.group(4) else ()
        return cls(int(m.group(1)), int(m.group(2)), int(m.group(3)), pre)

    def key(self) -> tuple:
        pre_key = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in self.pre)
        return (self.major, self.minor, self.patch, 0 if self.pre else 1, pre_key)

    def __lt__(self, other: Version) -> bool:
        return self.key() < other.key()

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}" + (f"-{'.'.join(self.pre)}" if self.pre else "")


def is_newer(candidate: str, current: str) -> bool:
    a, b = Version.parse(candidate), Version.parse(current)
    return bool(a and b and b < a)


# ---------------------------------------------------------------- Release-Daten
@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    size: int = 0


@dataclass
class Release:
    version: str
    tag: str
    html_url: str = ""
    notes: str = ""
    prerelease: bool = False
    assets: dict[str, Asset] = field(default_factory=dict)


def _request(url: str, accept: str = "application/vnd.github+json") -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})


def _release(data: dict) -> Release | None:
    if not isinstance(data, dict) or data.get("draft"):
        return None
    tag = str(data.get("tag_name") or "")
    version = Version.parse(tag)
    if version is None:
        return None
    assets = {}
    for a in data.get("assets") or []:
        if isinstance(a, dict) and a.get("name") and a.get("browser_download_url"):
            assets[a["name"]] = Asset(a["name"], a["browser_download_url"], int(a.get("size") or 0))
    return Release(str(version), tag, str(data.get("html_url") or ""), str(data.get("body") or ""),
                   bool(data.get("prerelease")) or bool(version.pre), assets)


def fetch_latest(product: Product = VPN, timeout: float = TIMEOUT, opener: Opener | None = None) -> Release | None:
    """Neueste Version laut GitHub (ohne Vorversionen). Wirft Netzwerk-/Formatfehler weiter."""
    if not _REPO_RE.match(product.repo):
        return None
    opener = opener or urllib.request.urlopen
    with opener(_request(f"{API_URL}/repos/{product.repo}/releases/latest"), timeout=timeout) as r:
        return _release(json.loads(r.read(4 * 1024 * 1024).decode("utf-8")))


def check(current: str = __version__, skip: str | None = None, opener: Opener | None = None) -> Release | None:
    """Gibt es eine neuere Version von Bony's VPN? None = nein (auch wenn GitHub nicht erreichbar ist)."""
    try:
        rel = fetch_latest(VPN, opener=opener)
    except Exception:  # noqa: BLE001 – die Update-Suche darf nie stören
        return None
    if rel is None or rel.prerelease or not is_newer(rel.version, current) or rel.version == skip:
        return None
    return rel


def release_page(product: Product = VPN) -> str:
    return f"https://github.com/{product.repo}/releases/latest"


# ---------------------------------------------------------------- Einstellungen
def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "bonys-vpn"


def load_settings() -> dict:
    try:
        data = json.loads((config_dir() / "settings.json").read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data: dict) -> None:
    d = config_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / ".settings.json.tmp"
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", "utf-8")
        os.replace(tmp, d / "settings.json")
    except OSError:
        pass  # Einstellungen sind Bequemlichkeit – nie ein Grund für einen Fehler


def set_setting(key: str, value) -> None:
    s = load_settings()
    s[key] = value
    save_settings(s)


def auto_check_due(now: float | None = None) -> bool:
    s = load_settings()
    now = time.time() if now is None else now
    last = float(s.get("update_last_check") or 0)
    return bool(s.get("update_auto_check", True)) and not 0 <= now - last < CHECK_INTERVAL


# ---------------------------------------------------------------- Herunterladen und prüfen
def _official(url: str, repo: str) -> bool:
    return url.lower().startswith(f"https://github.com/{repo.lower()}/releases/download/")


def _open_https(url: str, timeout: float, opener: Opener):
    r = opener(_request(url, accept="application/octet-stream"), timeout=timeout)
    final = r.geturl() if hasattr(r, "geturl") else url
    if not str(final).startswith("https://"):
        r.close()
        raise UpdateError("Download nicht über HTTPS – abgebrochen.")
    return r


def _fetch_small(url: str, opener: Opener, limit: int = 1024 * 1024) -> bytes:
    with _open_https(url, 30, opener) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise UpdateError("Unerwartet große Prüfsummen-Datei – abgebrochen.")
    return data


def parse_sums(text: str, filename: str) -> str:
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == filename and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            return parts[0].lower()
    raise UpdateError(f"{filename} steht nicht in der signierten Prüfsummen-Liste – abgebrochen.")


def download_verified(product: Product, rel: Release, asset_name: str, dest_dir: Path, *,
                      public_key: str | None = None, opener: Opener | None = None,
                      progress: Callable[[int, int], None] | None = None) -> Path:
    """Datei aus dem offiziellen Release laden – nur mit gültiger Signatur und passender Prüfsumme."""
    opener = opener or urllib.request.urlopen
    public_key = public_key or UPDATE_PUBLIC_KEY
    asset = rel.assets.get(asset_name)
    if asset is None:
        raise UpdateError(f"Das Release {rel.tag} enthält keine Datei {asset_name}.")
    sums, sig = rel.assets.get(SUMS_NAME), rel.assets.get(SIG_NAME)
    if sums is None or sig is None:
        raise UpdateError(f"Das Release {rel.tag} ist nicht signiert ({SUMS_NAME}/{SIG_NAME} fehlen) – "
                          "aus Sicherheitsgründen wird nichts installiert.")
    for a in (asset, sums, sig):
        if not _official(a.url, product.repo):
            raise UpdateError(f"{a.name} stammt nicht aus dem offiziellen Release – abgebrochen.")
    try:
        sums_data = _fetch_small(sums.url, opener)
        sig_text = _fetch_small(sig.url, opener).decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError) as e:
        raise UpdateError(f"Download fehlgeschlagen: {e}") from None
    try:
        trusted = signing.verify(sums_data, sig_text, public_key)
    except signing.SignatureError as e:
        raise UpdateError(f"Signaturprüfung fehlgeschlagen: {e} Es wird nichts installiert.") from None
    # Schutz vor echten, aber alten Dateien: Die Signatur muss genau diese Version nennen.
    if not {rel.tag, rel.version, f"v{rel.version}"} & set(trusted.split()):
        raise UpdateError(f"Die Signatur gehört zu einer anderen Version („{trusted}“) – abgebrochen.")
    want = parse_sums(sums_data.decode("utf-8", errors="replace"), asset_name)

    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / asset_name
    part = dest_dir / (asset_name + ".part")
    h = hashlib.sha256()
    try:
        with _open_https(asset.url, 60, opener) as r, part.open("wb") as out:
            total = int(getattr(r, "headers", {}).get("Content-Length") or asset.size or 0)
            done = 0
            while chunk := r.read(1024 * 1024):
                out.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    except (OSError, urllib.error.URLError) as e:
        part.unlink(missing_ok=True)
        raise UpdateError(f"Download fehlgeschlagen: {e}") from None
    if h.hexdigest() != want:
        part.unlink(missing_ok=True)
        raise UpdateError(f"Die Prüfsumme von {asset_name} stimmt nicht – Datei beschädigt oder verändert. "
                          "Es wird nichts installiert.")
    os.replace(part, target)
    target.chmod(0o644)  # apt liest lokale .deb als Benutzer _apt
    return target


# ---------------------------------------------------------------- Debian/Ubuntu/Mint
def deb_arch() -> str:
    """Debian-Architektur dieses Rechners (amd64, arm64 …)."""
    if shutil.which("dpkg"):
        try:
            out = subprocess.run(["dpkg", "--print-architecture"], capture_output=True, text=True, timeout=10,
                                 env=clean_env()).stdout.strip()
            if out:
                return out
        except (OSError, subprocess.SubprocessError):
            pass
    return {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine(), platform.machine())


def installed_version(package: str) -> str | None:
    """Installierte Version eines Debian-Pakets – None, wenn es nicht installiert ist."""
    if not shutil.which("dpkg-query"):
        return None
    try:
        r = subprocess.run(["dpkg-query", "-W", "-f=${db:Status-Status} ${Version}", package],
                           capture_output=True, text=True, timeout=15, env=clean_env())
    except (OSError, subprocess.SubprocessError):
        return None
    state, _, version = r.stdout.strip().partition(" ")
    return version if r.returncode == 0 and state == "installed" and version else None


def apt_source_configured(sources_dir: Path = SOURCES_DIR) -> bool:
    """Ist eine der beiden Paketquellen (Bony's Agents oder Bony's VPN) eingerichtet und nicht abgeschaltet?"""
    for name in APT_SOURCES:
        try:
            text = (sources_dir / name).read_text("utf-8")
        except OSError:
            continue
        if "URIs:" in text and not re.search(r"^\s*Enabled:\s*no\b", text, re.M | re.I):
            return True
    return False


def apt_sources_text() -> str:
    """Paketquelle im deb822-Format für /etc/apt/sources.list.d/bonys-vpn.sources (legt postinst an)."""
    return "\n".join([
        "# Bony's VPN – Updates über die Aktualisierungsverwaltung (Mint, Ubuntu, Debian).",
        "# Angelegt vom Paket bonys-vpn, entfernt bei „apt remove bonys-vpn“.",
        "# Gleiche Paketquelle wie Bony's Agents, eigene Suite – beide Einträge vertragen sich.",
        "# Abschalten: die Zeile „Enabled: no“ ergänzen – sie bleibt bei Updates erhalten.",
        "Types: deb",
        f"URIs: {APT_URL}",
        f"Suites: {APT_SUITE}",
        "Components: main",
        f"Signed-By: {APT_KEYRING}",
        "",
    ])


def apt_install_script(package: str, upgrade: bool = False) -> str:
    return "\n".join([
        "set -e",
        "export DEBIAN_FRONTEND=noninteractive",
        'apt-get update || echo "Warnung: Nicht alle Paketquellen ließen sich aktualisieren."',
        f"apt-get install -y {'--only-upgrade ' if upgrade else ''}{shlex.quote(package)}",
        "",
    ])


def deb_install_script(deb: Path) -> str:
    return f"set -e\nexport DEBIAN_FRONTEND=noninteractive\napt-get install -y {shlex.quote(str(deb))}\n"


def run_as_root(script: str, log: Log) -> int:
    """Skript über pkexec als root ausführen (grafische Passwortabfrage), Ausgabe zeilenweise an ``log``."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        cmd = ["sh", "-c", script]
    elif shutil.which("pkexec"):
        cmd = ["pkexec", "sh", "-c", script]
    else:
        raise UpdateError("pkexec fehlt – bitte das Paket „pkexec“ (bzw. „policykit-1“) installieren.")
    env = clean_env()
    env["PATH"] = SAFE_PATH
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            text=True, errors="replace", env=env)
    assert proc.stdout is not None
    for line in proc.stdout:
        if line.strip():
            log(line.rstrip())
    return proc.wait()


def download_dir() -> Path:
    d = Path(tempfile.mkdtemp(prefix="bonys-vpn-download-"))
    d.chmod(0o755)  # apt (Benutzer _apt) muss die Datei lesen können
    return d


def _check_rc(rc: int) -> None:
    if rc in (126, 127):
        raise UpdateError("Die Anmeldung wurde abgebrochen – nichts installiert.")
    if rc != 0:
        raise UpdateError(f"Die Installation ist fehlgeschlagen (Code {rc}). Details stehen oben im Protokoll.")


def install_package(product: Product, log: Log, rel: Release | None = None, upgrade: bool = False,
                    opener: Opener | None = None, progress: Callable[[int, int], None] | None = None,
                    sources_dir: Path = SOURCES_DIR, runner=run_as_root, public_key: str | None = None) -> str:
    """Paket installieren bzw. aktualisieren: über die Paketquelle, sonst das geprüfte .deb aus dem Release.
    Gibt die danach installierte Version zurück."""
    if apt_source_configured(sources_dir):
        log(f"Installiere {product.package} aus der Paketquelle (fragt nach deinem Passwort) …")
        rc = runner(apt_install_script(product.package, upgrade), log)
        if rc in (126, 127):
            _check_rc(rc)
        now = installed_version(product.package)
        if rc == 0 and now and (rel is None or not is_newer(rel.version, now.split("-")[0])):
            return now
        log("Die Paketquelle bietet diese Version (noch) nicht an – lade das Paket direkt aus dem Release.")
    if rel is None:
        log(f"Suche die neueste Version von {product.title} auf GitHub …")
        try:
            rel = fetch_latest(product, opener=opener)
        except Exception as e:  # noqa: BLE001
            raise UpdateError(f"GitHub ist nicht erreichbar: {e}") from None
        if rel is None:
            raise UpdateError(f"Auf GitHub gibt es noch kein Release von {product.title}.")
    name = product.deb_name(rel.version, deb_arch())
    log(f"Lade {name} und prüfe Signatur und Prüfsumme …")
    deb = download_verified(product, rel, name, download_dir(), opener=opener, progress=progress,
                            public_key=public_key)
    log("Signatur und Prüfsumme in Ordnung. Installiere (fragt nach deinem Passwort) …")
    _check_rc(runner(deb_install_script(deb), log))
    shutil.rmtree(deb.parent, ignore_errors=True)
    return installed_version(product.package) or rel.version

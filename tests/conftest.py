# SPDX-License-Identifier: GPL-3.0-or-later
"""Gemeinsame Test-Hilfen: Die Anmeldung beim Bony-Netz (``konto.json``, geteilt mit Bony's Agents) liegt in allen
Tests in einem Testordner – die echte Datei wird nie angefasst."""

import pytest


@pytest.fixture(autouse=True)
def bony_netz_ordner(tmp_path, monkeypatch):
    monkeypatch.setenv("BONY_NETZ_ORDNER", str(tmp_path / "bony-netz"))

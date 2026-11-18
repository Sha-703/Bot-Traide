"""Lecture des klines Binance 1 s — partagée entre scripts.

Données publiques, aucun compte : https://data.binance.vision/
"""

from __future__ import annotations

import io
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

def jours_selectionnes(jours: int) -> list:
    """`jours` derniers jours complets (aujourd'hui exclu)."""
    aujourdhui = datetime.now(timezone.utc).date()
    return [aujourdhui - timedelta(days=i) for i in range(jours, 0, -1)]


def iterer_ticks(prefixe: str, jours: list, cache: Path) -> Iterator[tuple[float, float]]:
    """Flux (epoch_s, close) streamé jour par jour, sans tout charger mémoire."""
    for jour in jours:
        nom = f"{prefixe}-1s-{jour.isoformat()}.zip"
        chemin = cache / nom
        if not chemin.exists():
            cache.mkdir(parents=True, exist_ok=True)
            url = f"https://data.binance.vision/data/spot/daily/klines/{prefixe}/1s/{nom}"
            print(f"  download {nom} ...", flush=True)
            urllib.request.urlretrieve(url, chemin)
        try:
            with zipfile.ZipFile(chemin) as z:
                with z.open(z.namelist()[0]) as f:
                    for ligne in io.TextIOWrapper(f, encoding="utf-8", errors="replace"):
                        brut = ligne.rstrip("\n").split(",")
                        if len(brut) < 6 or not brut[0].isdigit():
                            continue
                        try:
                            close = float(brut[4])
                        except ValueError:
                            continue
                        if close <= 0:
                            continue
                        yield float(int(brut[0]) // 1_000_000), close
        except zipfile.BadZipFile:
            # Téléchargement partiel : on ignore ce jour et on le redemandera
            # au prochain lancement.
            print(f"  archive corrompue ignorée : {nom}", flush=True)

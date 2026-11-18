"""Verrou mono-instance : deux consoles sur le même journal = données fausses.

Deux moteurs qui écrivent dans `journal/trades.csv` produisent des lignes
entrelacées, des compteurs de risque divergents et des statistiques qui ne
mesurent plus rien. Le verrou est un fichier `journal/.verrou` contenant le PID
du processus propriétaire : au démarrage, si ce PID est encore vivant, la
console refuse de s'ouvrir avec un message explicite.

Le fichier est supprimé à l'arrêt propre. Après un plantage, le PID est mort :
le verrou est considéré comme périmé et repris automatiquement.
"""

from __future__ import annotations

import os
from pathlib import Path

NOM_FICHIER = ".verrou"


class InstanceDejaOuverte(RuntimeError):
    """Une autre console utilise déjà ce dossier de journal."""


def pid_vivant(pid: int) -> bool:
    """Vrai si le processus existe encore (sans envoyer de signal)."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        SYNCHRONIZE = 0x00100000
        WAIT_TIMEOUT = 0x00000102
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False
        try:
            return (
                ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
            )
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class VerrouInstance:
    """Verrou consultatif, suffisant pour éviter deux moteurs dans un dossier."""

    def __init__(self, dossier: Path) -> None:
        self.chemin = Path(dossier) / NOM_FICHIER
        self.pid = os.getpid()
        self._pris = False

    def prendre(self) -> None:
        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        if self.chemin.exists():
            autre = self._lire_pid()
            if autre and autre != self.pid and pid_vivant(autre):
                raise InstanceDejaOuverte(
                    f"Une autre console utilise déjà ce journal "
                    f"(PID {autre}). Fermez-la avant de relancer : deux moteurs "
                    f"écriraient dans le même fichier et fausseraient les "
                    f"compteurs. Si ce processus est bloqué, supprimez "
                    f"{self.chemin.name} manuellement."
                )
        self.chemin.write_text(f"{self.pid}\n", encoding="utf-8")
        self._pris = True

    def liberer(self) -> None:
        if not self._pris:
            return
        try:
            if self.chemin.exists() and self._lire_pid() == self.pid:
                self.chemin.unlink()
        except OSError:
            pass
        self._pris = False

    def _lire_pid(self) -> int:
        try:
            return int(self.chemin.read_text(encoding="utf-8").split()[0])
        except (OSError, ValueError, IndexError):
            return 0

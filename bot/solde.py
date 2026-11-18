"""Lecture du solde affiché : simulé, ou celui du compte plateforme.

Le solde affiché dans la console a deux origines possibles :

    - « simulé » : le capital papier du moteur (mode démo) ;
    - « plateforme » : le solde réel du compte, lu en LECTURE SEULE.

Pourquoi un module dédié ? Parce qu'on refuse d'inventer un chiffre. Afficher
un solde qui ressemble à celui de la plateforme alors qu'il n'en est pas un
serait la pire des erreurs pour un outil de trading : cela donnerait une fausse
impression de contrôle.

Tant qu'aucun adaptateur n'est branché, `SoldePlateforme.lire()` retourne
`(None, explication)` et l'interface affiche explicitement « indisponible ».
Le jour où vous écrivez l'adaptateur, remplacez la méthode `lire` : le reste
de la chaîne (API, interface) n'a pas à changer.
"""

from __future__ import annotations

from typing import Any


class SourceSolde:
    """Contrat minimal : lire un solde et expliquer son origine."""

    nom = "SIMULE"

    async def lire(self) -> tuple[float | None, str]:  # pragma: no cover - interface
        raise NotImplementedError


class SoldeSimule(SourceSolde):
    """Capital papier : aucune connexion, aucune prétention de solde réel."""

    nom = "SIMULE"

    def __init__(self, capital: float) -> None:
        self.capital = capital

    async def lire(self) -> tuple[float | None, str]:
        return round(self.capital, 2), "Capital papier du simulateur."


class SoldePlateforme(SourceSolde):
    """Solde du compte réel, en lecture seule.

    Nécessite le protocole privé de la plateforme : non fourni. On ne simule
    donc PAS une valeur crédible, on retourne une absence assumée.
    """

    nom = "PLATEFORME"

    def __init__(self, session: Any) -> None:
        self.session = session

    async def lire(self) -> tuple[float | None, str]:
        if self.session is None or not getattr(self.session, "configuree", False):
            return None, "Aucun compte connecté : impossible de lire le solde."
        return (
            None,
            "Solde du compte indisponible : la lecture du solde exige "
            "l'adaptateur de protocole, non livré avec ce projet "
            "(voir SECURITY.md). Aucun chiffre n'est inventé.",
        )


def construire_source_solde(mode: str, capital: float, session: Any) -> SourceSolde:
    """Fabrique la source de solde cohérente avec le mode courant."""
    if mode == "OBSERVATION":
        return SoldePlateforme(session)
    return SoldeSimule(capital)

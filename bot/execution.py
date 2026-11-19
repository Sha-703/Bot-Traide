"""Exécution des ordres : le point le plus sensible du système.

C'est ici qu'on décide si un signal devient un vrai ordre. Trois verrous :

    1. Le mode. Le code livré n'exécute QUE des ordres simulés (papier).
       En mode réel, `MoteurExecution` lève `ExecutionReelleBloquee` : il
       n'existe volontairement aucun chemin de code capable d'engager de
       l'argent réel sans que VOUS n'écriviez et ne validiez un adaptateur
       courtier dédié.

    2. Le risque. Aucun ordre ne part sans validation préalable par
       `GestionRisque` (montant, quota, perte journalière).

    3. Le journal. Chaque ordre est écrit sur disque AVANT et APRÈS
       résolution, ce qui rend toute activité auditable.

Responsabilité : l'utilisateur reste seul responsable de l'usage de ce code
et du respect des conditions d'utilisation de la plateforme.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .journalisation import Journal, TradeJournalise
from .reglages import Reglages


def nom_strategie(settings) -> str:
    """Retourne un libellé lisible de la stratégie active."""
    if getattr(settings, "strategie", "ema") == "bollinger":
        return f"Bollinger{settings.bollinger_periode}/k={settings.bollinger_k}"
    return f"EMA{settings.ema_rapide}/{settings.ema_lente}"


from .mt5_adapter import (
    envoyer_ordre_marche,
    fermer_position,
    MT5Indisponible,
    pnl_approx_eurusd,
    position_du_symbole,
)
from .risque import GestionRisque
from .strategie import Direction, Signal


class ExecutionRefusee(Exception):
    """L'ordre ne peut pas être exécuté (risque, mode, état interne)."""


class ExecutionReelleBloquee(ExecutionRefusee):
    """Tentative d'exécution réelle alors que le code est verrouillé en démo.

    Ce comportement est VOLONTAIRE. Pour aller en réel, il faut écrire un
    adaptateur courtier explicite, le relire, le tester sur un compte démo
    de la plateforme, puis activer manuellement le mode réel.
    """


@dataclass
class Ordre:
    """Ordre binaire simplifié (call/put avec expiration)."""

    identifiant: str
    actif: str
    direction: Direction
    montant: float
    prix_entree: float
    expiration_s: int
    ouverture_ts: float
    echeance_ts: float
    mode: str
    raison: str = ""
    ticket_mt5: int = 0
    volume_mt5: float = 0.0


@dataclass
class ResultatOrdre:
    resultat: str  # GAGNANT | PERDANT | REMBOURSE
    pnl: float
    prix_sortie: float


class CourtierDemo:
    """Courtier simulé : aucune connexion, aucun argent réel.

    Résolution binaire classique :
        prix_sortie > prix_entree -> gain  = montant * payout
        prix_sortie < prix_entree -> perte = -montant
        égalité parfaite          -> remboursement (pnl = 0)
    """

    nom = "SIMULATEUR_PAPIER"

    def __init__(self, payout: float) -> None:
        if not 0 < payout <= 1:
            raise ValueError("Le payout doit être compris entre 0 exclu et 1 inclus.")
        self.payout = payout

    def ouvrir(self, signal: Signal, montant: float, expiration_s: int) -> Ordre:
        maintenant = signal.horodatage
        return Ordre(
            identifiant=f"SIM-{uuid.uuid4().hex[:10]}",
            actif="",  # renseigné par l'orchestrateur
            direction=signal.direction,
            montant=round(montant, 2),
            prix_entree=signal.prix,
            expiration_s=expiration_s,
            ouverture_ts=maintenant,
            echeance_ts=maintenant + expiration_s,
            mode="DEMO",
            raison=signal.raison,
        )

    def resoudre(self, ordre: Ordre, prix_sortie: float) -> ResultatOrdre:
        if abs(prix_sortie - ordre.prix_entree) < 1e-12:
            return ResultatOrdre("REMBOURSE", 0.0, prix_sortie)
        gagne = (
            prix_sortie > ordre.prix_entree
            if ordre.direction is Direction.ACHAT
            else prix_sortie < ordre.prix_entree
        )
        if gagne:
            return ResultatOrdre("GAGNANT", round(ordre.montant * self.payout, 2), prix_sortie)
        return ResultatOrdre("PERDANT", -round(ordre.montant, 2), prix_sortie)


class CourtierReel:
    """Emplacement réservé à VOTRE adaptateur courtier réel.

    Volontairement non implémenté. Pour l'activer plus tard :
        1. choisir une bibliothèque, lire son code source intégralement ;
        2. implémenter `ouvrir` et `resoudre` avec le protocole réel ;
        3. tester en démo plateforme, puis seulement envisager le réel ;
        4. assumer le risque : aucun gain n'est garanti, la perte totale
           du capital est possible.
    """

    nom = "REEL_NON_IMPLEMENTE"

    def __init__(self, *_: Any, **__: Any) -> None:
        raise ExecutionReelleBloquee(
            "Aucun adaptateur courtier réel n'est livré avec ce projet. "
            "Le mode réel est bloqué par conception. Voir SECURITY.md."
        )


class MoteurExecution:
    """Chef d'orchestre de l'ordre : validation risque -> ordre -> journal."""

    def __init__(
        self,
        settings: Settings,
        risque: GestionRisque,
        journal: Journal,
        reglages: Reglages | None = None,
    ) -> None:
        self.settings = settings
        self.risque = risque
        self.journal = journal
        self.reglages = reglages or Reglages.depuis_settings(settings)
        self.courtier = CourtierDemo(self.reglages.payout)
        self.ordre_en_cours: Ordre | None = None
        # Instrument effectif (suit le flux : BTCUSDT avec Binance).
        self.actif: str = settings.actif
        # Verrou n°1 : propriété de l'instance, modifiable uniquement par le
        # moteur (et jamais par une requête HTTP sans contrôle explicite).
        self.mode_reel: bool = settings.mode_reel
        self.mode_label: str = "REEL" if settings.mode_reel else "DEMO"

    # ------------------------------------------------------------------ #
    def executer(self, signal: Signal, capital: float) -> Ordre:
        """Transforme un signal en ordre, après tous les contrôles.

        Lève ExecutionRefusee (ou sous-classe) en cas de blocage.
        """
        # --- Verrou n°1 : le mode -------------------------------------
        if self.mode_reel:
            raise ExecutionReelleBloquee(
                "Exécution réelle désactivée : ce moteur ne passe que des ordres "
                "simulés. MODE_REEL=True n'autorise rien tant qu'aucun adaptateur "
                "courtier n'a été implémenté et audité."
            )

        if self.ordre_en_cours is not None:
            raise ExecutionRefusee(
                "Un ordre est déjà en cours : un seul trade à la fois (pas de "
                "martingale, pas d'exposition multiple)."
            )

        if not signal.exploitable:
            raise ExecutionRefusee("Signal neutre : rien à exécuter.")

        # --- Verrou n°2 : le risque -----------------------------------
        # Le montant demandé est plafonné par le budget calculé à partir du
        # capital courant : jamais de sizing fixe qui ignorerait les pertes.
        budget = self.risque.budget_trade(capital=capital)
        montant = min(self.reglages.montant_trade, budget)
        self.risque.valider(montant, capital)

        # --- Verrou n°3 : création + journalisation -------------------
        if self.settings.mt5_exe and self.settings.mt5_demo:
            # Chemin DÉMO MT5 — l'ordre part réellement sur le compte démo.
            # Les paramètres d'ordre (lot, SL/TP) viennent des réglages
            # dynamiques appliqués depuis la console (hot-reloadable).
            try:
                mt5_info = envoyer_ordre_marche(
                    self.actif,
                    signal.direction,
                    self.reglages.mt5_volume,
                    self.reglages.mt5_sl_points,
                    self.reglages.mt5_tp_points,
                    chemin=self.settings.mt5_chemin_terminal,
                    commentaire="PocketLab",
                )
            except MT5Indisponible as exc:
                self.journal.evenement(
                    "erreur", "mt5", f"Ordre MT5 refusé : {exc}"
                )
                raise ExecutionRefusee(f"MT5 : {exc}") from exc
            ordre = Ordre(
                identifiant=f"MT5-{mt5_info['ticket']}",
                actif=self.actif,
                direction=signal.direction,
                montant=self.reglages.montant_trade,
                prix_entree=mt5_info["prix"],
                expiration_s=self.reglages.duree_expiration_s,
                ouverture_ts=signal.horodatage,
                echeance_ts=signal.horodatage + self.reglages.duree_expiration_s,
                mode="MT5_DEMO",
                raison=signal.raison,
                ticket_mt5=int(mt5_info["ticket"] or 0),
                volume_mt5=float(mt5_info["volume"]),
            )
            self.journal.evenement(
                "info",
                "execution",
                f"Ordre DÉMO MT5 ouvert {ordre.direction.value} "
                f"{ordre.volume_mt5} lot {ordre.actif}",
                identifiant=ordre.identifiant,
                prix_entree=ordre.prix_entree,
                sl=mt5_info["sl"],
                tp=mt5_info["tp"],
            )
            self.risque.enregistrer_trade(self.reglages.montant_trade)
            self.ordre_en_cours = ordre
            return ordre

        ordre = self.courtier.ouvrir(
            signal, montant, self.reglages.duree_expiration_s
        )
        ordre.actif = self.actif
        ordre.mode = self.mode_label
        self.risque.enregistrer_trade(montant)
        self.ordre_en_cours = ordre
        self.journal.evenement(
            "info",
            "execution",
            f"Ordre simulé ouvert {ordre.direction.value} {ordre.montant:.2f}$",
            identifiant=ordre.identifiant,
            prix_entree=ordre.prix_entree,
        )
        return ordre

    # ------------------------------------------------------------------ #
    def resoudre(self, prix_sortie: float, capital: float) -> tuple[Ordre, ResultatOrdre, float]:
        """Clôture l'ordre en cours et met à jour risque + journal.

        Retourne (ordre, résultat, nouveau capital).
        """
        ordre = self.ordre_en_cours
        if ordre is None:
            raise ExecutionRefusee("Aucun ordre en cours à résoudre.")

        if getattr(ordre, "ticket_mt5", 0):
            # Chemin DÉMO MT5 : fermer la position si elle est encore ouverte,
            # sinon "calculer le prix de sortie" à partir de tick actuel.
            position = position_du_symbole(ordre.actif)
            if position is not None:
                fermer_position(ordre.actif, self.settings.mt5_chemin_terminal)
            pnl = pnl_approx_eurusd(
                ordre.prix_entree, prix_sortie, ordre.volume_mt5, ordre.direction
            )
            if pnl > 0:
                resultat = ResultatOrdre("GAGNANT", round(pnl, 2), prix_sortie)
            elif pnl < 0:
                resultat = ResultatOrdre("PERDANT", round(pnl, 2), prix_sortie)
            else:
                resultat = ResultatOrdre("REMBOURSE", 0.0, prix_sortie)
        else:
            resultat = self.courtier.resoudre(ordre, prix_sortie)
        self.risque.enregistrer_resultat(resultat.pnl)
        nouveau_capital = capital + resultat.pnl

        self.journal.enregistrer(
            TradeJournalise(
                horodatage_utc=Journal.maintenant(),
                mode=getattr(ordre, "ticket_mt5", 0) and "MT5_DEMO" or self.mode_label,
                actif=ordre.actif,
                direction=ordre.direction.value,
                montant=ordre.montant,
                expiration_s=ordre.expiration_s,
                prix_entree=ordre.prix_entree,
                prix_sortie=prix_sortie,
                resultat=resultat.resultat,
                pnl=resultat.pnl,
                solde_apres=round(nouveau_capital, 2),
                strategie=nom_strategie(self.settings),
                commentaire=ordre.raison,
            )
        )
        self.ordre_en_cours = None
        return ordre, resultat, nouveau_capital

    def annuler(self) -> None:
        """Abandon propre (arrêt du bot pendant un ordre ouvert)."""
        if self.ordre_en_cours:
            self.journal.evenement(
                "avertissement",
                "execution",
                "Ordre annulé avant échéance (arrêt du bot).",
                identifiant=self.ordre_en_cours.identifiant,
            )
            self.ordre_en_cours = None

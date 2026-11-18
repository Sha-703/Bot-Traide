"""Configuration centralisée du bot.

Règle d'or de ce module : rien de sensible n'est écrit en dur.
Le SSID de session est lu depuis le fichier `.env` (non versionné), et le
mode réel n'est jamais implicite.

Sécurité :
    - Si la variable MODE_REEL est absente -> le bot refuse de démarrer.
    - Si MODE_REEL=True mais que la confirmation textuelle manque -> refus.
    - Le SSID n'est jamais exposé dans les logs ni dans l'API du dashboard.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Racine du projet (le dossier qui contient `bot/`, `.env`, `requirements.txt`).
RACINE = Path(__file__).resolve().parent.parent
CHEMIN_ENV = RACINE / ".env"

# Valeur exacte attendue dans CONFIRMATION_REELLE pour autoriser le mode réel.
JETON_CONFIRMATION = "JE CONFIRME LE RISQUE REEL"


class ConfigError(RuntimeError):
    """Erreur de configuration bloquante : le bot ne doit pas démarrer."""


def _lire_bool(nom: str, obligatoire: bool = False) -> bool | None:
    """Lit un booléen de façon stricte (True/False, 1/0, oui/non)."""
    brut = os.getenv(nom)
    if brut is None or brut.strip() == "":
        if obligatoire:
            raise ConfigError(
                f"Variable d'environnement obligatoire absente : {nom}. "
                "Copiez `.env.example` vers `.env` puis définissez-la explicitement."
            )
        return None

    valeur = brut.strip().lower()
    if valeur in {"true", "1", "oui", "yes", "on"}:
        return True
    if valeur in {"false", "0", "non", "no", "off"}:
        return False
    raise ConfigError(
        f"Valeur invalide pour {nom} : '{brut}'. Attendu : True ou False."
    )


def _lire_float(nom: str, defaut: float) -> float:
    """Lit un flottant, avec validation de positivité."""
    brut = os.getenv(nom)
    if brut is None or brut.strip() == "":
        return defaut
    try:
        valeur = float(brut.replace(",", "."))
    except ValueError as exc:
        raise ConfigError(f"{nom} doit être un nombre, reçu : '{brut}'.") from exc
    if valeur <= 0:
        raise ConfigError(f"{nom} doit être strictement positif (reçu {valeur}).")
    return valeur


def _lire_int(nom: str, defaut: int) -> int:
    """Lit un entier, avec validation de positivité."""
    valeur = _lire_float(nom, float(defaut))
    return int(valeur)


@dataclass(frozen=True)
class Settings:
    """Photo immuable de la configuration validée."""

    # --- Mode d'exécution ---------------------------------------------------
    mode_reel: bool

    # --- Identifiants (jamais journalisés) ----------------------------------
    ssid: str | None = field(default=None, repr=False)

    # --- Flux de prix (indépendant du mode d'exécution) ---------------------
    # "simule"  : générateur interne (aucun réseau)
    # "binance" : prix réels publics Binance, lecture seule, ordres papier
    flux_prix: str = "simule"
    actif_binance: str = "BTCUSDT"

    # --- MT5 (Weltrade, compte DÉMO) --------------------------------------
    # Lecture seule par défaut. MT5_EXE=true active l'envoi d'ordres RÉELS
    # sur le compte DÉMO (jamais sur un compte réel).
    mt5_demo: bool = False
    mt5_exe: bool = False
    mt5_symbole: str = "EURUSD_i"
    mt5_chemin_terminal: str | None = None
    mt5_volume: float = 0.01
    mt5_sl_points: int = 50
    mt5_tp_points: int = 100

    # --- Marché -------------------------------------------------------------
    actif: str = "EURUSD"
    duree_expiration_s: int = 60
    payout: float = 0.87  # rendement versé par la plateforme en cas de gain

    # --- Capital et sizing --------------------------------------------------
    capital_initial: float = 1000.0
    montant_trade: float = 1.0
    taille_max_position: float = 5.0
    fraction_risque_par_trade: float = 0.01  # 1 % du capital par trade max

    # --- Garde-fous de risque ----------------------------------------------
    perte_max_journaliere: float = 30.0
    trades_max_par_jour: int = 20
    cooldown_signal_s: int = 30

    # --- Stratégie ----------------------------------------------------------
    ema_rapide: int = 9
    ema_lente: int = 21
    seuil_ecart_bps: float = 1.0  # écart minimal entre EMAs (points de base)
    intervalle_tick_s: float = 1.0

    # --- Infrastructure -----------------------------------------------------
    port_dashboard: int = 8080
    hote_dashboard: str = "127.0.0.1"
    dossier_journal: Path = RACINE / "journal"
    # Accélération de la simulation : 1.0 = temps réel.
    facteur_vitesse: float = 1.0
    graine_aleatoire: int | None = None

    # ------------------------------------------------------------------ #
    @property
    def mode(self) -> str:
        return "REEL" if self.mode_reel else "DEMO"

    def ssid_masque(self) -> str:
        """Aperçu du SSID sûr à afficher (jamais la valeur complète)."""
        if not self.ssid:
            return "(non fourni)"
        if len(self.ssid) <= 8:
            return "•" * len(self.ssid)
        return f"{self.ssid[:4]}…{self.ssid[-4:]}"

    def resume_public(self) -> dict[str, Any]:
        """Vue sans secret, destinée au dashboard et aux logs."""
        return {
            "mode": self.mode,
            "actif": self.actif,
            "flux_prix": self.flux_prix,
            "actif_binance": self.actif_binance,
            "mt5_demo": self.mt5_demo,
            "mt5_exe": self.mt5_exe,
            "mt5_symbole": self.mt5_symbole,
            "duree_expiration_s": self.duree_expiration_s,
            "payout": self.payout,
            "capital_initial": self.capital_initial,
            "montant_trade": self.montant_trade,
            "taille_max_position": self.taille_max_position,
            "fraction_risque_par_trade": self.fraction_risque_par_trade,
            "perte_max_journaliere": self.perte_max_journaliere,
            "trades_max_par_jour": self.trades_max_par_jour,
            "ema_rapide": self.ema_rapide,
            "ema_lente": self.ema_lente,
            "ssid_apercu": self.ssid_masque(),
        }


def charger_config(chemin_env: Path | None = None) -> Settings:
    """Charge `.env` puis valide tout avant de construire la configuration.

    Lève ConfigError si une règle de sécurité n'est pas respectée.
    """
    chemin = chemin_env or CHEMIN_ENV
    # `override=False` : les variables déjà présentes dans le shell gagnent.
    load_dotenv(dotenv_path=chemin, override=False)

    # 1) Le mode doit être déclaré EXPLICITEMENT, même pour la démo.
    mode_reel = _lire_bool("MODE_REEL", obligatoire=True)

    ssid = (os.getenv("SSID") or "").strip() or None

    # 1 bis) Flux de prix : explicite, jamais deviné.
    flux_prix = (os.getenv("FLUX_PRIX") or "simule").strip().lower()
    if flux_prix in {"simule", "simulé", "demo", "démo"}:
        flux_prix = "simule"
    elif flux_prix in {"binance", "reel", "réel"}:
        flux_prix = "binance"
    else:
        raise ConfigError(
            f"FLUX_PRIX invalide : '{flux_prix}'. Attendu : simule ou binance."
        )
    actif_binance = (os.getenv("ACTIF_BINANCE") or "BTCUSDT").strip().upper()

    # 1 ter) MT5 (Weltrade, compte DÉMO) : explicite, lecture seule.
    mt5_demo = (os.getenv("MT5_DEMO") or "").strip().lower() in {
        "true", "1", "oui", "yes", "on",
    }
    mt5_symbole = (os.getenv("MT5_SYMBOLE") or "EURUSD_i").strip()
    mt5_chemin_terminal = (os.getenv("MT5_CHEMIN_TERMINAL") or "").strip() or None
    mt5_volume = _lire_float("MT5_VOLUME", 0.01)
    mt5_sl_points = _lire_int("MT5_SL_POINTS", 50)
    mt5_tp_points = _lire_int("MT5_TP_POINTS", 100)
    mt5_exe = (os.getenv("MT5_EXE") or "").strip().lower() in {
        "true", "1", "oui", "yes", "on",
    }
    if mt5_exe and not mt5_demo:
        raise ConfigError(
            "MT5_EXE=true exige MT5_DEMO=true : l'envoi d'ordres réels "
            "sur compte réel est verrouillé par design et aucune exécution "
            "n'a lieu sans compte démo."
        )

    # 2) En mode réel, un SSID est indispensable et une double confirmation
    #    est exigée : un simple `MODE_REEL=True` ne suffit pas.
    if mode_reel:
        if not ssid:
            raise ConfigError(
                "MODE_REEL=True mais SSID est vide : impossible de se connecter."
            )
        confirmation = (os.getenv("CONFIRMATION_REELLE") or "").strip()
        if confirmation != JETON_CONFIRMATION:
            raise ConfigError(
                "Mode réel verrouillé. Pour l'activer, définissez "
                f'CONFIRMATION_REELLE="{JETON_CONFIRMATION}" dans `.env`.\n'
                "Rappel : le mode démo doit être validé sur un historique "
                "significatif avant tout passage en réel."
            )

    ema_rapide = _lire_int("EMA_RAPIDE", 9)
    ema_lente = _lire_int("EMA_LENTE", 21)
    if ema_rapide >= ema_lente:
        raise ConfigError(
            f"EMA_RAPIDE ({ema_rapide}) doit être inférieure à EMA_LENTE ({ema_lente})."
        )

    dossier_journal = Path(
        os.getenv("DOSSIER_JOURNAL") or (RACINE / "journal")
    )
    dossier_journal.mkdir(parents=True, exist_ok=True)

    graine = os.getenv("GRAINE_ALEATOIRE")
    settings = Settings(
        mode_reel=mode_reel,
        ssid=ssid,
        flux_prix=flux_prix,
        actif_binance=actif_binance,
        mt5_demo=mt5_demo,
        mt5_symbole=mt5_symbole,
        mt5_chemin_terminal=mt5_chemin_terminal,
        mt5_volume=mt5_volume,
        mt5_sl_points=mt5_sl_points,
        mt5_tp_points=mt5_tp_points,
        actif=(os.getenv("ACTIF") or "EURUSD").strip().upper(),
        duree_expiration_s=_lire_int("DUREE_EXPIRATION_S", 60),
        payout=_lire_float("PAYOUT", 0.87),
        capital_initial=_lire_float("CAPITAL_INITIAL", 1000.0),
        montant_trade=_lire_float("MONTANT_TRADE", 1.0),
        taille_max_position=_lire_float("TAILLE_MAX_POSITION", 5.0),
        fraction_risque_par_trade=_lire_float("FRACTION_RISQUE_PAR_TRADE", 0.01),
        perte_max_journaliere=_lire_float("PERTE_MAX_JOURNALIERE", 30.0),
        trades_max_par_jour=_lire_int("TRADES_MAX_PAR_JOUR", 20),
        cooldown_signal_s=_lire_int("COOLDOWN_SIGNAL_S", 30),
        ema_rapide=ema_rapide,
        ema_lente=ema_lente,
        seuil_ecart_bps=_lire_float("SEUIL_ECART_BPS", 1.0),
        intervalle_tick_s=_lire_float("INTERVALLE_TICK_S", 1.0),
        port_dashboard=_lire_int("PORT_DASHBOARD", 8080),
        hote_dashboard=(os.getenv("HOTE_DASHBOARD") or "127.0.0.1").strip(),
        dossier_journal=dossier_journal,
        facteur_vitesse=_lire_float("FACTEUR_VITESSE", 1.0),
        graine_aleatoire=int(graine) if graine else None,
    )

    # 3) Cohérence : le sizing par trade ne doit pas dépasser le plafond.
    if settings.montant_trade > settings.taille_max_position:
        raise ConfigError(
            "MONTANT_TRADE dépasse TAILLE_MAX_POSITION : corrigez le sizing."
        )

    return settings

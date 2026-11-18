"""Mesure la stratégie sur des PRIX RÉELS, sans compte et sans risque.

Ce script ne se connecte à aucun compte, n'envoie aucun ordre et n'écrit rien
dans `journal/trades.csv`. Il consomme le flux public Binance (lecture seule)
et rejoue la stratégie exactement comme la console : un signal validé ouvre un
trade papier, résolu à l'échéance par comparaison de prix.

Exemples :
    python scripts/mesurer_flux_reel.py --duree 30
    python scripts/mesurer_flux_reel.py --duree 120 --expiration 10
    python scripts/mesurer_flux_reel.py --duree 0        # jusqu'à Ctrl+C

Rappel de lecture : avec un payout de 87 %, il faut 53,5 % de réussite pour
être à l'équilibre. En dessous, l'espérance est négative par construction.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import math
import sys
import time
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from bot.connexion import FluxBinance  # noqa: E402
from bot.strategie import Direction, StrategieMoyennesMobiles  # noqa: E402

DOSSIER_MESURES = RACINE / "mesures"


def wilson(gagnants: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Intervalle de confiance à 95 % (Wilson) — honnête sur petits échantillons."""
    if total <= 0:
        return (0.0, 1.0)
    p = gagnants / total
    denominateur = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominateur
    marge = (
        z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominateur
    )
    return (max(0.0, centre - marge), min(1.0, centre + marge))


def _ligne_separation(caractere: str = "─", largeur: int = 68) -> str:
    return caractere * largeur


def _afficher_bilan(etat: dict, payout: float, entete: str = "BILAN") -> None:
    total = etat["gagnants"] + etat["perdants"]
    taux = 100 * etat["gagnants"] / total if total else 0.0
    seuil = 100 / (1 + payout)
    bas, haut = wilson(etat["gagnants"], total)
    esperance = etat["pnl"] / total if total else 0.0

    print(_ligne_separation())
    print(f"{entete} — {etat['symbole']} · {etat['ticks']} ticks · "
          f"{etat['duree_s'] / 60:.1f} min")
    print(f"  trades       : {total}  (gagnants {etat['gagnants']} · "
          f"perdants {etat['perdants']} · remboursés {etat['rembourses']})")
    print(f"  réussite     : {taux:.1f} %   IC 95 % : "
          f"[{100 * bas:.1f} % ; {100 * haut:.1f} %]")
    print(f"  seuil rentab.: {seuil:.1f} % (payout {100 * payout:.0f} %)")
    print(f"  P/L papier   : {etat['pnl']:+.2f} $   "
          f"espérance/trade : {esperance:+.3f} $")
    if total and etat["pnl"] > 0:
        verdict = "positif sur cet échantillon"
    elif total:
        verdict = "négatif sur cet échantillon"
    else:
        verdict = "aucun trade — échantillon vide"
    print(f"  verdict      : {verdict}")
    if total and (haut - bas) > 0.25:
        print("  ⚠ échantillon trop petit : l'intervalle est très large, "
              "aucune conclusion fiable.")
    print(_ligne_separation())


async def mesurer(args: argparse.Namespace) -> None:
    DOSSIER_MESURES.mkdir(parents=True, exist_ok=True)
    chemin = DOSSIER_MESURES / time.strftime("mesure-%Y%m%d-%H%M%S.csv")
    champ = [
        "horodatage_utc", "symbole", "direction", "prix_entree",
        "prix_sortie", "resultat", "pnl",
    ]

    strategie = StrategieMoyennesMobiles(
        ema_rapide=args.ema_rapide,
        ema_lente=args.ema_lente,
        seuil_ecart_bps=args.seuil_ecart,
        cooldown_secondes=args.cooldown,
    )
    flux = FluxBinance(symbole=args.symbole, intervalle_s=1.0)
    etat = {
        "symbole": args.symbole.upper(),
        "ticks": 0,
        "gagnants": 0,
        "perdants": 0,
        "rembourses": 0,
        "pnl": 0.0,
        "duree_s": 0.0,
    }
    trade: dict | None = None
    debut = time.monotonic()
    fin = debut + args.duree * 60 if args.duree > 0 else float("inf")

    print(f"Flux public Binance · {etat['symbole']} · expiration {args.expiration} s · "
          f"payout {100 * args.payout:.0f} %")
    print("Aucun compte, aucun ordre, aucun impact sur le journal de trading.")
    print("Démarrage (chauffe de la stratégie)…\n")

    fichier = chemin.open("w", newline="", encoding="utf-8")
    writer = csv.DictWriter(fichier, delimiter=";", fieldnames=champ)
    writer.writeheader()

    try:
        async for tick in flux.flux():
            etat["ticks"] += 1

            # 1) Résolution du trade ouvert, comme le moteur.
            if trade is not None and tick.horodatage >= trade["echeance"]:
                sortie = tick.prix
                if abs(sortie - trade["entree"]) < 1e-12:
                    resultat, pnl = "REMBOURSE", 0.0
                    etat["rembourses"] += 1
                else:
                    gagne = (
                        sortie > trade["entree"]
                        if trade["direction"] == Direction.ACHAT
                        else sortie < trade["entree"]
                    )
                    if gagne:
                        resultat = "GAGNANT"
                        pnl = round(args.montant * args.payout, 2)
                        etat["gagnants"] += 1
                    else:
                        resultat = "PERDANT"
                        pnl = -args.montant
                        etat["perdants"] += 1
                etat["pnl"] += pnl
                writer.writerow(
                    {
                        "horodatage_utc": time.strftime(
                            "%Y-%m-%dT%H:%M:%S", time.gmtime(tick.horodatage)
                        ),
                        "symbole": etat["symbole"],
                        "direction": trade["direction"].value,
                        "prix_entree": f"{trade['entree']:.2f}",
                        "prix_sortie": f"{sortie:.2f}",
                        "resultat": resultat,
                        "pnl": f"{pnl:.2f}",
                    }
                )
                fichier.flush()
                total = etat["gagnants"] + etat["perdants"]
                taux = 100 * etat["gagnants"] / total if total else 0.0
                print(
                    f"#{total:>2} {trade['direction'].value:<5} "
                    f"{trade['entree']:.2f} → {sortie:.2f} · {resultat:<9} "
                    f"{pnl:+.2f} $ · réussite {taux:5.1f} % "
                    f"· cumul {etat['pnl']:+.2f} $"
                )
                trade = None

            # 2) La stratégie est nourrie à chaque tick, même trade ouvert.
            signal = strategie.maj(tick.prix, tick.horodatage)

            # 3) Un seul trade à la fois, comme la console.
            if trade is None and signal is not None and signal.exploitable:
                trade = {
                    "direction": signal.direction,
                    "entree": tick.prix,
                    "echeance": tick.horodatage + args.expiration,
                }

            etat["duree_s"] = time.monotonic() - debut
            if time.monotonic() >= fin:
                break
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\nInterruption demandée — bilan partiel :")
    finally:
        fichier.close()
        await flux.fermer()

    print()
    _afficher_bilan(etat, args.payout)
    print(f"Journal de mesure : {chemin}")
    print("Ces chiffres décrivent des prix réels, mais restent un échantillon : "
          "ne pas en tirer une promesse de rentabilité.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mesure la stratégie sur les prix réels Binance (lecture seule)."
    )
    parser.add_argument("--symbole", default="BTCUSDT")
    parser.add_argument("--duree", type=float, default=30.0,
                        help="durée en minutes (0 = jusqu'à Ctrl+C)")
    parser.add_argument("--expiration", type=int, default=60,
                        help="durée du trade papier, en secondes")
    parser.add_argument("--payout", type=float, default=0.87,
                        help="rendement simulé en cas de gain (0,87 = 87 %%)")
    parser.add_argument("--montant", type=float, default=1.0)
    parser.add_argument("--ema-rapide", type=int, default=9)
    parser.add_argument("--ema-lente", type=int, default=21)
    parser.add_argument("--seuil-ecart", type=float, default=1.0,
                        help="écart minimal entre EMA, en points de base")
    parser.add_argument("--cooldown", type=int, default=30,
                        help="délai minimal entre deux signaux, en secondes")
    args = parser.parse_args()

    if not 0 < args.payout <= 1:
        parser.error("--payout doit être compris entre 0 exclu et 1.")
    if args.expiration < 1:
        parser.error("--expiration doit être d'au moins 1 seconde.")

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        asyncio.run(mesurer(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

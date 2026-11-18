"""Campagne de test : 100 trades simulés, répétés sur plusieurs graines.

Objectif pédagogique : montrer qu'un échantillon de 100 trades ne prouve
RIEN, et que le résultat varie fortement d'une graine à l'autre.

Usage :
    python scripts/simuler_100_trades.py            # 100 trades x 5 graines
    python scripts/simuler_100_trades.py --trades 300 --graines 8
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import replace
from pathlib import Path

# Permet d'exécuter le script depuis n'importe quel dossier.
RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from bot.config import charger_config  # noqa: E402
from bot.engine import Moteur  # noqa: E402
from bot.journalisation import Journal  # noqa: E402


async def campagne(nb_trades: int, graine: int) -> dict:
    """Exécute une campagne en temps virtuel et retourne ses indicateurs."""
    settings = charger_config()
    settings = replace(
        settings,
        mode_reel=False,  # sécurité : jamais de réel dans ce script
        facteur_vitesse=1000.0,
        graine_aleatoire=graine,
    )
    # Un journal par graine : les campagnes ne se mélangent pas.
    journal = Journal(settings.dossier_journal / "campagnes", f"graine_{graine}.csv")
    moteur = Moteur(settings, journal=journal)
    moteur.max_trades = nb_trades
    moteur.demarrer_en_tache()
    await moteur.attendre_fin()
    stats = moteur.journal.statistiques()
    return {
        "graine": graine,
        "trades": stats["total"],
        "taux": stats["taux_reussite"],
        "pnl": stats["pnl_total"],
        "dd": stats["drawdown_max"],
        "pf": stats["profit_factor"],
        "esperance": stats["esperance"],
    }


def main() -> int:
    for flux in (sys.stdout, sys.stderr):
        try:
            if (flux.encoding or "").lower().replace("-", "") != "utf8":
                flux.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--trades", type=int, default=100)
    parseur.add_argument("--graines", type=int, default=5)
    args = parseur.parse_args()

    print(f"\n  {args.trades} trades simulés × {args.graines} graines "
          f"(mode démo, aucun ordre réel)\n")
    entete = f"{'graine':>7} {'trades':>7} {'réussite':>9} {'P&L':>10} {'drawdown':>10} {'PF':>6} {'espérance':>10}"
    print("  " + entete)
    print("  " + "-" * len(entete))

    resultats = []
    for graine in range(1, args.graines + 1):
        r = asyncio.run(campagne(args.trades, graine))
        resultats.append(r)
        print(
            f"  {r['graine']:>7} {r['trades']:>7} {r['taux']:>8.1f}% "
            f"{r['pnl']:>+10.2f} {r['dd']:>10.2f} {r['pf']:>6.2f} {r['esperance']:>+10.2f}"
        )

    if resultats:
        positifs = sum(1 for r in resultats if r["pnl"] > 0)
        print(f"\n  Sessions profitables : {positifs}/{len(resultats)}")
        print("  Rappel : aucune conclusion de rentabilité ne peut être tirée")
        print("  d'un générateur de prix synthétique. Ce test valide la")
        print("  plomberie (risque, exécution, journal), pas la stratégie.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

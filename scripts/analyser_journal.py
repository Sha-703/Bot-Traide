"""Analyse honnête d'un journal de trades : verdict statistique, pas d'illusion.

Usage :
    python scripts/analyser_journal.py
    python scripts/analyser_journal.py --fichier journal/campagnes/trades-20261117-230412.csv

Ce que le script refuse de faire : conclure sur un échantillon minuscule. Il
affiche l'intervalle de confiance à 95 % et la probabilité d'obtenir un tel
résultat par hasard, puis un verdict explicite (concluant / non concluant).
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from bot.statistiques import (  # noqa: E402
    p_au_moins,
    p_value_exacte,
    seuil_rentabilite,
    wilson,
)


@dataclass
class Ligne:
    ts: str
    actif: str
    mode: str
    direction: str
    montant: float
    resultat: str
    pnl: float
    solde: float


def charger(chemin: Path) -> list[Ligne]:
    lignes: list[Ligne] = []
    with chemin.open("r", newline="", encoding="utf-8-sig") as f:
        for brut in csv.DictReader(f, delimiter=";"):
            if not (brut.get("horodatage_utc") or "").strip():
                continue
            try:
                lignes.append(
                    Ligne(
                        ts=brut["horodatage_utc"],
                        actif=brut.get("actif") or "?",
                        mode=brut.get("mode") or "?",
                        direction=brut.get("direction") or "?",
                        montant=float(brut.get("montant") or 0),
                        resultat=brut.get("resultat") or "?",
                        pnl=float(brut.get("pnl") or 0),
                        solde=float(brut.get("solde_apres") or 0),
                    )
                )
            except ValueError:
                continue
    return lignes


def analyser(lignes: list[Ligne], titre: str) -> None:
    print("=" * 72)
    print(f"{titre}")
    print("=" * 72)
    if not lignes:
        print("Aucun trade. Rien à analyser.\n")
        return

    actifs = Counter(l.actif for l in lignes)
    modes = Counter(l.mode for l in lignes)
    print(f"Période   : {lignes[0].ts} → {lignes[-1].ts}")
    print(f"Actifs    : {', '.join(f'{a} ×{n}' for a, n in actifs.items())}")
    print(f"Mode      : {', '.join(f'{m} ×{n}' for m, n in modes.items())}")

    # Payout déduit des gains réels : pnl_gagnant / mise.
    payouts = [l.pnl / l.montant for l in lignes if l.pnl > 0 and l.montant > 0]
    payout = sum(payouts) / len(payouts) if payouts else 0.0
    seuil = 100 / (1 + payout) if payout > 0 else float("nan")

    gagnants = sum(1 for l in lignes if l.pnl > 0)
    perdants = sum(1 for l in lignes if l.pnl < 0)
    nuls = sum(1 for l in lignes if l.pnl == 0)
    n = gagnants + perdants
    taux = 100 * gagnants / n if n else 0.0
    pnl_total = sum(l.pnl for l in lignes)
    gains = sum(l.pnl for l in lignes if l.pnl > 0)
    pertes = -sum(l.pnl for l in lignes if l.pnl < 0)

    # Drawdown et série perdante.
    sommet = lignes[0].solde - lignes[0].pnl
    dd_max = 0.0
    serie = serie_max = 0
    for l in lignes:
        sommet = max(sommet, l.solde)
        dd_max = max(dd_max, sommet - l.solde)
        serie = serie + 1 if l.pnl < 0 else 0
        serie_max = max(serie_max, serie)

    bas, haut = wilson(gagnants, n)
    p_hasard = p_au_moins(gagnants, n, seuil / 100) if n else 1.0
    p_bilateral = p_value_exacte(gagnants, n, seuil / 100) if n else 1.0

    print(f"Mise      : {min(l.montant for l in lignes):.2f} $ → "
          f"{max(l.montant for l in lignes):.2f} $")
    print(f"Payout    : {100 * payout:.1f} %  → seuil de rentabilité "
          f"{seuil:.1f} %")
    print("-" * 72)
    print(f"Trades résolus : {n}  (gagnants {gagnants} · perdants {perdants}"
          f" · remboursés {nuls})")
    print(f"Réussite       : {taux:.1f} %   IC 95 % : "
          f"[{100 * bas:.1f} % ; {100 * haut:.1f} %]")
    print(f"P/L            : {pnl_total:+.2f} $   espérance/trade : "
          f"{pnl_total / n:+.3f} $")
    print(f"Drawdown max   : -{dd_max:.2f} $   série perdante max : {serie_max}")
    print(f"Profit factor  : {(gains / pertes if pertes else float('inf')):.2f}")
    print("-" * 72)

    if n == 0:
        print("Verdict : aucun trade exploitable.\n")
        return

    print(f"Si la stratégie n'avait AUCUN avantage ({seuil:.1f} % attendu) :")
    print(f"  · probabilité de faire au moins {gagnants}/{n} : "
          f"{100 * p_hasard:.1f} %")
    print(f"  · p-value bilatérale : {p_bilateral:.3f} "
          f"({'significatif' if p_bilateral < 0.05 else 'non significatif'} à 5 %)")

    # Verdict : trois conditions cumulatives.
    ic_contient_seuil = bas <= seuil / 100 <= haut
    sous_seuil = haut < seuil / 100
    au_dessus = bas > seuil / 100

    print("-" * 72)
    if n < 30:
        verdict = (
            f"NON CONCLUANT — {n} trades, c'est trop peu. L'intervalle "
            f"[{100 * bas:.0f} % ; {100 * haut:.0f} %] est si large qu'il "
            f"contient aussi bien l'échec que la réussite."
        )
    elif sous_seuil and p_bilateral < 0.05:
        verdict = (
            "NÉGATIF — la réussite est significativement sous le seuil de "
            "rentabilité : cette stratégie perd de l'argent par construction."
        )
    elif au_dessus and p_bilateral < 0.05:
        verdict = (
            "POSITIF — au-dessus du seuil avec significativité statistique. "
            "Reste à confirmer hors échantillon (walk-forward) avant tout euro."
        )
    else:
        verdict = (
            "INDÉTERMINÉ — l'échantillon ne permet pas de distinguer un "
            f"avantage réel du hasard (IC 95 % = [{100 * bas:.0f} % ; "
            f"{100 * haut:.0f} %] autour du seuil de {seuil:.0f} %)."
        )
    if ic_contient_seuil and n >= 30:
        verdict += " L'intervalle contient le seuil."
    print(f"VERDICT : {verdict}")

    # Détail par actif : deux mondes ne se mélangent pas.
    if len(actifs) > 1:
        print("-" * 72)
        print("Par actif (ne jamais additionner des marchés différents) :")
        par_actif: dict[str, list[Ligne]] = defaultdict(list)
        for l in lignes:
            par_actif[l.actif].append(l)
        for actif, groupe in par_actif.items():
            g = sum(1 for x in groupe if x.pnl > 0)
            p = sum(1 for x in groupe if x.pnl < 0)
            t = g + p
            b, h = wilson(g, t)
            print(
                f"  · {actif:<9} {t:>3} trades · {g} G / {p} P · "
                f"{100 * g / t if t else 0:5.1f} % "
                f"(IC [{100 * b:.0f} ; {100 * h:.0f}]) · "
                f"P/L {sum(x.pnl for x in groupe):+.2f} $"
            )
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fichier", type=Path, default=RACINE / "journal" / "trades.csv")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if not args.fichier.exists():
        sys.exit(f"Fichier introuvable : {args.fichier}")
    analyser(charger(args.fichier), f"JOURNAL : {args.fichier.name}")


if __name__ == "__main__":
    main()

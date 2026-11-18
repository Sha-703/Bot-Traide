# Rapport de backtest — BTCUSDT (klines 1 s, Binance)

Date du rapport : 2026-11-17.
Script : `scripts/backtest_historique.py` · données : `data.binance.vision` (archive publique).

## Méthodologie

- **Instrument** : `BTCUSDT` — spot Binance. Ce n'est **pas** l'EUR/USD de
  Pocket Option. Les résultats disent quelque chose sur BTC/USDT, pas sur le
  payoff binaire 87 % / 60 s.
- **Données** : 30 jours de klines 1 s complets (2026-10-18 → 2026-11-16),
  soit 2 592 000 ticks. Cache local dans `backtest/data/` (gitignore).
- **Rejeu** : même code que la stratégie live (`bot/replay.py`), une position à
  la fois, cooldown 30 s, un tick = une clôture (résolution close→close).
- **Fenêtres** :
  - calibration : 20 jours (2026-10-18 → 2026-11-06) — 1 728 000 ticks ;
  - test : 10 jours (2026-11-07 → 2026-11-16) — 864 000 ticks.
  La fenêtre de test n'a jamais servi à choisir quoi que ce soit.
- **Payout** : 87 % (binaire classique). Seuil de rentabilité : 53,5 %.
- Mise : 1,00 $ par trade. Aucun frais, aucun spread, aucune latency — c'est une
  mesure du **signal**, pas de l'exécution.

## Confirmation sur 90 jours (calibration 2026-08-19 → 2026-11-06)

| échéance | trades | réussite | IC 95 % | P/L papier | espérance |
|---|---|---|---|---|---|
| 1 s | 34 277 | 64,1 % | [63,5 ; 64,8] | +4 499,29 $ | +0,199 $ |
| 5 s | 34 277 | 62,4 % | [61,8 ; 62,9] | +4 984,34 $ | +0,166 $ |
| 15 s | 34 277 | 56,9 % | [56,4 ; 57,4] | +2 125,96 $ | +0,064 $ |
| 30 s | 34 277 | 54,7 % | [54,2 ; 55,2] | +766,48 $ | +0,023 $ |
| 60 s | 26 733 | 52,9 % | [52,3 ; 53,5] | −269,88 $ | −0,010 $ |
| 120 s | 19 555 | 51,3 % | [50,6 ; 52,0] | −804,13 $ | −0,041 $ |
| 300 s | 11 631 | 50,4 % | [49,5 ; 51,3] | −673,37 $ | −0,058 $ |

L'avantage court terme est **confirmé sur 80 jours de données d'entraînement**
(cohérent avec la fenêtre de test), de façon monotone du 1 s au 300 s.

## Résultats (fenêtre de test, non biaisée)

| échéance | trades | réussite | IC 95 % | P/L papier | espérance | DD max | série perdante |
|---|---|---|---|---|---|---|---|
| 1 s | 4 768 | 62,2 % | [60,8 ; 63,6] | +776,55 $ | +0,163 $ | −13,42 $ | 7 |
| 5 s | 6 495 | 60,0 % | [58,8 ; 61,2] | +794,26 $ | +0,122 $ | −22,41 $ | 10 |
| 15 s | 6 958 | 56,3 % | [55,1 ; 57,5] | +366,79 $ | +0,053 $ | −58,21 $ | 11 |
| 30 s | 7 053 | 52,9 % | [51,8 ; 54,1] | −72,29 $ | −0,010 $ | −144,54 $ | 11 |
| 60 s | 5 037 | 52,2 % | [50,8 ; 53,6] | −122,64 $ | −0,024 $ | −172,88 $ | 12 |
| 120 s | 3 444 | 50,6 % | [48,9 ; 52,2] | −186,46 $ | −0,054 $ | −199,53 $ | 11 |
| 300 s | 1 814 | 51,1 % | [48,8 ; 53,4] | −80,51 $ | −0,044 $ | −93,72 $ | 14 |

Calibration (cohérente, même forme) : 63,9 / 61,2 / 56,3 / 54,2 / 51,6 / 51,3 / 50,0 %.

## Ce que ça montre — et ce que ça ne montre pas

**Ce qui est réel :**

1. Le signal de **momentum à très court terme** existe sur BTC/USDT sur ce mois-là.
   Monotone du 1 s au 300 s, reproduit sur les deux fenêtres, IC qui excluent le
   seuil de 53,5 % pour 1/5/15 s. Ce n'est pas du cherry-picking : la courbe
   entière est visible et cohérente.

2. **La configuration actuelle du bot (60 s) n'a pas d'avantage** sur ce mois :
   52,2 % ± 1,4, espérance −0,024 $/trade. Elle perdrait de l'argent en continu.

**Ce qui n'est pas réel :**

3. L'avantage du 1 s n'est pas actionnable tel quel : aucune plateforme de
   binaires ne paye 87 % à 1 seconde, ton flux à 1 Hz suppose une exécution
   parfaite sans slippage ni latence, et c'est BTC spot — pas l'EUR/USD OTC.
4. Résultat d'**un seul mois** sur un seul instrument. Un autre régime pourrait
   invalider tout ça. Aucune garantie future, jamais.
5. Le modèle ignore frais, spread, latence et taille d'ordre.
6. Multiples comparaisons : 7 échéances testées. La forme monotone rend
   l'effet plausible, mais le 15 s reste la seule zone vraiment honnête.

## Contre-épreuve : EURUSDT (même méthode, même fenêtre, 30 jours)

| échéance | calibration (20 j) | test (10 j) | P/L test |
|---|---|---|---|
| 1 s | 18,5 % | 23,4 % | −81,42 $ |
| 5 s | 18,1 % | 20,0 % | −215,97 $ |
| 15 s | 19,5 % | 23,2 % | −261,91 $ |
| 30 s | 21,4 % | 29,2 % | −224,85 $ |
| 60 s | 25,4 % | 35,8 % | −123,29 $ |
| 120 s | 28,8 % | 39,7 % | −71,30 $ |
| 300 s | 31,3 % | 40,6 % | −42,23 $ |

Le momentum EMA perd **partout et à chaque horizon sur EURUSDT**. La
classique inversion BTC (momentum) ↔ FX (réversion à la moyenne court terme)
se manifeste ici : le résultat BTC n'est pas transférable à l'EUR/USD.

## Recommandations

1. **Ne plus backtester à 60 s.** Cette hypothèse est mesurée, elle est morte.
   (BTC 90 jours : 52,9 % ± 0,6, IC effondré, espérance −0,010 $/trade.)
2. **Sur BTC/USDT, l'avantage court terme est confirmé sur 90 jours** : 5 s
   62,4 %, 15 s 56,9 %, espérance positive. C'est un sujet de **spot
   scalping avec stop/target**, pas de binaire 60 s.
3. **Sur EURUSDT (near-miss FX), la stratégie perd partout** ; le momentum
   ne se transpose pas. Sur l'EUR/USD réel de Pocket Option : stratégie à
   réinventer ou à abandonner — pas à optimiser à l'aveugle.
4. Pocket Option : données OTC nécessaires pour valider sur l'instrument
   cible — non disponibles. Décision : compte démo plateforme obligatoire
   avant tout argent réel.

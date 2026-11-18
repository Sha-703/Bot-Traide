# Rapport sweep de candidats — 2026-11-17

Objectif : comparer plusieurs règles candidates dans des conditions identiques
(one position à la fois, cooldown 30 s, payout 87 %, mise 1 $, close→close).

## BTCUSDT (30 jours, test hors calibration)

| règle | échéance | trades | réussite | IC 95 % | P/L test | espérance |
|---|---|---|---|---|---|---|
| EMA 9/21 (base) | 5 s | 6 495 | 60,0 % | [58,8 ; 61,2] | +794,26 $ | +0,122 $ |
| EMA 9/21 (base) | 15 s | 6 958 | 56,3 % | [55,1 ; 57,5] | +366,79 $ | +0,053 $ |
| EMA 9/21 (base) | 60 s | 5 037 | 52,2 % | [50,8 ; 53,6] | −122,64 $ | −0,024 $ |
| RSI 14 | 5 s | 15 717 | 38,7 % | — | −4 336,18 $ | −0,276 $ |
| RSI 7 | 5 s | 18 588 | 43,0 % | — | −3 641,09 $ | −0,196 $ |
| Bollinger k=2 | 5 s | 15 784 | 33,5 % | — | −5 902,92 $ | −0,374 $ |
| Donchian 20 | 5 s | 19 978 | 54,4 % | [53,7 ; 55,0] | +330,20 $ | +0,017 $ |
| Donchian 20 | 60 s | 12 103 | 53,0 % | [52,1 ; 53,9] | −112,56 $ | −0,009 $ |

## EURUSDT (30 jours, test hors calibration)

| règle | échéance | trades | réussite | IC 95 % | P/L test | espérance |
|---|---|---|---|---|---|---|
| EMA 9/21 (base) | 5 s | 345 | 20,0 % | [16,1 ; 24,5] | −215,97 $ | −0,626 $ |
| EMA 9/21 (base) | 60 s | 372 | 35,8 % | [31,1 ; 40,7] | −123,29 $ | −0,331 $ |
| RSI 14 | 5 s | 6 652 | 90,6 % | [89,9 ; 91,3] | +4 616,62 $ | +0,694 $ |
| RSI 7 | 5 s | 7 580 | 90,8 % | [90,1 ; 91,4] | +5 289,34 $ | +0,698 $ |
| Bollinger k=2 | 5 s | 6 873 | 93,7 % | [93,1 ; 94,3] | +5 171,67 $ | +0,752 $ |
| Bollinger k=3 | 5 s | 5 324 | 93,9 % | [93,2 ; 94,5] | +4 026,00 $ | +0,756 $ |
| Donchian 20 | 5 s | 2 629 | 16,3 % | [15,0 ; 17,8] | −1 826,77 $ | −0,695 $ |

## Interprétation honnête

1. **EURUSDT 5–15 s avec réversion (RSI / Bollinger) : 84–94 % de réussite,
   mais c'est un artefact de mesure**, pas une opportunité :
   - 87 % de payout supposé — irrealiste à 5 s (réels ~50–60 %) ;
   - aucun spread, aucun frais, aucun écart bid-ask, aucune contrainte broker ;
   - plusieurs milliers d'ordres par jour — aucun broker n'accepterait le rythme ;
   - un seul régime de marché (EUR/USD en micro-variabilité) — résultat non transférable.
2. **BTCUSDT (tours courts) reste profitable en 5–15 s** (momentum EMA) ;
   le Donchian 20 est marginal (54,4 %, proche du seuil).
3. **BTC 60 s à 30 s : mort.** Sur EURUSDT, la stratégie perd à tous les horizons.
4. Les règles universelles n'existent pas — il faut faire valider chaque candidat
   sur données hors échantillon + frais + exécution réelle.
5. Le profil des meilleurs signaux (5–15 s) montre que l'edge réside dans la
   micro-structure et non dans un "cerveau prédictif".

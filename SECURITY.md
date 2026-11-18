# Guide de sécurité — Pocket Lab

Ce document part d'un constat simple : **un SSID de session est un mot de passe déguisé.**
Quiconque le possède peut agir sur le compte. Toute l'hygiène du projet découle de cette phrase.

---

## 1. Stocker le SSID sans se mettre en danger

### Ce qu'il ne faut jamais faire
- ❌ L'écrire dans le code, dans un commit, dans une capture d'écran, dans un ticket.
- ❌ Le coller dans un chat ou une IA en ligne (il finit dans les logs de conversation).
- ❌ Le partager entre plusieurs machines ou l'envoyer par mail.
- ❌ Le conserver des semaines : la session expire de toute façon.

### Ce que fait ce projet
| Mesure | Implémentation |
|---|---|
| Fichier dédié non versionné | `.env` (dans `.gitignore`), modèle `.env.example` sans secret |
| Jamais dans les logs | `Journal` ne reçoit aucune donnée d'authentification |
| Jamais dans l'API | `Settings.resume_public()` n'expose que `ssid_masque()` → `abcd…wxyz` |
| Jamais dans `repr()` | Champ déclaré `field(repr=False)` |
| Double confirmation | `CONFIRMATION_REELLE` texte exact, sinon refus de démarrer |
| Verrou d'exécution | `ExecutionReelleBloquee` même si tout est correctement configuré |

### Le panneau « Connexion » : la session sans disque ni mot de passe

La console propose deux chemins, dans cet ordre de préférence :

1. **« Se connecter avec votre navigateur »** (recommandé). Une fenêtre Edge
   s'ouvre sur la page de connexion de la plateforme ; *vous* tapez email,
   mot de passe et captcha. La console ne voit jamais ces informations — elle
   attend la redirection de succès, relit uniquement le cookie
   `po_session` du navigateur, et le place en mémoire vive. C'est exactement
   le flux OAuth utilisé par les CLIs GitHub/Google.
2. **Coller un jeton manuellement** (replié par défaut). Même discipline :
   mémoire vive uniquement, masque `abcd…wxyz`, effacement en un clic.

Ce que la console n'implémente pas, par choix :

- aucun formulaire email/mot de passe : aucun bot sérieux n'en a besoin ;
- aucun solveur de captcha (ni API, ni service tiers) ;
- aucune connexion « avec robot » : la page de connexion reste pilotée par
  l'humain.

Les trames WebSocket échangées par *votre* navigateur sont capturées
localement (`.audit/frames_capture_*.jsonl`) pour permettre d'écrire le
client : **ce fichier contient votre session ; supprimez-le après usage**.
Une fois le protocole reproduit, ce dossier ne doit plus contenir aucune
capture.

Le jeton est effacé par le bouton *Effacer* ou à l'arrêt du serveur.
Le test de connexion ouvre un **vrai WebSocket** et distingue « serveur
joignable » (handshake socket.io reçu) de « jeton validé » — il affiche
`JOIGNABLE`, jamais « connecté ».

### Durcissement supplémentaire (recommandé si vous allez plus loin)
1. **Chiffrement au repos** : stocker le SSID dans le gestionnaire de secrets de l'OS
   (`Windows Credential Manager` via `keyring`), pas en clair dans `.env`.
2. **Compte dédié** : jamais votre compte principal ; capital strictement limité.
3. **Rotation** : nouveau SSID à chaque session, révocation par déconnexion de la
   session navigateur après usage.
4. **Droits fichiers** : `.env` lisible par votre seul utilisateur Windows.
   ```powershell
   icacls .env /inheritance:r
   icacls .env /grant:r "$($env:USERNAME):(R,W)"
   ```
5. **Pas de cloud** : ce projet n'a aucune raison de tourner sur un VPS tiers ; le
   SSID y serait exposé à un administrateur de plus.

### En cas de fuite soupçonnée
1. Déconnecter immédiatement toutes les sessions de la plateforme.
2. Changer le mot de passe du compte.
3. Vérifier l'historique des transactions.
4. Purger l'historique shell qui contiendrait le jeton.
5. Considérer le capital du compte comme compromise tant que non vérifié.

---

## 2. Auditer une bibliothèque tierce (BinaryOptionsToolsV2, pocketoptionapi-async…)

Ces bibliothèques ne sont pas des dépendances officielles. Un `pip install` exécute du code
arbitraire avec **vos** droits. Procédure minimale avant tout usage :

```powershell
# 1. Télécharger SANS installer
pip download BinaryOptionsToolsV2 --no-deps -d .\audit

# 2. Inspecter l'archive
cd .\audit
Expand-Archive .\*.whl -DestinationPath .\src
Get-ChildItem .\src -Recurse -Include *.py | Select-String -Pattern `
  "eval\(|exec\(|subprocess|os\.system|base64|socket|requests\.|urllib|http|requests|pickle|__import__"
```

Grille de lecture (checklist) :

- [ ] **Aucun `eval` / `exec` / `compile`** sur des données réseau.
- [ ] **Aucun appel réseau non documenté** (télémétrie, exfiltration, webhook).
- [ ] **Aucun `pickle.loads`** sur des messages entrants (exécution de code arbitraire).
- [ ] **Aucun accès** à `~/AppData`, navigateur, presse-papiers, variables d'environnement globales.
- [ ] **Aucune écriture** hors du dossier du projet.
- [ ] **Licence et mainteneur** identifiables ; historique de commits cohérent.
- [ ] **Version épinglée** avec hash (`pip install paquet==x.y.z`, idéalement `--require-hashes`).
- [ ] **Isolation** : testée d'abord dans un conteneur / VM jetable, sans SSID réel.

Alternative plus sûre : ne pas installer ces paquets et écrire vous-même le strict
nécessaire (WebSocket + parsing) après capture du trafic **de votre propre session**,
en restant dans le cadre des conditions d'utilisation de la plateforme.

> ⚠️ Point juridique : automatiser un compte via un SSID peut contrevenir aux CGU.
> Le risque n'est pas seulement technique (bannissement, fonds bloqués).

---

## 3. Environnement virtuel isolé

```powershell
cd "F:\MES PROJETS DEV\pocket-lab"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
pip freeze > requirements.lock      # versions exactes reproductibles
```

Bonnes pratiques :
- **Un venv par projet** — jamais `pip install` global.
- Vérifier que le prompt affiche `(.venv)` avant toute commande.
- `.venv/` est ignoré par git.
- `requirements.lock` permet de détecter toute dérive de dépendance.
- Audit périodique : `pip list --outdated`, `pip install pip-audit && pip-audit`.
- Si PowerShell bloque l'activation :
  ```powershell
  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
  ```

---

## 4. Hygiène du dépôt

```powershell
git init
git add .
git status          # vérifier qu'aucun .env n'apparaît
git commit -m "Pocket Lab — simulation, risque, console"
```

- Le `.gitignore` exclut `.env`, `.venv/` et `journal/`.
- Avant chaque commit : `git diff --cached | Select-String -Pattern "ssid|token|secret"`.
- Si un secret a déjà été commité : retirer le fichier de l'index **et** réécrire
  l'historique (`git filter-repo`), puis révoquer le secret. Le supprimer dans un
  nouveau commit ne suffit pas.
- Activer la protection de branche et ne jamais pousser `.env` sur un dépôt public.

---

## 5. Sécurité à l'exécution

| Risque | Réponse dans le code |
|---|---|
| Ordre réel accidentel | `MODE_REEL` obligatoire + confirmation + `ExecutionReelleBloquee` |
| Martingale involontaire | Un seul ordre à la fois ; `montant` toujours ≤ budget recalculé |
| Perte illimitée | `PERTE_MAX_JOURNALIERE` vérifiée avant chaque ordre |
| Rafale d'ordres | `TRADES_MAX_PAR_JOUR` + cooldown de signal |
| Fuite du SSID dans l'UI | `resume_public()` filtré, aucun secret dans les snapshots |
| Jeton écrit sur disque | Panneau *Connexion* : SSID en RAM uniquement, aperçu masqué, effacement en un clic |
| Mot de passe saisi dans l'outil | Connexion supervisée : la saisie se fait dans votre navigateur ; la console lit seulement le cookie de session |
| Solveur de captcha tiers | Aucun : le captcha reste résolu par l'humain |
| Réglages aberrants (faute) | Bornes strictes côté serveur (HTTP 400) + cohérence mise ≤ plafond |
| Solde du compte inventé | « indisponible » affiché tant que l'adaptateur n'est pas livré ; rien n'est simulé |
| Flux réel figé en silence | Veille : alerte si aucun prix reçu 12 s après la bascule en Observation |
| Injection dans la console | Le JS n'insère que des données générées localement ; pas de HTML utilisateur |
| Dépendance compromise | Aucun CDN, aucune lib front, dépendances Python minimales et épinglées |
| Arrêt brutal | `Moteur.arreter()` annule l'ordre ouvert et journalise l'incident |

Rappels d'exploitation :
- Ne laissez **jamais** tourner le bot sans surveillance sur un compte approvisionné.
- Coupez tout (fermeture de session) si le comportement diverge du mode démo.
- Sauvegardez `journal/` avant toute modification de stratégie : c'est votre seule preuve.

---

## 6. Limites de ce document

Aucune architecture ne rend le trading binaire « sûr » : le risque de perte est
structurel (payout < 100 %). La sécurité décrite ici protège vos **identifiants**,
votre **capital engagé par erreur** et votre **traçabilité**. Elle ne protège pas
d'un mauvais pari, d'un changement de CGU, ni d'une manipulation côté plateforme.

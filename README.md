# Bot-HidenCloud — README d'automatisation (Playwright)

Ce document décrit en détail l'outil d'automatisation du renouvellement de service HidenCloud fondé sur Playwright.
Il couvre l'installation, la configuration, toutes les options de ligne de commande, le déploiement (GitHub Actions, Docker), la sécurité et les bonnes pratiques.

1. Vue d'ensemble
-----------------
Le script principal `renew_hidencloud_playwright.py` automatise la séquence : ouvrir la page de gestion du service, cliquer sur "Renouveler", "Créer une facture" puis "Payer" (si applicable). Le script utilise Playwright pour exécuter JavaScript et contourner les protections WAF/Cloudflare qui bloquent les simples requêtes HTTP.

2. Pré-requis
-------------
- Système : Linux recommandé (outil testé sur Ubuntu / Debian et sur Raspberry Pi avec adaptations)
- Python 3.11+ (ou 3.10 selon l'image)
- Playwright Python et ses binaires (Chromium)
- Dépendances Python : contenues dans `requirements.txt` (par ex. playwright, requests, beautifulsoup4)

3. Installation locale
----------------------
1) Créer et activer un virtualenv (recommandé) :

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2) Installer les dépendances Python :

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

3) Installer les navigateurs Playwright (une seule fois) :

```bash
python -m playwright install --with-deps
```

4. Structure de configuration (`config.json`)
------------------------------------------
Le fichier `config.json` contient l'essentiel des paramètres :
- `service_manage_url` : URL de la page de gestion du service.
- `base_url` : URL de base du dashboard.
- `cookies` : objet mapping cookieName → cookieValue (optionnel mais utile pour bypasser challenge si cookie de clearance valide).
- `discord_webhook` : URL du webhook Discord pour notifications.
- `paths` : chemins locaux (workdir, last_run_file, log_file).
- `min_days_between_runs` : intervalle minimal entre deux exécutions utiles (ex : 6).
- `use_playwright` : bool (si présent). Le script peut aussi être forcé via variable d'environnement.
- `selectors` : objet contenant `renew`, `create_invoice`, `pay` (sélecteurs CSS/Playwright recommandés).
- `http` : paramètres timeout/retries/user_agent.

Exemples et précautions :
- Les cookies dans `config.json` confèrent l'accès à la session — les stocker dans un dépôt public est dangereux. Mieux vaut stocker `CONFIG_JSON` comme secret sur la plateforme CI.

5. Toutes les options de ligne de commande
----------------------------------------
Le script expose les options suivantes (toutes documentées ci-dessous) :

- `--dry` : Mode non-destructif. Le script charge la page, exécute le JavaScript et exporte les cookies en JSON imprimé sur stdout, mais n'effectue pas les clics de création/paiement.

- `--run-renew` : Forcer explicitement l'exécution de la séquence de renouvellement. Si aucun flag n'est fourni au script, la séquence est activée par défaut (comportement historique) ; toutefois, certains flags (ex. `--use-config-cookies`) désactivent l'activation automatique pour éviter des runs non intentionnels.

- `--headful` : Lancer le navigateur en mode visible (utile pour débogage et pour résoudre manuellement des challenges Cloudflare/Turnstile). Sans ce flag, le navigateur tourne en headless.

- `--timeout-ms <ms>` : Timeout de navigation en millisecondes (par défaut 60000).

- `--use-config-cookies` : Injecter dans le contexte navigateur les cookies définis dans `config.json` avant la navigation. Permet souvent de passer la vérification anti-bot si le cookie `cf_clearance` ou équivalent est valide.

- `--pause` : Mettre une pause après le chargement (utile en combinaison avec `--headful` pour inspection manuelle).

- `--bypass-restriction` : Forcer la poursuite de la séquence même si le site affiche "Renewal Restricted" ou un message équivalent. DANGEREUX : à n'utiliser que si la conséquence est pleinement comprise.

- `--confirm-pay` : Autoriser explicitement le clic final "Payer". Par défaut, si le montant de la facture est détecté > 0, le script refuse d'exécuter le paiement et notifie. `--confirm-pay` doit être fourni pour permettre le paiement automatique.

Notes sur l'activation par défaut :
- Si le script est lancé sans aucun flag, il active `--run-renew` par défaut. Toutefois, la présence de certains flags (comme `--use-config-cookies`) empêche l'activation automatique afin d'éviter une exécution non voulue lorsque l'utilisateur fournit des options.

6. Variables d'environnement et secrets
--------------------------------------
Le comportement du script peut être influencé par des variables d'environnement :

- `USE_PLAYWRIGHT` : si défini à 1/true, force l'utilisation de Playwright (utile pour CI).
- `PLAYWRIGHT_HEADFUL` : si défini, force headful via env.
- `DISCORD_WEBHOOK` : alternative à la valeur dans `config.json` (préférer GitHub secrets / Railway env variables plutôt que commit).
- `CONFIG_JSON` : contenu complet de `config.json` (pratique pour CI — le workflow écrit le secret en `config.json` avant d'exécuter le script).

7. Sécurité et bonnes pratiques
--------------------------------
- Ne pas committer `cookies` sensibles dans un dépôt public.
- Préférer l'usage de secrets (GitHub Secrets, Railway variables) pour `DISCORD_WEBHOOK` et `CONFIG_JSON`.
- Tester en local avec `--dry` et `--headful` avant d'automatiser.
- Mettre `--confirm-pay` explicitement si le paiement automatique est réellement souhaité.

8. Déploiement : GitHub Actions (recommended)
-------------------------------------------
Un workflow exemple est inclus dans `.github/workflows/renew.yml`. Il :

- installe Python et dépendances
- installe les navigateurs Playwright
- écrit `config.json` depuis le secret `CONFIG_JSON` si fourni
- exécute `python3 renew_hidencloud_playwright.py --run-renew --use-config-cookies`

Procédure rapide :
1) Pousser le repo sur GitHub.
2) Ajouter les secrets (Repository → Settings → Secrets) : `DISCORD_WEBHOOK` et, optionnellement, `CONFIG_JSON`.
3) Sur Actions, lancer manuellement le workflow la première fois pour vérifier les logs.

Conseil sur la planification : le workflow est défini pour s'exécuter quotidiennement mais le script a déjà `min_days_between_runs=6` — il ne renouvellera que si ce délai est atteint.

9. Déploiement : Docker / Railway
--------------------------------
Pour déployer sur Railway (ou tout autre PaaS supportant Docker), utiliser un `Dockerfile` adapté qui installe les dépendances système requises par Chromium et exécute `python3 renew_hidencloud_playwright.py`. Un exemple minimal est fourni dans le dépôt (ou peut être demandé).

Points importants :
- Playwright / Chromium consomment de la mémoire. Vérifier le plan Railway choisi (1–2 GB recommandé).
- Utiliser des variables d'environnement (Railway Environment) pour fournir `CONFIG_JSON` ou `DISCORD_WEBHOOK`.

10. Logs, debugging et captures
------------------------------
- Le script écrit des logs dans le fichier indiqué par `paths.log_file` (par défaut `renew.log`).
- Pour les problèmes liés aux challenges Cloudflare, lancer en `--headful` et résoudre manuellement le challenge, puis utiliser `--dry` pour exporter les cookies.
- Il est possible d'ajouter des captures d'écran (page.screenshot) en mode headful; si souhaité, ce comportement peut être ajouté.

11. Comportements de sécurité déjà intégrés
------------------------------------------
- Détection « Renewal Restricted » : le script scanne la page (body, éléments d'alerte, modals, titres `<h3>`) pour détecter un blocage et envoie une notification Discord avant d'abandonner.
- Vérification du montant : après la création de facture, le script extrait le montant détecté et n'exécute le paiement que si le montant est 0, ou si `--confirm-pay` est fourni.
- Flag `--bypass-restriction` : permet de forcer l'exécution malgré la présence de la restriction (dangerous, nécessite prudence).

12. Commandes d'exemple
-----------------------
Chargement non destructif et export cookies :
```bash
python3 renew_hidencloud_playwright.py --dry --use-config-cookies
```

Exécution headful (intervention manuelle possible) :
```bash
python3 renew_hidencloud_playwright.py --run-renew --headful --use-config-cookies
```

Exécution automatique (headless) en CI / Actions :
```bash
python3 renew_hidencloud_playwright.py --run-renew --use-config-cookies
```

Forcer la poursuite malgré restriction (dangerous) :
```bash
python3 renew_hidencloud_playwright.py --run-renew --bypass-restriction --use-config-cookies --confirm-pay
```

13. Suivi et maintenance
------------------------
- Surveiller `renew.log` pour les erreurs et notifications envoyées sur Discord.
- Mettre à jour les sélecteurs dans `config.json` si l'interface du site évolue.
- Si Playwright échoue souvent sur CI, vérifier la compatibilité des dépendances système et la mémoire disponible.

14. Support / modifications
---------------------------
Pour ajouter : captures d'écran, confirmation interactive via Discord avant paiement, ou un mécanisme d'alerte plus riche, fournir la demande et le dépôt sera mis à jour.

Fin du document.



# Bot_HiddenCloud

Helper Playwright pour l'automatisation du renouvellement HidenCloud

Prérequis (Linux, zsh) :

1) Créer et activer un virtualenv (recommandé) :

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2) Installer les dépendances :

```bash
python -m pip install -r requirements.txt
```

3) Installer les binaires de navigateurs Playwright (une seule fois) :

```bash
python -m playwright install
```

Utilisation :

- Lancer le script sans option lance par défaut la séquence de renouvellement (Renouveler → Créer une facture → Payer) en mode headless :

```bash
python3 renew_hidencloud_playwright.py
```

- Options utiles :

  --dry            : mode non-destructif, n'effectue que le chargement et exporte les cookies (print JSON)
  --headful        : lancer le navigateur en mode visible (utile pour déboguer ou résoudre un challenge)
  --use-config-cookies : injecter les cookies présents dans `config.json` avant la navigation

Exemples :

```bash
# Dry-run (n'effectue pas de clics)
python3 renew_hidencloud_playwright.py --dry

# Lancer en visible et exécuter la séquence (utile si tu dois résoudre manuellement un challenge)
python3 renew_hidencloud_playwright.py --headful --use-config-cookies
```

Notifications : le script envoie des notifications dans Discord (webhook) pour : démarrage, éléments manquants, challenge détecté, et si le renouvellement est restreint (message "Renewal Restricted"). Assure-toi que `discord_webhook` est configuré dans `config.json`.

Sélecteurs : le script utilise les sélecteurs définis dans `config.json` (champ `selectors`) pour cliquer précisément sur :
- `renew` : bouton « Renouveler »
- `create_invoice` : bouton « Créer une facture »
- `pay` : bouton « Payer »

Si tu préfères que le script n'effectue pas le clic final "Payer", dis-le et j'ajouterai une confirmation manuelle via Discord ou un flag local.

Remarque : si le site présente un challenge anti-bot (Cloudflare/Turnstile), lance le script en mode `--headful` et complète le challenge manuellement, puis relance en headless pour que le cookie de clearance soit récupéré.

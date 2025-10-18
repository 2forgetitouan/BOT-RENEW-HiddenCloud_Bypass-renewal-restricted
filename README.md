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

  --dry         : mode non-destructif, n'effectue que le chargement et exporte les cookies (print JSON)
  
  --headful        : lancer le navigateur en mode visible (utile pour déboguer ou résoudre un challenge)
  
  --use-config-cookies      : injecter les cookies présents dans `config.json` avant la navigation

  --run-renew        : tente d'exécuter la logique de renouvellement via Playwright

Exemples :

```bash
# Dry-run (n'effectue pas de clics)
python3 renew_hidencloud_playwright.py --dry

# Lancer en visible et exécuter la séquence
python3 renew_hidencloud_playwright.py --headful --use-config-cookies
```

Notifications : le script envoie des notifications dans Discord (webhook) pour : démarrage, éléments manquants, challenge détecté, et si le renouvellement est restreint (message "Renewal Restricted").
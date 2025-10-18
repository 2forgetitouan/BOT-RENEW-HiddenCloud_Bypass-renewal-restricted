#!/usr/bin/env python3
"""
Script Playwright pour HidenCloud
- ouvre la page de gestion en utilisant Chromium headless
- attend le chargement complet (exécution JS)
- extrait cookies valides et imprime un JSON (pour copie dans config.json)
- option --run-renew: tente d'exécuter la logique de renouvellement via Playwright (non destructif par défaut)

Usage:
  python3 renew_hidencloud_playwright.py --dry
  python3 renew_hidencloud_playwright.py --run-renew

Note: Playwright Python et ses binaires doivent être installés (voir README.md).
"""
import sys
import json
import argparse
from pathlib import Path
import time
import traceback
import requests

from playwright.sync_api import sync_playwright

CONFIG_PATH = Path(__file__).parent / 'config.json'


def load_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def now_str():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg, conf=None):
    ts = now_str()
    line = f"[{ts}] {msg}"
    print(line)
    try:
        if conf:
            p = Path(conf.get('paths', {}).get('log_file') or '')
            if p and p.parent.exists():
                with open(p, 'a') as f:
                    f.write(line + "\n")
    except Exception:
        pass


def send_discord(content, conf=None):
    try:
        webhook = None
        if conf:
            webhook = conf.get('discord_webhook')
        if not webhook:
            return
        if isinstance(content, str):
            payload = {"content": content}
        elif isinstance(content, dict):
            # support simple embeds
            if content.get('title') or content.get('description') or content.get('fields'):
                embed = {"title": content.get('title', ''), "description": content.get('description', ''), "fields": content.get('fields', [])}
                payload = {"embeds": [embed]}
            else:
                payload = content
        else:
            payload = {"content": str(content)}
        r = requests.post(webhook, json=payload, timeout=10)
        if r.status_code >= 400:
            log(f"Webhook error: {r.status_code} {r.text}", conf=conf)
    except Exception as e:
        tb = traceback.format_exc()
        log(f"Erreur webhook: {e} {tb}", conf=conf)


def save_cookies_output(cookies):
    # Affiche JSON sur stdout pour copie
    out = {c['name']: c['value'] for c in cookies}
    print(json.dumps({'cookies': out}, indent=2))


def main(dry=True, run_renew=False, headful=False, timeout_ms=60000, use_config_cookies=True, pause=False):
    conf = load_config()
    manage_url = conf.get('service_manage_url')
    base = conf.get('base_url')
    if not manage_url:
        print('Erreur: service_manage_url manquant dans config.json')
        return 2

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=(not headful), args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        # set a user agent if present in config
        ua = None
        try:
            ua = conf.get('http', {}).get('user_agent')
        except Exception:
            ua = None
        context_kwargs = {}
        if ua:
            context_kwargs['user_agent'] = ua
        context = browser.new_context(**context_kwargs)
        page = context.new_page()

        # inject cookies from config before navigation if requested
        if use_config_cookies:
            try:
                conf_cookies = conf.get('cookies', {}) or {}
                cookie_list = []
                for name, val in conf_cookies.items():
                    if not val:
                        continue
                    # domain must be provided for playwright cookie; derive from base if available
                    domain = None
                    if base:
                        # remove scheme
                        domain = base.replace('https://', '').replace('http://', '').split('/')[0]
                    cookie_list.append({'name': name, 'value': val, 'domain': domain, 'path': '/'})
                if cookie_list:
                    context.add_cookies(cookie_list)
                    print('Cookies injectés dans le contexte (names):', [c['name'] for c in cookie_list])
            except Exception as e:
                print('Erreur injection cookies:', e)

        print('Ouverture:', manage_url)
        try:
            # tente un goto rapide sur domcontentloaded puis attend networkidle si possible
            page.goto(manage_url, timeout=timeout_ms, wait_until='domcontentloaded')
            try:
                page.wait_for_load_state('networkidle', timeout=min(timeout_ms, 60000))
            except Exception:
                # fallback: wait a short time
                page.wait_for_timeout(2000)
        except Exception as e:
            print('Navigation erreur / timeout:', e)

        # si la page affiche un challenge, on capture le titre
        title = page.title()
        url = page.url
        content_snippet = page.content()[:2000]
        print('Titre:', title)
        print('URL finale:', url)

        # extraire cookies
        cookies = context.cookies()
        save_cookies_output(cookies)

        # si on execute l'option run_renew (ou si l'appel se fait sans --dry), tenter d'appuyer sur Renouveler -> Créer une facture -> Payer
        if run_renew:
            try:
                # vérifier si la page affiche un challenge qui bloquerait (ex: Security Verification)
                page_html = page.content().lower()
                if 'security verification' in page_html or 'cf_chl_prog' in page_html or 'turnstile' in page_html:
                    log('La page semble afficher un challenge de sécurité (403/JS). Abandon de run-renew.', conf=conf)
                    send_discord({'title': 'Renouvellement: challenge', 'description': 'La page affiche un challenge de sécurité (403/JS). Intervention requise.'}, conf=conf)
                else:
                    sel_conf = conf.get('selectors', {}) or {}
                    seq = [
                        ('renew', sel_conf.get('renew') or 'text=/Renouvel|renouvel|Renew/i'),
                        ('create_invoice', sel_conf.get('create_invoice') or 'text=/Créer une facture|Create Invoice/i'),
                        ('pay', sel_conf.get('pay') or 'text=/Payer|Pay/i'),
                    ]
                    for name, selector in seq:
                        try:
                            print(f"Tentative click '{name}' avec sélecteur: {selector}")
                            # attendre la présence de l'élément (timeout réduit)
                            el = None
                            try:
                                el = page.wait_for_selector(selector, timeout=5000)
                            except Exception:
                                # fallback: query_selector sans attendre
                                try:
                                    el = page.query_selector(selector)
                                except Exception:
                                    el = None
                            if not el:
                                log(f"Élément '{name}' introuvable avec le sélecteur/heuristique.", conf=conf)
                                send_discord({'title': f"Renouvellement: élément introuvable", 'description': f"'{name}' introuvable (sélecteur: {selector})"}, conf=conf)
                                continue
                            # click et attendre réseau / petit délai
                            try:
                                el.click()
                            except Exception:
                                # parfois click() échoue si l'élément est un <button type=submit>; utiliser evaluate
                                try:
                                    page.evaluate("el => el.click()", el)
                                except Exception as e:
                                    print(f"Impossible de cliquer sur '{name}': {e}")
                                    continue
                            # attendre navigation ou réseau bref
                            try:
                                page.wait_for_load_state('networkidle', timeout=8000)
                            except Exception:
                                page.wait_for_timeout(1200)
                            log(f"Après click '{name}', URL: {page.url}", conf=conf)
                            # dump court pour debug
                            snippet = page.content()[:1200]
                            log(f"Snippet après '{name}': {snippet[:800]}", conf=conf)
                            # Détecter message 'Renewal Restricted' après le click renew
                            if name == 'renew':
                                low = snippet.lower()
                                if 'renewal restricted' in low or 'you can only renew your free service' in low:
                                    msg = {
                                        'title': 'Renouvellement restreint',
                                        'description': 'Renewal Restricted — votre service ne peut être renouvelé que lorsque moins de 1 jour reste avant l expiration.',
                                        'fields': [
                                            {'name': 'URL', 'value': page.url},
                                            {'name': 'Info', 'value': 'Le renouvellement a été bloqué par la règle site (Renewal Restricted).'}
                                        ]
                                    }
                                    log('Renewal Restricted détecté après click renew.', conf=conf)
                                    send_discord(msg, conf=conf)
                                    # arrêter la séquence
                                    break
                        except Exception as e:
                            print(f"Erreur pendant le click '{name}': {e}")
            except Exception as e:
                print('Erreur pendant run_renew:', e)

        browser.close()
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry', action='store_true', help='Dry run: n effectue que le chargement et exporte cookies')
    ap.add_argument('--run-renew', action='store_true', help='Tenter d appuyer sur le bouton Renouveler (peut être destructif)')
    ap.add_argument('--headful', action='store_true', help='Lancer le navigateur en mode non-headless (utile pour debug/intervention)')
    ap.add_argument('--timeout-ms', type=int, default=60000, help='Timeout de navigation en ms')
    ap.add_argument('--use-config-cookies', action='store_true', help='Injecter les cookies présents dans config.json dans le contexte Playwright')
    ap.add_argument('--pause', action='store_true', help='Pause après chargement pour debug (headful conseillé)')
    args = ap.parse_args()
    # si aucun flag n'est fourni, on lance la séquence (non-dry) par défaut
    if not any([args.dry, args.run_renew, args.headful, args.use_config_cookies, args.pause]):
        args.run_renew = True
    rc = main(dry=args.dry, run_renew=args.run_renew, headful=args.headful, timeout_ms=args.timeout_ms, use_config_cookies=args.use_config_cookies, pause=args.pause)
    sys.exit(rc)

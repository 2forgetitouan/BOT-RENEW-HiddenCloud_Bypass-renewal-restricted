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


def main(dry=True, run_renew=False, headful=False, timeout_ms=60000, use_config_cookies=True, pause=False, bypass_restriction=False, confirm_pay=False):
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
                            # fallback supplémentaires si élément pas trouvé
                            if not el:
                                # essayer une recherche par texte (différentes variantes)
                                try:
                                    txt_sel = "text=Renouveler"
                                    el = page.query_selector(txt_sel)
                                except Exception:
                                    el = None
                            if not el:
                                try:
                                    el = page.query_selector("text=/renouvel/i")
                                except Exception:
                                    el = None
                            if not el:
                                # essayer button:has-text
                                try:
                                    el = page.query_selector("button:has-text(\"Renouveler\")")
                                except Exception:
                                    el = None
                            if not el:
                                # essayer attributs data-modal-target/toggle partiel
                                try:
                                    el = page.query_selector("[data-modal-target*=\"renewService\"], [data-modal-toggle*=\"renewService\"]")
                                except Exception:
                                    el = None
                            if not el:
                                # dernier recours : inspecter tout le DOM pour occurrences du mot et logguer
                                try:
                                    body_text = page.inner_text('body')
                                except Exception:
                                    body_text = (page.content() or '')[:2000]
                                # log excerpt around 'renouvel'
                                if 'renouvel' in body_text.lower() or 'renew' in body_text.lower():
                                    snippet = ''
                                    low = body_text.lower()
                                    idx = low.find('renouvel')
                                    if idx == -1:
                                        idx = low.find('renew')
                                    if idx >= 0:
                                        start = max(0, idx - 120)
                                        snippet = body_text[start:start+400]
                                    log(f"Élément '{name}' introuvable mais le mot 'renouvel' apparaît dans la page. Extrait: {snippet}", conf=conf)
                                else:
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
                            # Si on vient de créer une facture, extraire le montant et décider du paiement
                            if name == 'create_invoice':
                                # tenter d'extraire un montant (0.00, 0,00, €)
                                amt_text = ''
                                try:
                                    # chercher éléments usuels sur la page facture
                                    # ex: .invoice-amount, .amount, strong, .price
                                    for sel_amt in ['.invoice-amount', '.amount', '.price', 'strong', '.total']:
                                        try:
                                            node_amt = page.query_selector(sel_amt)
                                            if node_amt:
                                                amt_text = (node_amt.text_content() or '').strip()
                                                if amt_text:
                                                    break
                                        except Exception:
                                            pass
                                except Exception:
                                    pass
                                # fallback: rechercher motif monétaire dans le snippet
                                if not amt_text:
                                    import re
                                    m = re.search(r"(0[,.]0{1,2}|\d+[,.]\d{2})\s*€", snippet)
                                    if m:
                                        amt_text = m.group(0)
                                    else:
                                        m2 = re.search(r"(0[,.]0{1,2}|\d+[,.]\d{2})", snippet)
                                        if m2:
                                            amt_text = m2.group(0)
                                log(f"Montant détecté facture (raw): '{amt_text}'", conf=conf)
                                # Normaliser et décider
                                def parse_amount(s):
                                    if not s:
                                        return None
                                    s = s.replace('\u00A0', '').replace(' ', '')
                                    s = s.replace('€', '')
                                    s = s.replace(',', '.')
                                    try:
                                        return float(re.search(r"[0-9]+\.?[0-9]*", s).group(0))
                                    except Exception:
                                        return None
                                try:
                                    import re as _re
                                    re = _re
                                except Exception:
                                    pass
                                amt_val = parse_amount(amt_text)
                                if amt_val is None:
                                    log('Impossible de déterminer le montant de la facture, arrêt par sécurité.', conf=conf)
                                    send_discord({'title': 'Renouvellement: montant inconnu', 'description': 'Impossible de déterminer le montant de la facture. Arrêt par sécurité.'}, conf=conf)
                                    break
                                if amt_val > 0.0 and not confirm_pay:
                                    log(f"Facture non gratuite détectée ({amt_val}€) — paiement refusé sans --confirm-pay.", conf=conf)
                                    send_discord({'title': 'Renouvellement: paiement refusé', 'description': f'Facture détectée: {amt_text} — pas de paiement automatique sans --confirm-pay.'}, conf=conf)
                                    break
                            # Détecter message 'Renewal Restricted' avant et après le click renew
                            if name == 'renew':
                                def detect_renewal_restricted(pg):
                                    # Texte complet visible
                                    try:
                                        full = pg.inner_text('body') or ''
                                    except Exception:
                                        full = (pg.content() or '')
                                    low = full.lower()
                                    checks = [
                                        'renewal restricted',
                                        'you can only renew your free service',
                                        'renouvellement restreint',
                                        'ne peut être renouvelé',
                                        'vous ne pouvez renouveler',
                                        'you can only renew',
                                    ]
                                    for c in checks:
                                        if c in low:
                                            return True, f"matched_text:{c}"
                                    # role=alert and common selectors
                                    for sel in ['[role="alert"]', '.alert', '.alert-danger', '.toast', '.modal', '.modal-body', '.notification', '.notice']:
                                        try:
                                            node = pg.query_selector(sel)
                                            if node:
                                                t = (node.text_content() or '').lower()
                                                for c in checks:
                                                    if c in t:
                                                        return True, f"sel:{sel}:{c}"
                                        except Exception:
                                            pass
                                    # recherche explicite de titres/h3 contenant le message (ex: modal header)
                                    try:
                                        h3 = pg.query_selector("h3:has-text(\"Renewal Restricted\")")
                                        if h3:
                                            return True, 'h3:Renewal Restricted'
                                    except Exception:
                                        pass
                                    try:
                                        # générique: tout h3 avec mot 'renewal' ou 'renouvel'
                                        h3_any = pg.query_selector_all('h3')
                                        for n in h3_any:
                                            try:
                                                txt = (n.text_content() or '').lower()
                                                for c in checks:
                                                    if c in txt:
                                                        return True, f"h3:{c}"
                                            except Exception:
                                                pass
                                    except Exception:
                                        pass
                                    return False, None

                                # check before click
                                pre_found, pre_why = detect_renewal_restricted(page)
                                if pre_found:
                                    log('Renewal Restricted détecté AVANT click renew (' + (pre_why or '') + ').', conf=conf)
                                    send_discord({'title': 'Renouvellement restreint', 'description': 'Renewal Restricted détecté avant tentative.','fields':[{'name':'URL','value':page.url},{'name':'Raison','value':pre_why or ''}]}, conf=conf)
                                    if bypass_restriction:
                                        log('Bypass demandé: on poursuit malgré la restriction (dangerous).', conf=conf)
                                        send_discord({'title': 'Renouvellement: bypass', 'description': 'Proceeding despite Renewal Restricted due to --bypass-restriction flag. Attention.'}, conf=conf)
                                    else:
                                        break
                                # after click, re-evaluate (page updated)
                                post_found, post_why = detect_renewal_restricted(page)
                                if post_found:
                                    log('Renewal Restricted détecté APRÈS click renew (' + (post_why or '') + ').', conf=conf)
                                    send_discord({'title': 'Renouvellement restreint', 'description': 'Renewal Restricted détecté après tentative.','fields':[{'name':'URL','value':page.url},{'name':'Raison','value':post_why or ''}]}, conf=conf)
                                    if bypass_restriction:
                                        log('Bypass demandé: on poursuit malgré la restriction (dangerous).', conf=conf)
                                        send_discord({'title': 'Renouvellement: bypass', 'description': 'Proceeding despite Renewal Restricted due to --bypass-restriction flag. Attention.'}, conf=conf)
                                    else:
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
    ap.add_argument('--bypass-restriction', action='store_true', help='Tenter malgré Renewal Restricted (dangerous)')
    ap.add_argument('--confirm-pay', action='store_true', help='Autoriser le clic final Payer (nécessaire si montant > 0)')
    args = ap.parse_args()
    # si aucun flag n'est fourni, on lance la séquence (non-dry) par défaut
    if not any([args.dry, args.run_renew, args.headful, args.use_config_cookies, args.pause]):
        args.run_renew = True
    rc = main(dry=args.dry, run_renew=args.run_renew, headful=args.headful, timeout_ms=args.timeout_ms, use_config_cookies=args.use_config_cookies, pause=args.pause, bypass_restriction=args.bypass_restriction, confirm_pay=args.confirm_pay)
    sys.exit(rc)

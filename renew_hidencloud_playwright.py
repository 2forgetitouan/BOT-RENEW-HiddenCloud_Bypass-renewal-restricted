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
import os
import tempfile
import mimetypes

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


def _map_status_color(status):
    colors = {
        'success': 0x0ECF5F,
        'failure': 0xE82515,
        'warning': 0xF1A60F,
        'info': 0x3498DB,
        None: 0x95A5A6
    }
    return colors.get(status, colors[None])


def send_discord(content, conf=None):
    """Envoie un message compact au webhook Discord.

    Si content contient 'screenshots': list[str] alors les fichiers sont envoyés en attachments.
    """
    try:
        webhook = None
        if conf:
            webhook = conf.get('discord_webhook')
        if not webhook:
            return

        def _t(s, l):
            try:
                s = str(s)
            except Exception:
                s = ''
            if len(s) <= l:
                return s
            return s[: l - 3] + '...'

        # Minimal embed (shorter, moderne)
        if isinstance(content, str):
            embed = {'title': _t('Notification', 80), 'description': _t(content, 400), 'color': _map_status_color('info')}
            payload = {'embeds': [embed]}
        elif isinstance(content, dict):
            title = _t(content.get('title') or content.get('heading') or 'Notification', 80)
            desc = _t(content.get('description') or '', 400)
            status = content.get('status') or content.get('level') or content.get('type')
            color = _map_status_color(status if isinstance(status, str) else None)
            embed = {'title': title, 'description': desc, 'color': color}
            # small useful fields only
            fields = []
            if content.get('url'):
                fields.append({'name': 'URL', 'value': _t(content.get('url'), 200), 'inline': False})
            if content.get('amount') is not None:
                fields.append({'name': 'Montant', 'value': _t(str(content.get('amount')), 50), 'inline': True})
            if content.get('reason'):
                fields.append({'name': 'Raison', 'value': _t(content.get('reason'), 120), 'inline': False})
            if fields:
                embed['fields'] = fields
            payload = {'embeds': [embed]}
        else:
            payload = {'content': _t(content, 400)}

        # support screenshots attachments
        screenshots = None
        if isinstance(content, dict) and content.get('screenshots'):
            screenshots = content.get('screenshots')

        # deduplicate screenshots sent recently (avoid doubles)
        global _LAST_SCREENSHOT_SEND
        try:
            _LAST_SCREENSHOT_SEND
        except NameError:
            _LAST_SCREENSHOT_SEND = {}

        if screenshots:
            # filter out screenshots sent in the last 30s
            now_ts = time.time()
            filtered = []
            for p in screenshots:
                last = _LAST_SCREENSHOT_SEND.get(p)
                if last and now_ts - last < 30:
                    continue
                filtered.append(p)
            screenshots = filtered
            files = {}
            multipart = {'payload_json': (None, json.dumps(payload))}
            for i, path in enumerate(screenshots):
                try:
                    if not os.path.exists(path):
                        continue
                    key = f'file{i}'
                    files[key] = (os.path.basename(path), open(path, 'rb'), mimetypes.guess_type(path)[0] or 'application/octet-stream')
                except Exception:
                    continue
            # requests expects files as dict of name: (filename, fileobj, content_type)
            files_payload = {k: v for k, v in files.items()}
            # build files param merging payload_json
            # Note: requests uses a tuple (name, filetuple)
            files_param = {'payload_json': (None, json.dumps(payload))}
            files_param.update({k: (v[0], v[1], v[2]) for k, v in files.items()})
            r = requests.post(webhook, files=files_param, timeout=20)
            # close opened file objects
            try:
                for v in files.values():
                    try:
                        v[1].close()
                    except Exception:
                        pass
            except Exception:
                pass
            # mark sent
            if r.status_code < 400:
                for p in screenshots:
                    try:
                        _LAST_SCREENSHOT_SEND[p] = time.time()
                    except Exception:
                        pass
        else:
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


def _ensure_screens_dir():
    d = Path(__file__).parent / 'screenshots'
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        d = Path(tempfile.gettempdir())
    return d


def capture_screenshot(page, label='screenshot'):
    try:
        d = _ensure_screens_dir()
        fname = f"{int(time.time())}_{label}.png"
        path = d / fname
        # capture full page to show entire content
        page.screenshot(path=str(path), full_page=True)
        return str(path)
    except Exception as e:
        log(f"Erreur screenshot: {e}")
        return None


def debug_wait(step_name, debug=False, headful=False):
    if not debug:
        return
    hint = ' (headful: vous pouvez interagir avec la page)' if headful else ''
    try:
        input(f"DEBUG: étape '{step_name}'. Appuyez sur Entrée pour continuer{hint}...")
    except Exception:
        # si non interactif, fallback à un court délai
        time.sleep(1)


def _extract_amount_from_totals(page):
    """Tente d'extraire le montant en ciblant les blocs 'Sous-total / Total' décrits par l'utilisateur."""
    try:
        # Chercher des lignes structurées: .space-y-3 .flex.justify-between -> label / value
        try:
            nodes = page.query_selector_all('.space-y-3 .flex.justify-between')
            for n in nodes:
                try:
                    parts = n.query_selector_all('div')
                    if len(parts) >= 2:
                        label = (parts[0].text_content() or '').strip().lower()
                        value = (parts[1].text_content() or '').strip()
                        if 'total' in label or 'sous-total' in label or 'sous total' in label or 'total' in label:
                            return value
                except Exception:
                    continue
        except Exception:
            pass

        # fallback: rechercher un élément contenant 'Total' puis récupérer son sibling
        try:
            el = page.query_selector("text=/Total|Sous-total|Sous total|Sous-total/i")
            if el:
                val = page.evaluate("el => { let s = el.nextElementSibling || el.parentElement.querySelector('div:last-child'); return s ? s.textContent : null}", el)
                if val:
                    return val.strip()
        except Exception:
            pass

        # last resort: chercher le mot 'Total' dans le HTML et extraire le montant proche
        try:
            html = page.content() or ''
            import re
            m = re.search(r"(Total|Sous-total|Sous total)[\s\S]{0,60}?(€?\s?\d[0-9\s\.,]*\d)", html, re.I)
            if m:
                return m.group(2).strip()
        except Exception:
            pass
    except Exception:
        pass
    return None


def main(run_renew=False, headful=False, timeout_ms=60000, use_config_cookies=True, bypass_restriction=False, confirm_pay=False, screen=False, debug=False):
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

        # inject cookies from config before navigation (always enabled by default)
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
            # si bypass_restriction, réduire l'attente pour aller plus vite
            try:
                if bypass_restriction:
                    page.wait_for_load_state('load', timeout=min(5000, timeout_ms))
                else:
                    page.wait_for_load_state('networkidle', timeout=min(timeout_ms, 60000))
            except Exception:
                # fallback: wait un court temps
                page.wait_for_timeout(1000 if bypass_restriction else 2000)
            # capture après chargement
            if screen:
                pth = capture_screenshot(page, 'loaded')
                if pth:
                    send_discord({'title': 'Page chargée', 'description': 'Page ouverte', 'status': 'info', 'url': page.url, 'screenshots': [pth]}, conf=conf)
            debug_wait('after_goto', debug=debug, headful=headful)
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

        # si on execute l'option run_renew, tenter d'appuyer sur Renouveler -> Créer une facture -> Payer
        if run_renew:
            try:
                # état/resultat du processus (sera envoyé à Discord à la fin)
                renew_status = {'status': 'unknown', 'reason': None, 'url': page.url, 'amount': None}
                amt_val = None

                # vérifier si la page affiche un challenge qui bloquerait (ex: Security Verification)
                page_html = page.content().lower()
                if 'security verification' in page_html or 'cf_chl_prog' in page_html or 'turnstile' in page_html:
                    log('La page semble afficher un challenge de sécurité (403/JS). Abandon de run-renew.', conf=conf)
                    send_discord({'title': 'Renouvellement: challenge', 'description': 'La page affiche un challenge de sécurité (403/JS). Intervention requise.', 'status': 'failure', 'url': page.url}, conf=conf)
                    renew_status.update({'status': 'failed', 'reason': 'security_challenge'})
                else:
                    sel_conf = conf.get('selectors', {}) or {}
                    seq = [
                        ('renew', sel_conf.get('renew') or 'text=/Renouvel|renouvel|Renew/i'),
                        ('create_invoice', sel_conf.get('create_invoice') or 'text=/Créer une facture|Create Invoice/i'),
                        ('pay', sel_conf.get('pay') or 'text=/Payer|Pay/i'),
                    ]
                    selector_timeout_default = 5000
                    selector_timeout_bypass = 2000
                    for name, selector in seq:
                        try:
                            print(f"Tentative click '{name}' avec sélecteur: {selector}")
                            # attendre la présence de l'élément (timeout réduit)
                            el = None
                            to = selector_timeout_bypass if bypass_restriction else selector_timeout_default
                            try:
                                el = page.wait_for_selector(selector, timeout=to)
                            except Exception:
                                # fallback: query_selector sans attendre
                                try:
                                    el = page.query_selector(selector)
                                except Exception:
                                    el = None
                            # debug pause before interacting
                            debug_wait(f'before_click:{name}', debug=debug, headful=headful)
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
                                # dernier recours : inspecter un extrait du DOM pour occurrences du mot et logguer
                                try:
                                    # limiter la taille lue pour aller plus vite en bypass
                                    if bypass_restriction:
                                        body_text = (page.content() or '')[:4000]
                                    else:
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
                                    send_discord({'title': f"Renouvellement: élément introuvable", 'description': f"'{name}' introuvable (sélecteur: {selector})", 'status': 'warning', 'url': page.url}, conf=conf)
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
                            # attendre navigation ou réseau bref (plus court si bypass)
                            try:
                                if bypass_restriction:
                                    page.wait_for_load_state('load', timeout=5000)
                                else:
                                    page.wait_for_load_state('networkidle', timeout=8000)
                            except Exception:
                                page.wait_for_timeout(800 if bypass_restriction else 1200)
                            log(f"Après click '{name}', URL: {page.url}", conf=conf)
                            # dump court pour debug
                            snippet = (page.content() or '')[:1200]
                            log(f"Snippet après '{name}': {snippet[:800]}", conf=conf)
                            # capture écran après click si demandé
                            if screen:
                                pth = capture_screenshot(page, name)
                                if pth:
                                    send_discord({'title': f"Étape {name}", 'description': f"Étape {name} effectuée", 'status': 'info', 'url': page.url, 'screenshots': [pth]}, conf=conf)
                            debug_wait(f'after_click:{name}', debug=debug, headful=headful)
                            # si on vient de cliquer sur 'pay', considérer le workflow comme réussi
                            if name == 'pay':
                                renew_status.update({'status': 'success', 'reason': 'paid', 'url': page.url})
                                try:
                                    extras = {'title': 'Paiement déclenché', 'description': 'Bouton Payer cliqué', 'status': 'success', 'url': page.url}
                                    if screen:
                                        p = capture_screenshot(page, 'paid')
                                        if p:
                                            extras['screenshots'] = [p]
                                    send_discord(extras, conf=conf)
                                except Exception:
                                    pass
                                break
                            # Si on vient de créer une facture, extraire le montant et décider du paiement
                            if name == 'create_invoice':
                                # tenter d'extraire un montant (0.00, 0,00, €)
                                amt_text = ''
                                # tentative ciblée sur la structure 'Sous-total / Total' (plus fiable)
                                try:
                                    found = _extract_amount_from_totals(page)
                                    if found:
                                        amt_text = found
                                except Exception:
                                    pass
                                try:
                                    # chercher éléments usuels sur la page facture
                                    # ex: .invoice-amount, .amount, .price, .total, strong
                                    import re
                                    # prefer explicit amount containers before generic tags like <strong>
                                    for sel_amt in ['.invoice-amount', '.amount', '.price', '.total', 'strong']:
                                        try:
                                            node_amt = page.query_selector(sel_amt)
                                            if node_amt:
                                                candidate = (node_amt.text_content() or '').strip()
                                                # n'accepter que si on trouve au moins un chiffre ou un symbole monétaire
                                                if not candidate:
                                                    continue
                                                if re.search(r"\d", candidate) or '€' in candidate or '$' in candidate or '£' in candidate:
                                                    amt_text = candidate
                                                    break
                                                # sinon ignorer (ex: titre de la page comme 'HidenCloud™')
                                        except Exception:
                                            pass
                                except Exception:
                                    pass
                                # fallback: recherche plus robuste dans tout le HTML
                                if not amt_text:
                                    try:
                                        import re
                                        page_full = page.content() or ''
                                        low = page_full.lower()
                                        # priorité: mentions explicites de gratuité
                                        if re.search(r"\b(gratuit|gratuitement|free|no charge|without charge)\b", low, re.I):
                                            amt_text = '0.00'
                                        else:
                                            # collecter candidats monétaires : formats type 123.45 ou 1 234,56 ou avec symbole € $ £
                                            candidates = []
                                            # pattern qui capture nombre + optional currency symbol
                                            pat = re.compile(r"(?P<num>\d{1,3}(?:[\d\s\.\,]*\d)?[\.,]\d{2})\s*(?P<cur>€|eur|\$|usd|£|gbp)?", re.I)
                                            for m in pat.finditer(page_full):
                                                idx = m.start()
                                                txt = m.group(0).strip()
                                                candidates.append((idx, txt))

                                            # pattern with explicit symbol before amount (e.g. € 12.34)
                                            pat2 = re.compile(r"(?P<cur>€|\$|£)\s*(?P<num>\d+[\.,]\d{2})", re.I)
                                            for m in pat2.finditer(page_full):
                                                idx = m.start()
                                                txt = m.group(0).strip()
                                                candidates.append((idx, txt))

                                            # si on a des candidats, choisir celui proche d'un label utile
                                            chosen = None
                                            if candidates:
                                                # labels utiles
                                                labels = ['total', 'montant', 'price', 'amount', 'due', 'subtotal', 'balance', 'prix']
                                                best_score = None
                                                for idx, txt in candidates:
                                                    score = 999999
                                                    # cherche label proximité +/- 120 chars
                                                    window_start = max(0, idx - 120)
                                                    window_end = idx + 120
                                                    context = page_full[window_start:window_end].lower()
                                                    for lab in labels:
                                                        pos = context.find(lab)
                                                        if pos != -1:
                                                            # distance to center
                                                            dist = abs((window_start + pos) - idx)
                                                            if dist < score:
                                                                score = dist
                                                    # si aucun label trouvé, use default large score
                                                    if best_score is None or score < best_score:
                                                        best_score = score
                                                        chosen = txt
                                                amt_text = chosen
                                            else:
                                                # dernier recours: chercher motifs simples dans snippet
                                                m = re.search(r"(0[,.]0{1,2}|\d+[,.]\d{2})\s*€", snippet)
                                                if m:
                                                    amt_text = m.group(0)
                                                else:
                                                    m2 = re.search(r"(0[,.]0{1,2}|\d+[,.]\d{2})", snippet)
                                                    if m2:
                                                        amt_text = m2.group(0)
                                    except Exception:
                                        # si tout échoue, ne pas crash
                                        amt_text = ''
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
                                renew_status['amount'] = amt_val
                                if amt_val is None:
                                    log('Impossible de déterminer le montant de la facture, arrêt par sécurité.', conf=conf)
                                    extras = {'title': 'Montant inconnu', 'description': 'Impossible de déterminer le montant — arrêt par sécurité.', 'status': 'failure', 'url': page.url}
                                    if screen:
                                        p = capture_screenshot(page, 'amount_unknown')
                                        if p:
                                            extras['screenshots'] = [p]
                                    send_discord(extras, conf=conf)
                                    renew_status.update({'status': 'failed', 'reason': 'amount_unknown'})
                                    break
                                if amt_val > 0.0 and not confirm_pay:
                                    log(f"Facture non gratuite détectée ({amt_val}€) — paiement refusé sans --confirm-pay.", conf=conf)
                                    extras = {'title': 'Paiement requis', 'description': f'Facture détectée: {amt_text} — pas de paiement automatique sans --confirm-pay.', 'status': 'warning', 'amount': amt_text, 'url': page.url}
                                    if screen:
                                        p = capture_screenshot(page, 'payment_required')
                                        if p:
                                            extras['screenshots'] = [p]
                                    send_discord(extras, conf=conf)
                                    renew_status.update({'status': 'failed', 'reason': 'payment_required', 'amount': amt_val})
                                    break
                                # si montant = 0 => on considère la création de facture comme succès (pas de paiement nécessaire)
                                if amt_val == 0.0:
                                    renew_status.update({'status': 'success', 'reason': 'free_invoice', 'amount': 0.0})
                                    # capture si demandé
                                    if screen:
                                        p = capture_screenshot(page, 'free_invoice')
                                        if p:
                                            send_discord({'title': 'Facture gratuite', 'description': 'Facture gratuite détectée', 'status': 'success', 'url': page.url, 'screenshots': [p]}, conf=conf)
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
                                    send_discord({'title': 'Renouvellement restreint', 'description': 'Renewal Restricted détecté avant tentative.', 'status': 'failure', 'url': page.url, 'reason': pre_why}, conf=conf)
                                    renew_status.update({'status': 'failed', 'reason': f'renewal_restricted_before:{pre_why}'})
                                    if bypass_restriction:
                                        log('Bypass demandé: on poursuit malgré la restriction (dangerous).', conf=conf)
                                        send_discord({'title': 'Renouvellement: bypass', 'description': 'Proceeding despite Renewal Restricted due to --bypass-restriction flag. Attention.', 'status': 'warning', 'url': page.url}, conf=conf)
                                    else:
                                        break
                                # after click, re-evaluate (page updated)
                                post_found, post_why = detect_renewal_restricted(page)
                                if post_found:
                                    log('Renewal Restricted détecté APRÈS click renew (' + (post_why or '') + ').', conf=conf)
                                    send_discord({'title': 'Renouvellement restreint', 'description': 'Renewal Restricted détecté après tentative.', 'status': 'failure', 'url': page.url, 'reason': post_why}, conf=conf)
                                    renew_status.update({'status': 'failed', 'reason': f'renewal_restricted_after:{post_why}'})
                                    if bypass_restriction:
                                        log('Bypass demandé: on poursuit malgré la restriction (dangerous).', conf=conf)
                                        send_discord({'title': 'Renouvellement: bypass', 'description': 'Proceeding despite Renewal Restricted due to --bypass-restriction flag. Attention.', 'status': 'warning', 'url': page.url}, conf=conf)
                                    else:
                                        break
                        except Exception as e:
                            print(f"Erreur pendant le click '{name}': {e}")
                            # marquer l'erreur et continuer la boucle
                            renew_status.update({'status': 'failed', 'reason': f"click_error:{name}:{e}"})
                            send_discord({'title': 'Renouvellement: erreur clic', 'description': f"Erreur pendant le click '{name}': {e}", 'status': 'failure', 'url': page.url}, conf=conf)
                            # on laisse la boucle tenter la suite si possible
            except Exception as e:
                print('Erreur pendant run_renew:', e)
            # après la tentative: envoyer un résumé final selon le statut
            try:
                if renew_status.get('status') == 'success':
                    send_discord({
                        'title': '✅ Renouvellement réussi',
                        'description': 'Le renouvellement a été effectué avec succès.',
                        'status': 'success',
                        'url': page.url,
                        'amount': renew_status.get('amount'),
                        'fields': [
                            {'name': 'URL', 'value': page.url, 'inline': False},
                            {'name': 'Montant', 'value': str(renew_status.get('amount') or '—'), 'inline': True},
                        ]
                    }, conf=conf)
                else:
                    # défaut: échec ou inconnu
                    reason = renew_status.get('reason') or 'unknown'
                    send_discord({
                        'title': '❌ Erreur lors du renouvellement',
                        'description': f"Le renouvellement a échoué ou n'a pas été effectué.",
                        'status': 'failure',
                        'url': page.url,
                        'reason': reason,
                        'json': {'renew_status': renew_status},
                        'fields': [
                            {'name': 'URL', 'value': page.url, 'inline': False},
                            {'name': 'Raison', 'value': str(reason), 'inline': False},
                        ]
                    }, conf=conf)
            except Exception:
                pass

        browser.close()
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-renew', action='store_true', help='Tenter d appuyer sur le bouton Renouveler (peut être destructif)')
    ap.add_argument('--headful', action='store_true', help='Lancer le navigateur en mode non-headless (utile pour debug/intervention)')
    ap.add_argument('--timeout-ms', type=int, default=60000, help='Timeout de navigation en ms')
    ap.add_argument('--debug', action='store_true', help='Mode debug: demande de validation avant chaque étape (conseillé avec --headful)')
    ap.add_argument('--screen', action='store_true', help='Prendre des captures d écran à chaque étape et les envoyer au webhook Discord')
    ap.add_argument('--bypass-restriction', action='store_true', help='Tenter malgré Renewal Restricted (dangerous)')
    ap.add_argument('--confirm-pay', action='store_true', help='Autoriser le clic final Payer (nécessaire si montant > 0)')
    args = ap.parse_args()
    # Par défaut on injecte les cookies depuis config.json; il faut explicitement fournir --run-renew pour effectuer la séquence de paiement
    rc = main(run_renew=args.run_renew, headful=args.headful, timeout_ms=args.timeout_ms, use_config_cookies=True, bypass_restriction=args.bypass_restriction, confirm_pay=args.confirm_pay, screen=args.screen, debug=args.debug)
    sys.exit(rc)

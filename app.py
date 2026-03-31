#!/usr/bin/env python3
"""
Barbier Immobilier - PDF Dossier de Vente v5.2
Flask app deployed on Railway.
Routes: GET /, POST /generate-quartier, POST /dossier
"""

import html as _html_mod
import io
import json
import math
import os
import re
import logging
import base64 as _b64

import requests
from flask import Flask, request, jsonify, send_file
from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle

from assets import (LOGO_B64, PICTO_SURFACE_B64, PICTO_TYPE_B64,
                     PICTO_LIEU_B64, PICTO_VILLE_B64)

# ---------------------------------------------------------------------------
app = Flask(__name__)
app.logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Charte graphique (couleurs extraites du logo)
# ---------------------------------------------------------------------------
TEAL      = colors.HexColor("#16708B")
TEAL_DARK = colors.HexColor("#0D5570")
TEAL_LIGHT= colors.HexColor("#E8F5F8")
ORANGE    = colors.HexColor("#F0795B")
WHITE     = colors.white
GRAY_DARK = colors.HexColor("#333333")
GRAY_MID  = colors.HexColor("#6B7280")
GRAY_LIGHT= colors.HexColor("#F3F4F6")
GRAY_BDR  = colors.HexColor("#D1D5DB")

PAGE_W, PAGE_H = A4
ML = 14 * mm          # margin left
MR = 14 * mm
CW = PAGE_W - ML - MR # content width
HEADER_H = 11 * mm
FOOTER_H = 9 * mm

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def _clean(txt):
    if not txt:
        return ""
    t = _html_mod.unescape(str(txt))
    t = re.sub(r"\{\{[^}]+\}\}", "", t)
    t = t.replace("\xa0", " ").replace("\u202f", " ").replace("\u2009", " ")
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"(,\s*){3,}[^.!?\n]{0,300}$", "", t, flags=re.M)
    lines = t.split("\n")
    lines = [" ".join(l.split()) for l in lines]
    return "\n".join(lines).strip()


def _pfmt(val):
    if not val:
        return "\u2014"
    try:
        n = int(float(str(val).replace(" ", "").replace("\xa0", "")))
        return "{:,}".format(n).replace(",", " ") + " \u20ac"
    except (ValueError, TypeError):
        return str(val)


def _safe(v, fb="\u2014"):
    if v is None or str(v).strip() == "" or v == 0:
        return fb
    return str(v)


def _ir(b64):
    return ImageReader(io.BytesIO(_b64.b64decode(b64)))


def _fetch_photo(url_or_data):
    if not url_or_data:
        return None
    try:
        s = str(url_or_data)
        if s.startswith("data:"):
            _, b = s.split(",", 1)
            return ImageReader(io.BytesIO(_b64.b64decode(b)))
        resp = requests.get(s, timeout=15, headers={"User-Agent": "BarbierImmo/1.0"})
        if resp.status_code == 200:
            return ImageReader(io.BytesIO(resp.content))
    except Exception as e:
        app.logger.error("Photo fetch: %s", e)
    return None


def _geocode(adresse, ville):
    try:
        import urllib.parse
        q = urllib.parse.quote(str(adresse) + ", " + str(ville) + ", France")
        r = requests.get(
            "https://api-adresse.data.gouv.fr/search/?q=" + q + "&limit=1",
            headers={"User-Agent": "BarbierImmo/1.0"}, timeout=10)
        feats = r.json().get("features", [])
        if feats:
            lon, lat = feats[0]["geometry"]["coordinates"]
            return float(lat), float(lon)
    except Exception as e:
        app.logger.error("Geocode: %s", e)
    return None, None


def _osm_map(adresse, ville, zoom=16, tiles=3):
    """Returns (PIL Image, lat, lon) or (None, None, None)."""
    try:
        lat, lon = _geocode(adresse, ville)
        if lat is None:
            return None, None, None
        n = 2 ** zoom
        cx = int((lon + 180) / 360 * n)
        lr = math.radians(lat)
        cy = int((1 - math.log(math.tan(lr) + 1 / math.cos(lr)) / math.pi) / 2 * n)
        half = tiles // 2
        headers = {"User-Agent": "BarbierImmo/1.0"}
        rows = []
        for row in range(tiles):
            ri = []
            for col in range(tiles):
                tx, ty = cx - half + col, cy - half + row
                url = "https://tile.openstreetmap.org/{}/{}/{}.png".format(zoom, tx, ty)
                tr = requests.get(url, headers=headers, timeout=8)
                if tr.status_code == 200:
                    ri.append(Image.open(io.BytesIO(tr.content)).convert("RGB"))
                else:
                    ri.append(Image.new("RGB", (256, 256), (220, 220, 220)))
            rows.append(ri)
        tw = 256
        result = Image.new("RGB", (tw * tiles, tw * tiles))
        for r in range(tiles):
            for c2 in range(tiles):
                result.paste(rows[r][c2], (c2 * tw, r * tw))
        return result, lat, lon
    except Exception as e:
        app.logger.error("OSM map: %s", e)
        return None, None, None


def _get_poi_osm(lat, lon, radius=500):
    """Fetch POI via Overpass. Returns list of (category, name, color_hex)."""
    categories = [
        ("amenity", "parking", "Parking", "#1B3A5C"),
        ("public_transport", "stop_position", "Transport", "#0D5570"),
        ("amenity", "restaurant|cafe|bar", "Restauration", "#E8472A"),
        ("amenity", "bank|post_office", "Banque", "#1B5C3A"),
        ("amenity", "school|university", "Formation", "#5C3A1B"),
        ("shop", "supermarket|convenience|mall", "Commerce", "#3A1B5C"),
        ("amenity", "hospital|clinic|pharmacy", "Sante", "#5C1B3A"),
    ]
    results = []
    try:
        import urllib.parse
        for key, values, label, color in categories:
            val_filter = "|".join('"' + v + '"' for v in values.split("|"))
            query = (
                '[out:json][timeout:6];(node["' + key + '"~' + val_filter
                + '](around:' + str(radius) + ',' + str(lat) + ',' + str(lon)
                + '););out 3;'
            )
            enc = urllib.parse.quote(query)
            resp = requests.get(
                "https://overpass-api.de/api/interpreter?data=" + enc,
                headers={"User-Agent": "BarbierImmo/1.0"}, timeout=7)
            if resp.status_code != 200:
                continue
            elements = resp.json().get("elements", [])
            noms = []
            for el in elements:
                nom = el.get("tags", {}).get("name", "")
                if nom and nom not in noms:
                    noms.append(nom)
            if noms:
                results.append((label, noms[0][:28], color))
            if len(results) >= 6:
                break
    except Exception as e:
        app.logger.error("POI fetch: %s", e)
    return results


def _gpt_quartier(adresse, ville, type_bien):
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return ""
    v = ville or "Vannes"
    a = adresse or v
    prompt = (
        "Tu es un expert en immobilier commercial dans le Golfe du Morbihan (Bretagne Sud).\n"
        "Redige un texte de presentation du secteur, destine a un acquereur.\n\n"
        "Secteur : " + a + ", " + v + " (Morbihan, 56)\n"
        "Type de bien : " + (type_bien or "Local commercial") + "\n\n"
        "5 a 6 phrases riches (160-220 mots), texte continu, sans titre ni liste.\n"
        "Aborde : attractivite economique, le secteur, accessibilite, environnement commercial, pourquoi strategique.\n"
        "Ton : editorial, valorisant. Pas de formule vague."
    )
    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 500, "temperature": 0.65},
            timeout=30)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        app.logger.error("GPT quartier: %s", e)
        return ""


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------
def _rrect(c, x, y, w, h, r=4, fill=None, stroke=None, sw=0.6):
    c.saveState()
    if fill:
        c.setFillColor(fill)
    if stroke:
        c.setStrokeColor(stroke)
        c.setLineWidth(sw)
    p = c.beginPath()
    p.moveTo(x + r, y); p.lineTo(x + w - r, y)
    p.arcTo(x + w - 2*r, y, x + w, y + 2*r, -90, 90)
    p.lineTo(x + w, y + h - r)
    p.arcTo(x + w - 2*r, y + h - 2*r, x + w, y + h, 0, 90)
    p.lineTo(x + r, y + h)
    p.arcTo(x, y + h - 2*r, x + 2*r, y + h, 90, 90)
    p.lineTo(x, y + r)
    p.arcTo(x, y, x + 2*r, y + 2*r, 180, 90)
    p.close()
    c.drawPath(p, fill=1 if fill else 0, stroke=1 if stroke else 0)
    c.restoreState()


def _header(c, sub=""):
    c.setFillColor(TEAL)
    c.rect(0, PAGE_H - HEADER_H, PAGE_W, HEADER_H, fill=1, stroke=0)
    c.saveState()
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(ML, PAGE_H - 7.5 * mm, "DOSSIER DE PRESENTATION  >  " + sub.upper()[:70])
    c.restoreState()
    try:
        w = 18 * mm
        h = w * (662 / 488)
        if h > HEADER_H * 0.90:
            h = HEADER_H * 0.90
            w = h * (488 / 662)
        lx = PAGE_W - w - 4 * mm
        ly = (PAGE_H - HEADER_H) + (HEADER_H - h) / 2
        c.drawImage(_ir(LOGO_B64), lx, ly, width=w, height=h, mask="auto")
    except Exception:
        pass


def _footer(c, n, total=3):
    c.setFillColor(TEAL)
    c.rect(0, 0, PAGE_W, FOOTER_H, fill=1, stroke=0)
    c.saveState()
    c.setFillColor(WHITE)
    c.setFont("Helvetica", 6.5)
    c.drawString(ML, 3.5 * mm,
                 "Barbier Immobilier \u2014 2 place Albert Einstein, 56000 Vannes \u2014 02.97.47.11.11 \u2014 barbierimmobilier.com")
    c.drawRightString(PAGE_W - MR, 3.5 * mm, str(n) + " / " + str(total))
    c.restoreState()


def _sec(c, text, x, y, w=None):
    sw = w if w is not None else CW
    c.setFillColor(colors.HexColor("#EBF0F8"))
    c.rect(x, y, sw, 8 * mm, fill=1, stroke=0)
    c.setFillColor(ORANGE)
    c.rect(x, y, 3.5 * mm, 8 * mm, fill=1, stroke=0)
    c.setFillColor(TEAL_DARK)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(x + 8 * mm, y + 2.5 * mm, text)


def _pill(c, x, y, picto_b64, label, value, w=57*mm, h=16*mm):
    c.setFillColor(GRAY_LIGHT)
    c.setStrokeColor(colors.HexColor("#D1D8E8"))
    c.setLineWidth(0.5)
    c.roundRect(x, y, w, h, 2 * mm, fill=1, stroke=1)
    r = 5.5 * mm
    icx = x + r + 2 * mm
    icy = y + h / 2
    c.setFillColor(colors.HexColor("#F0F4F8"))
    c.circle(icx, icy, r, fill=1, stroke=0)
    try:
        ico = _ir(picto_b64)
        s = r * 1.2
        c.drawImage(ico, icx - s/2, icy - s/2, width=s, height=s, mask="auto")
    except Exception:
        pass
    tx = x + r * 2 + 5 * mm
    mw = w - r * 2 - 8 * mm
    c.saveState()
    c.setFillColor(colors.HexColor("#777777"))
    c.setFont("Helvetica", 6.5)
    lbl = label.upper()
    while lbl and c.stringWidth(lbl, "Helvetica", 6.5) > mw:
        lbl = lbl[:-1]
    c.drawString(tx, y + h - 4.5 * mm, lbl)
    c.restoreState()
    c.saveState()
    c.setFillColor(TEAL_DARK)
    vs = str(value)
    for fsz in [9.5, 9, 8, 7]:
        c.setFont("Helvetica-Bold", fsz)
        if c.stringWidth(vs, "Helvetica-Bold", fsz) <= mw:
            break
    c.drawString(tx, y + 3.5 * mm, vs)
    c.restoreState()


def _draw_cover(c, img, x, y, w, h):
    """Draw image with cover-crop inside a rounded rect."""
    try:
        iw, ih = img.getSize()
        tr = w / h
        ir = iw / ih if ih > 0 else 1
        if ir > tr:
            dh = h; dw = h * ir
            dx = x - (dw - w) / 2; dy = y
        else:
            dw = w; dh = w / ir if ir > 0 else h
            dx = x; dy = y - (dh - h) / 2
        c.saveState()
        clip = c.beginPath()
        clip.roundRect(x, y, w, h, 3 * mm)
        c.clipPath(clip, stroke=0, fill=0)
        c.drawImage(img, dx, dy, dw, dh, mask="auto")
        c.restoreState()
    except Exception as e:
        app.logger.error("draw_cover: %s", e)


def _draw_poi_icon(c, cat, cx, cy, r):
    c.setFillColor(WHITE)
    cu = cat.upper()
    if "PARKING" in cu:
        c.setFont("Helvetica-Bold", r * 1.5); c.drawCentredString(cx, cy - r * 0.5, "P")
    elif "TRANSPORT" in cu:
        c.setFont("Helvetica-Bold", r * 1.3); c.drawCentredString(cx, cy - r * 0.45, "T")
    elif "RESTAURATION" in cu:
        c.setFont("Helvetica-Bold", r * 1.3); c.drawCentredString(cx, cy - r * 0.45, "R")
    elif "COMMERCE" in cu:
        c.setFont("Helvetica-Bold", r * 1.3); c.drawCentredString(cx, cy - r * 0.45, "C")
    elif "BANQUE" in cu:
        c.setFont("Helvetica-Bold", r * 1.3); c.drawCentredString(cx, cy - r * 0.45, "B")
    elif "SANTE" in cu:
        c.setFont("Helvetica-Bold", r * 1.5); c.drawCentredString(cx, cy - r * 0.5, "+")
    elif "FORMATION" in cu:
        c.setFont("Helvetica-Bold", r * 1.3); c.drawCentredString(cx, cy - r * 0.45, "F")
    else:
        c.setFont("Helvetica-Bold", r * 1.3); c.drawCentredString(cx, cy - r * 0.45, "\u00b7")


def _draw_poi_card(c, bx, by, bw, bh, label, valeur, color_hex):
    col = colors.HexColor(color_hex) if color_hex else TEAL
    # Background
    c.setFillColor(GRAY_LIGHT)
    c.setStrokeColor(colors.HexColor("#D1D8E8"))
    c.setLineWidth(0.5)
    c.roundRect(bx, by, bw, bh, 2 * mm, fill=1, stroke=1)
    # Icon circle
    r = 5.5 * mm
    icx = bx + r + 2 * mm
    icy = by + bh / 2
    c.saveState()
    c.setFillColor(col)
    c.circle(icx, icy, r, fill=1, stroke=0)
    _draw_poi_icon(c, label, icx, icy, r)
    c.restoreState()
    # Text
    tx = bx + r * 2 + 5 * mm
    c.saveState()
    c.setFillColor(colors.HexColor("#777777"))
    c.setFont("Helvetica", 6.5)
    c.drawString(tx, by + bh - 4.5 * mm, label.upper())
    c.setFillColor(TEAL_DARK)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(tx, by + 3.5 * mm, str(valeur)[:28])
    c.restoreState()


# ---------------------------------------------------------------------------
# PAGE 1 — Couverture
# ---------------------------------------------------------------------------
def _page1(c, d):
    c.setFillColor(TEAL)
    c.rect(0, PAGE_H * 0.50, PAGE_W, PAGE_H * 0.50, fill=1, stroke=0)

    # Badge exclusivite
    statut = str(d.get("statut_mandat") or "").lower()
    yoff = 0
    if "exclusi" in statut:
        c.saveState()
        c.setFont("Helvetica-Bold", 11)
        bw = c.stringWidth("EXCLUSIVITE", "Helvetica-Bold", 11) + 12 * mm
        c.setFillColor(ORANGE)
        c.roundRect(ML, PAGE_H - 28 * mm, bw, 8 * mm, 2 * mm, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.drawCentredString(ML + bw / 2, PAGE_H - 23.5 * mm, "EXCLUSIVITE")
        c.restoreState()
        yoff = 12 * mm

    # Title
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 30)
    c.drawString(ML, PAGE_H - 38 * mm - yoff, _safe(d.get("type_bien"), "Bien immobilier"))
    c.setFillColor(ORANGE)
    c.rect(ML, PAGE_H - 41.5 * mm - yoff, 40 * mm, 2 * mm, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont("Helvetica", 14)
    c.drawString(ML, PAGE_H - 50 * mm - yoff, _safe(d.get("adresse")))
    cp = _safe(d.get("code_postal"), "")
    vi = _safe(d.get("ville"), "")
    c.drawString(ML, PAGE_H - 58 * mm - yoff, (cp + " " + vi).strip())

    # Price
    prix = d.get("prix") or 0
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 34)
    c.drawString(ML, PAGE_H - 84 * mm, _pfmt(prix))
    # Honoraires
    hono = d.get("honoraires")
    if hono:
        c.setFillColor(colors.HexColor("#FFFFFFBB"))
        c.setFont("Helvetica", 9)
        ht = "Honoraires : " + _pfmt(hono)
        hc = d.get("honoraires_charge", "")
        if hc:
            ht = ht + " (" + str(hc) + ")"
        c.drawString(ML, PAGE_H - 91 * mm, ht)
    c.setFillColor(WHITE)
    c.setFont("Helvetica", 9)
    c.drawString(ML, PAGE_H - (97 if hono else 91) * mm, "PRIX DE VENTE FAI")

    # Pills: Surface, Type, Activite (3 pills like before)
    pills = [
        ("SURFACE", _safe(d.get("surface")) + " m\u00b2"),
        ("TYPE", _safe(d.get("type_bien"))),
    ]
    if d.get("activite"):
        pills.append(("ACTIVITE", _safe(d.get("activite"))))
    else:
        pills.append(("ETAT", _safe(d.get("etat_bien"), "---")))
    pills = pills[:4]

    np = len(pills)
    pg = 2 * mm
    pw = (CW - (np - 1) * pg) / np
    ph = 22 * mm
    py = PAGE_H * 0.50 - ph / 2 + 1 * mm
    for i, (lbl, val) in enumerate(pills):
        px = ML + i * (pw + pg)
        c.saveState()
        c.setFillColor(colors.HexColor("#00000022"))
        c.roundRect(px + 0.5 * mm, py - 0.5 * mm, pw, ph, 2 * mm, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.roundRect(px, py, pw, ph, 2 * mm, fill=1, stroke=0)
        c.restoreState()
        c.setFillColor(ORANGE)
        c.rect(px + 2 * mm, py + ph - 2 * mm, pw - 4 * mm, 2 * mm, fill=1, stroke=0)
        c.saveState()
        c.setFillColor(colors.HexColor("#888888"))
        c.setFont("Helvetica", 7)
        c.drawCentredString(px + pw / 2, py + ph - 7 * mm, lbl)
        c.setFillColor(TEAL_DARK)
        for fsz in [12, 10, 8, 7, 6]:
            c.setFont("Helvetica-Bold", fsz)
            if c.stringWidth(val, "Helvetica-Bold", fsz) < pw - 4 * mm:
                break
        c.drawCentredString(px + pw / 2, py + 5 * mm, val)
        c.restoreState()

    # Bottom half: white + main photo
    c.setFillColor(WHITE)
    c.rect(0, 0, PAGE_W, PAGE_H * 0.50, fill=1, stroke=0)
    pho_h = PAGE_H * 0.50 - 22 * mm
    pho_x = ML
    pho_y = 20 * mm
    pho_w = CW

    photos = d.get("photos") or []
    img = None
    for p in photos:
        if p:
            img = _fetch_photo(p)
            if img:
                break
    if not img:
        url = d.get("Photo principale URL", "")
        if url:
            img = _fetch_photo(url)
    if img:
        _draw_cover(c, img, pho_x, pho_y, pho_w, pho_h)
    else:
        c.setFillColor(GRAY_LIGHT)
        c.setStrokeColor(GRAY_BDR)
        c.setLineWidth(1)
        c.roundRect(pho_x, pho_y, pho_w, pho_h, 3 * mm, fill=1, stroke=1)
        c.setFillColor(GRAY_MID)
        c.setFont("Helvetica", 10)
        c.drawCentredString(pho_x + pho_w / 2, pho_y + pho_h / 2, "[ Photo principale du bien ]")

    # Logo top-right
    try:
        lw2 = 28 * mm
        lh2 = lw2 * (662 / 488)
        lx2 = PAGE_W - lw2 - 8 * mm
        ly2 = PAGE_H - lh2 - 5 * mm
        pad = 2.5 * mm
        c.setFillColor(WHITE)
        c.roundRect(lx2 - pad, ly2 - pad, lw2 + pad * 2, lh2 + pad * 2, 3 * mm, fill=1, stroke=0)
        c.drawImage(_ir(LOGO_B64), lx2, ly2, width=lw2, height=lh2, mask="auto")
    except Exception:
        pass

    c.saveState()
    c.setFillColor(GRAY_DARK)
    c.setFont("Helvetica", 7.5)
    neg = _safe(d.get("negociateur"), "Barbier Immobilier")
    ref = _safe(d.get("reference"))
    c.drawString(ML, 13 * mm, "Dossier prepare par  " + neg + "  \u00b7  Ref. " + ref)
    c.restoreState()
    _footer(c, 1)


# ---------------------------------------------------------------------------
# PAGE 2 — Quartier & Localisation (50/50 carte + POI)
# ---------------------------------------------------------------------------
def _page2(c, d):
    _header(c, "Quartier & environnement")

    # Section "Le quartier"
    _sec(c, "Le quartier", ML, PAGE_H - 32 * mm)

    # Chapeau
    ville = _safe(d.get("ville"), "Vannes")
    tb = d.get("type_bien") or ""
    if tb and tb != "\u2014":
        chapeau = "Un emplacement strategique pour votre " + tb.lower() + " au c\u0153ur de " + ville + "."
    else:
        chapeau = "Un emplacement strategique au c\u0153ur de " + ville + "."
    c.setFillColor(ORANGE)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(ML, PAGE_H - 38 * mm, chapeau)

    # Layout: carte+POI anchored at bottom, text fills above
    zone_h = 75 * mm
    col_gap = 5 * mm
    col_w = (CW - col_gap) / 2
    zone_bot = FOOTER_H
    zone_top = zone_bot + zone_h
    qbot = zone_top + 12 * mm

    # Quartier text
    texte = d.get("texte_quartier") or ""
    if not texte:
        texte = (
            "Situe a " + ville + ", ce bien beneficie d'une localisation strategique "
            "dans un secteur economiquement actif du Morbihan. L'accessibilite est optimale grace a la "
            "proximite de la rocade et des axes principaux. Le secteur compte de nombreux commerces, "
            "services et equipements a proximite immediate, offrant un environnement favorable a "
            "l'exploitation d'une activite commerciale ou professionnelle."
        )

    text_top = PAGE_H - 41 * mm
    max_text_h = text_top - qbot
    if max_text_h < 10 * mm:
        max_text_h = 10 * mm

    # First sentence bold, rest regular
    parts = re.split(r"(?<=[.!?])\s+", texte.strip(), maxsplit=1)
    if len(parts) == 2:
        p1 = parts[0].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        p2 = parts[1].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        texte_xml = "<b>" + p1 + "</b><br/><br/>" + p2
    else:
        texte_xml = texte.replace("&", "&amp;")

    sty = ParagraphStyle("qt", fontName="Helvetica", fontSize=9, textColor=GRAY_DARK, leading=14, alignment=4)
    p = Paragraph(texte_xml, sty)
    _, ph = p.wrap(CW, 9999)
    if ph > max_text_h:
        for fsz in [8, 7.5, 7]:
            sty2 = ParagraphStyle("qt" + str(fsz), fontName="Helvetica", fontSize=fsz,
                                  textColor=GRAY_DARK, leading=fsz * 1.6, alignment=4)
            p = Paragraph(texte_xml, sty2)
            _, ph = p.wrap(CW, 9999)
            if ph <= max_text_h:
                break
    ty = text_top - ph
    if ty < qbot:
        ty = qbot
    p.drawOn(c, ML, ty)

    # Column titles
    _sec(c, "Localisation", ML, zone_top + 2 * mm, w=col_w)
    _sec(c, "Environnement", ML + col_w + col_gap, zone_top + 2 * mm, w=col_w)

    # Left column: OSM map
    mx = ML
    mw = col_w
    mh = zone_h
    my = zone_bot
    lat = lon = None
    try:
        osm_img, lat, lon = _osm_map(_safe(d.get("adresse"), ""), _safe(d.get("ville"), "Vannes"))
        if osm_img:
            iw2, ih2 = osm_img.size
            tr = mw / mh
            if iw2 / ih2 > tr:
                nw = int(ih2 * tr)
                osm_img = osm_img.crop(((iw2 - nw) // 2, 0, (iw2 - nw) // 2 + nw, ih2))
            else:
                nh = int(iw2 / tr)
                osm_img = osm_img.crop((0, (ih2 - nh) // 2, iw2, (ih2 - nh) // 2 + nh))
            buf2 = io.BytesIO()
            osm_img.save(buf2, format="PNG")
            buf2.seek(0)
            c.saveState()
            clip = c.beginPath()
            clip.roundRect(mx, my, mw, mh, 3 * mm)
            c.clipPath(clip, stroke=0, fill=0)
            c.drawImage(ImageReader(buf2), mx, my, width=mw, height=mh)
            c.restoreState()
            # Marker
            px2 = mx + mw / 2
            py2 = my + mh / 2
            c.setFillColor(ORANGE)
            c.circle(px2, py2, 3.5 * mm, fill=1, stroke=0)
            c.setFillColor(WHITE)
            c.setFont("Helvetica-Bold", 8)
            c.drawCentredString(px2, py2 - 2.5 * mm, "+")
            # Address bubble
            adr = _safe(d.get("adresse")) + ", " + _safe(d.get("ville"))
            bwb = min(c.stringWidth(adr, "Helvetica-Bold", 6.5) + 12, mw - 8 * mm)
            c.setFillColor(WHITE)
            c.setStrokeColor(colors.HexColor("#AAAAAA"))
            c.setLineWidth(0.4)
            c.roundRect(px2 - bwb / 2, py2 + 5 * mm, bwb, 8 * mm, 1 * mm, fill=1, stroke=1)
            c.setFillColor(TEAL_DARK)
            c.setFont("Helvetica-Bold", 6.5)
            c.drawCentredString(px2, py2 + 8.5 * mm, adr[:50])
            # Border
            c.setStrokeColor(colors.HexColor("#CCCCCC"))
            c.setLineWidth(0.6)
            c.roundRect(mx, my, mw, mh, 3 * mm, fill=0, stroke=1)
            # Copyright
            c.setFillColor(colors.HexColor("#FFFFFF88"))
            c.rect(mx, my, mw, 5 * mm, fill=1, stroke=0)
            c.setFillColor(colors.HexColor("#666666"))
            c.setFont("Helvetica", 5)
            c.drawRightString(mx + mw - 2 * mm, my + 1.5 * mm, "\u00a9 OpenStreetMap contributors")
    except Exception as e:
        app.logger.error("Map draw: %s", e)
        c.setFillColor(colors.HexColor("#E8F0F4"))
        c.roundRect(mx, my, mw, mh, 3 * mm, fill=1, stroke=0)
        c.setFillColor(colors.HexColor("#AAAAAA"))
        c.setFont("Helvetica", 8)
        c.drawCentredString(mx + mw / 2, my + mh / 2, "Carte indisponible")

    # Right column: POI cards
    poi_x = ML + col_w + col_gap
    poi_blocks = []
    if lat and lon:
        poi_blocks = _get_poi_osm(lat, lon, radius=500)

    n_poi = min(len(poi_blocks), 5)
    if n_poi > 0:
        poi_gap = 3 * mm
        poi_ch = (zone_h - (n_poi - 1) * poi_gap) / n_poi
        for i, (lbl, val, col_hex) in enumerate(poi_blocks[:n_poi]):
            by = zone_top - (i + 1) * poi_ch - i * poi_gap
            _draw_poi_card(c, poi_x, by, col_w, poi_ch, lbl, val, col_hex)
    else:
        c.setFillColor(colors.HexColor("#E8F0F4"))
        c.roundRect(poi_x, my, col_w, mh, 3 * mm, fill=1, stroke=0)
        c.setFillColor(colors.HexColor("#AAAAAA"))
        c.setFont("Helvetica", 8)
        c.drawCentredString(poi_x + col_w / 2, my + mh / 2, "Donnees en cours...")

    _footer(c, 2)


# ---------------------------------------------------------------------------
# PAGE 3 — Annonce + Donnees financieres + Prix
# ---------------------------------------------------------------------------
def _page3(c, d):
    _header(c, _safe(d.get("type_bien")) + " \u2014 " + _safe(d.get("adresse")) + ", " + _safe(d.get("ville")))
    _sec(c, "Presentation du bien", ML, PAGE_H - 32 * mm)

    # Title line
    c.setFillColor(TEAL_DARK)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(ML, PAGE_H - 37 * mm, _safe(d.get("type_bien"), "Bien immobilier").upper())
    c.setFillColor(GRAY_DARK)
    c.setFont("Helvetica", 9)
    adr = _safe(d.get("adresse"), "")
    vl = _safe(d.get("ville"), "")
    c.drawString(ML, PAGE_H - 40.5 * mm, (adr + ", " + vl) if adr and vl else (adr or vl))

    desc = _clean(d.get("description", ""))
    text_y = PAGE_H - 43 * mm
    desc_bot = _render_desc(c, desc, text_y, 60 * mm)
    bot = desc_bot - 8 * mm

    # Caracteristiques pills
    _sec(c, "Caracteristiques", ML, bot - 2 * mm)
    pills_data = [
        (PICTO_SURFACE_B64, "Surface habitable", _safe(d.get("surface")) + " m\u00b2"),
        (PICTO_TYPE_B64, "Type de bien", _safe(d.get("type_bien"))),
        (PICTO_LIEU_B64, "Adresse", _safe(d.get("adresse"))),
        (PICTO_VILLE_B64, "Ville", _safe(d.get("ville"))),
    ]
    if d.get("activite"):
        pills_data.append((PICTO_TYPE_B64, "Activite", _safe(d.get("activite"))))

    pw2 = 57 * mm
    ph2 = 16 * mm
    pgx = 3 * mm
    pgy = 3 * mm
    cols = 3
    sy = bot - 14 * mm
    for i, (b64, lbl, val) in enumerate(pills_data):
        col = i % cols
        row = i // cols
        _pill(c, ML + col * (pw2 + pgx), sy - row * (ph2 + pgy), b64, lbl, val, pw2, ph2)
    pb = sy - ((len(pills_data) - 1) // cols) * (ph2 + pgy) - ph2 - 3 * mm

    # Financial data
    loc = d.get("locataire") or ""
    lht = d.get("loyer_annuel_ht") or 0
    linit = d.get("loyer_initial_ht") or 0
    evol = d.get("evolution_loyer") or ""
    duree = d.get("duree_bail") or ""
    taxe = d.get("taxe_fonciere") or 0
    is_bail = bool(loc or lht or linit or evol or duree)

    _fin_bottom = None
    if is_bail:
        n_col1 = sum([bool(loc), bool(lht)])
        n_col2 = sum([bool(linit), bool(evol), bool(duree), bool(taxe)])
        n_rows = max(n_col1, n_col2, 1)
        bloc_h = max(14 * mm, n_rows * 11 * mm + 6 * mm)
        _sec(c, "Donnees du bail", ML, pb)
        fy = pb - 7 * mm
        c.setFillColor(colors.HexColor("#EBF0F8"))
        c.roundRect(ML, fy - bloc_h, CW, bloc_h, 2 * mm, fill=1, stroke=0)
        c.setFillColor(ORANGE)
        c.rect(ML, fy - bloc_h, 3 * mm, bloc_h, fill=1, stroke=0)

        fcx1 = ML + 6 * mm
        fcx2 = ML + CW / 2 + 3 * mm
        fcy = fy - 4 * mm

        def _bail_line(x, y2, label, valeur):
            c.setFillColor(TEAL_DARK)
            c.setFont("Helvetica-Bold", 7)
            c.drawString(x, y2, label.upper())
            c.setFillColor(GRAY_DARK)
            c.setFont("Helvetica", 8.5)
            c.drawString(x, y2 - 5 * mm, str(valeur))

        if loc:
            _bail_line(fcx1, fcy, "Locataire", loc)
            fcy -= 11 * mm
        if lht:
            _bail_line(fcx1, fcy, "Loyer annuel HT", _pfmt(lht) + " HT/an")
            fcy -= 11 * mm

        fcy2 = fy - 4 * mm
        if linit:
            _bail_line(fcx2, fcy2, "Loyer initial", _pfmt(linit) + " HT")
            fcy2 -= 11 * mm
        if evol:
            _bail_line(fcx2, fcy2, "Evolution du loyer", evol)
            fcy2 -= 11 * mm
        if duree:
            _bail_line(fcx2, fcy2, "Duree du bail", duree)
            fcy2 -= 11 * mm
        if taxe:
            _bail_line(fcx2, fcy2, "Taxe fonciere", _pfmt(taxe) + "/an")
        _fin_bottom = fy - bloc_h - 4 * mm

    elif taxe:
        _sec(c, "Donnees financieres", ML, pb - 4 * mm)
        fy = pb - 10 * mm
        fw = CW / 2 - 1 * mm
        c.setFillColor(colors.HexColor("#EBF0F8"))
        c.roundRect(ML, fy - 12 * mm, fw, 12 * mm, 1.5 * mm, fill=1, stroke=0)
        c.setFillColor(TEAL_DARK)
        c.setFont("Helvetica", 6.5)
        c.drawString(ML + 3 * mm, fy - 5 * mm, "TAXE FONCIERE")
        c.setFont("Helvetica-Bold", 9)
        c.drawString(ML + 3 * mm, fy - 10 * mm, _pfmt(taxe))
        _fin_bottom = fy - 12 * mm - 4 * mm

    # Prix block anchored above footer
    prix_brut = d.get("prix") or 0
    if not prix_brut:
        pnv = d.get("prix_net_vendeur") or 0
        hnr = d.get("honoraires") or 0
        if pnv and hnr:
            try:
                prix_brut = int(float(str(pnv))) + int(float(str(hnr)))
            except Exception:
                pass
    if prix_brut:
        try:
            prix_fai = int(float(str(prix_brut)))
            pnv_v = d.get("prix_net_vendeur") or 0
            hono_raw = d.get("honoraires") or 0
            if pnv_v:
                pnv_v = int(float(str(pnv_v)))
                hono_v = int(float(str(hono_raw))) if hono_raw else (prix_fai - pnv_v)
            else:
                hono_v = int(prix_fai * 0.05)
                pnv_v = prix_fai - hono_v

            bloc_h2 = 22 * mm
            bw3 = (CW) / 3 - 2 * mm
            hcharge = d.get("honoraires_charge") or "Acquereur"
            if _fin_bottom is not None and _fin_bottom > 46 * mm:
                bloc_y = max(18 * mm, _fin_bottom - bloc_h2 - 11 * mm)
            else:
                bloc_y = 18 * mm
            _sec(c, "Prix", ML, bloc_y + bloc_h2 + 3 * mm)
            items = [
                ("PRIX DE VENTE FAI", _pfmt(prix_fai), TEAL_DARK),
                ("HONORAIRES (" + str(hcharge)[:10] + ")", _pfmt(hono_v), ORANGE),
                ("PRIX NET VENDEUR", _pfmt(pnv_v), colors.HexColor("#0D5570")),
            ]
            for ip, (lbl, val, col) in enumerate(items):
                bxp = ML + ip * (bw3 + 3 * mm)
                c.setFillColor(col)
                c.roundRect(bxp, bloc_y, bw3, bloc_h2, 2 * mm, fill=1, stroke=0)
                c.setFillColor(WHITE)
                c.setFont("Helvetica", 6.5)
                c.drawString(bxp + 3 * mm, bloc_y + bloc_h2 - 7 * mm, lbl)
                c.setFont("Helvetica-Bold", 10)
                c.drawString(bxp + 3 * mm, bloc_y + 6 * mm, val)
        except Exception as e:
            app.logger.error("Prix block: %s", e)

    _footer(c, 3)


def _render_desc(c, desc_txt, start_y, max_h):
    """Render description with editorial formatting. Returns y of bottom."""
    if not desc_txt:
        return start_y
    y = start_y
    gap = 2.5 * mm
    col_w = CW
    x = ML

    def _xs(t):
        return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _sb(t):
        return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)

    def _strip(t):
        return re.sub(r"<[^>]+>", "", t)

    is_html = bool(re.search(r"<(p|h[1-6]|ul|li|strong|em|br)\b", desc_txt, re.I))

    if is_html:
        tokens = re.split(
            r"(<h[1-6][^>]*>.*?</h[1-6]>|<p[^>]*>.*?</p>|<li[^>]*>.*?</li>)",
            desc_txt, flags=re.I | re.DOTALL)
        first = True
        bloc_count = 0
        for tok in tokens:
            tok = tok.strip()
            if not tok:
                continue
            bloc_count += 1
            if bloc_count > 5:
                break
            mh2 = re.match(r"<h[1-6][^>]*>(.*?)</h[1-6]>", tok, re.I | re.DOTALL)
            mp = re.match(r"<p[^>]*>(.*?)</p>", tok, re.I | re.DOTALL)
            ml = re.match(r"<li[^>]*>(.*?)</li>", tok, re.I | re.DOTALL)
            if mh2:
                txt = _xs(_strip(mh2.group(1)).strip())
                if first:
                    p = Paragraph("<b>" + txt + "</b>",
                                  ParagraphStyle("a", fontName="Helvetica-Bold", fontSize=10,
                                                 textColor=TEAL_DARK, leading=15))
                    first = False
                else:
                    p = Paragraph(txt, ParagraphStyle("s", fontName="Helvetica-Bold", fontSize=8.5,
                                                      textColor=TEAL_DARK, leading=13))
                _, ph = p.wrap(col_w, 9999)
                if y - ph < 18 * mm:
                    break
                y -= ph
                p.drawOn(c, x, y)
                y -= gap
            elif ml:
                txt = _sb(_xs(_strip(ml.group(1)).strip()))
                if txt:
                    p = Paragraph("\u2022 " + txt,
                                  ParagraphStyle("l", fontName="Helvetica", fontSize=8.5,
                                                 textColor=GRAY_DARK, leading=12,
                                                 leftIndent=4*mm, firstLineIndent=-4*mm))
                    _, ph = p.wrap(col_w, 9999)
                    if y - ph < 18 * mm:
                        break
                    y -= ph
                    p.drawOn(c, x, y)
                    y -= 0.8 * mm
                    first = False
            elif mp:
                txt = _strip(mp.group(1)).strip()
                txt = re.sub(r"(\d[\d\s]*(?:\u20ac|%|m\u00b2|ans?)[^.,;]*)", r"**\1**", txt)
                txt = _sb(_xs(txt))
                if txt:
                    if first:
                        p = Paragraph("<b>" + txt + "</b>",
                                      ParagraphStyle("a2", fontName="Helvetica-Bold", fontSize=10,
                                                     textColor=TEAL_DARK, leading=15))
                        first = False
                    else:
                        p = Paragraph(txt, ParagraphStyle("p", fontName="Helvetica", fontSize=9,
                                                          textColor=GRAY_DARK, leading=13, alignment=4))
                    _, ph = p.wrap(col_w, 9999)
                    if y - ph < 18 * mm:
                        break
                    y -= ph
                    p.drawOn(c, x, y)
                    y -= gap
    else:
        blocs = [b.strip() for b in desc_txt.split("\n\n") if b.strip()]
        for idx, bloc in enumerate(blocs):
            if y < 18 * mm or idx >= 4:
                break
            lines = [l.strip() for l in bloc.splitlines() if l.strip()]
            if not lines:
                continue
            fl = lines[0]
            is_sec = (fl == fl.upper() and len(fl) > 4 and not any(ch.isdigit() for ch in fl[:2]) and len(lines) == 1)
            is_bul = len(lines) > 1 and all(len(l) < 120 for l in lines)
            if idx == 0:
                if len(fl) <= 100 and len(lines) == 1:
                    p = Paragraph("<b>" + _xs(fl) + "</b>",
                                  ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=10,
                                                 textColor=TEAL_DARK, leading=15))
                    _, ph = p.wrap(col_w, 9999)
                    if y - ph < 18 * mm:
                        break
                    y -= ph
                    p.drawOn(c, x, y)
                    y -= gap + 1 * mm
                else:
                    txt = re.sub(r"(\d[\d\s]*(?:\u20ac|%|m\u00b2)[^.,;]*)", r"**\1**", " ".join(lines))
                    p = Paragraph(_sb(_xs(txt)),
                                  ParagraphStyle("b", fontName="Helvetica", fontSize=9,
                                                 textColor=GRAY_DARK, leading=13, alignment=4))
                    _, ph = p.wrap(col_w, 9999)
                    if y - ph < 18 * mm:
                        break
                    y -= ph
                    p.drawOn(c, x, y)
                    y -= gap
            elif is_sec:
                txt = _xs(fl)
                p = Paragraph(txt, ParagraphStyle("sc", fontName="Helvetica-Bold", fontSize=8.5,
                                                   textColor=TEAL_DARK, leading=13))
                _, ph = p.wrap(col_w - 6 * mm, 9999)
                bh = ph + 4 * mm
                if y - bh < 18 * mm:
                    break
                y -= 1 * mm
                c.setFillColor(colors.HexColor("#EBF0F8"))
                c.roundRect(x, y - ph - 1 * mm, col_w, bh, 1 * mm, fill=1, stroke=0)
                c.setFillColor(ORANGE)
                c.rect(x, y - ph - 1 * mm, 3 * mm, bh, fill=1, stroke=0)
                y -= ph
                p.drawOn(c, x + 6 * mm, y)
                y -= gap
            elif is_bul:
                for li in lines:
                    txt = re.sub(r"(\d[\d\s]*(?:\u20ac|%|m\u00b2|ans?)[^.,;]*)", r"**\1**", li)
                    p = Paragraph("\u2022 " + _sb(_xs(txt)),
                                  ParagraphStyle("bl", fontName="Helvetica", fontSize=8.5,
                                                 textColor=GRAY_DARK, leading=12,
                                                 leftIndent=4*mm, firstLineIndent=-4*mm))
                    _, ph = p.wrap(col_w, 9999)
                    if y - ph < 18 * mm:
                        break
                    y -= ph
                    p.drawOn(c, x, y)
                    y -= 0.8 * mm
            else:
                txt = re.sub(r"(\d[\d\s]*(?:\u20ac|%|m\u00b2)[^.,;]*)", r"**\1**", " ".join(lines))
                p = Paragraph(_sb(_xs(txt)),
                              ParagraphStyle("p2", fontName="Helvetica", fontSize=9,
                                             textColor=GRAY_DARK, leading=13, alignment=4))
                _, ph = p.wrap(col_w, 9999)
                if y - ph < 18 * mm:
                    break
                y -= ph
                p.drawOn(c, x, y)
                y -= gap
    return y


# ---------------------------------------------------------------------------
# PAGE 4 — Photos
# ---------------------------------------------------------------------------
def _page_photos(c, d):
    _header(c, _safe(d.get("type_bien")) + " \u2014 " + _safe(d.get("adresse")) + ", " + _safe(d.get("ville")))
    _sec(c, "Photos du bien", ML, PAGE_H - 32 * mm)

    photos = d.get("photos") or []
    # Skip first photo (already on cover), take next ones
    photo_list = photos[1:] if len(photos) > 1 else photos
    pw = CW
    zone_top = PAGE_H - 42 * mm
    zone_bot = FOOTER_H + 5 * mm
    gap = 5 * mm

    if not photo_list:
        c.setFillColor(GRAY_LIGHT)
        c.roundRect(ML, zone_bot, pw, zone_top - zone_bot, 3 * mm, fill=1, stroke=1)
        c.setFillColor(GRAY_MID)
        c.setFont("Helvetica", 10)
        c.drawCentredString(ML + pw / 2, (zone_top + zone_bot) / 2, "Aucune photo supplementaire")
        _footer(c, 4, total=4)
        return

    n = min(len(photo_list), 2)
    ph_each = (zone_top - zone_bot - (n - 1) * gap) / n

    for i in range(n):
        img = _fetch_photo(photo_list[i])
        py = zone_top - (i + 1) * ph_each - i * gap
        if img:
            _draw_cover(c, img, ML, py, pw, ph_each)
        else:
            c.setFillColor(GRAY_LIGHT)
            c.setStrokeColor(GRAY_BDR)
            c.roundRect(ML, py, pw, ph_each, 3 * mm, fill=1, stroke=1)
            c.setFillColor(GRAY_MID)
            c.setFont("Helvetica", 9)
            c.drawCentredString(ML + pw / 2, py + ph_each / 2, "Photo " + str(i + 2))

    _footer(c, 4, total=4)


# ---------------------------------------------------------------------------
# PDF Generation
# ---------------------------------------------------------------------------
def generate_dossier_pdf(d):
    buf = io.BytesIO()
    cv = rl_canvas.Canvas(buf, pagesize=A4)
    cv.setTitle("Dossier \u2014 " + str(d.get("reference", "")))

    photos = d.get("photos") or []
    has_extra_photos = len(photos) > 1
    total = 4 if has_extra_photos else 3

    _page1(cv, d)
    cv.showPage()
    _page2(cv, d)
    cv.showPage()
    _page3(cv, d)
    cv.showPage()
    if has_extra_photos:
        _page_photos(cv, d)
        cv.showPage()

    cv.save()
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def health():
    return jsonify({"service": "Barbier PDF Generator", "status": "ok", "version": "5.2"})


@app.route("/generate-quartier", methods=["POST"])
def generate_quartier():
    body = request.get_json(silent=True) or {}
    adresse = body.get("adresse", "")
    ville = body.get("ville", "")
    type_bien = body.get("type_bien", "")
    if not ville:
        return jsonify({"error": "Champ 'ville' requis"}), 400
    texte = _gpt_quartier(adresse, ville, type_bien)
    return jsonify({"texte_quartier": texte})


@app.route("/dossier", methods=["POST"])
def dossier():
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "Body JSON manquant"}), 400
    ref = body.get("reference", "inconnu")
    app.logger.info("Dossier for %s — keys: %s", ref, list(body.keys()))

    # Generate quartier text if missing
    texte_q = body.get("texte_quartier") or ""
    if not texte_q:
        try:
            texte_q = _gpt_quartier(
                body.get("adresse", ""), body.get("ville", "Vannes"),
                body.get("type_bien", ""))
        except Exception:
            pass
        if not texte_q:
            v = body.get("ville", "Vannes")
            texte_q = (
                "Situe a " + v + ", ce bien beneficie d'une localisation strategique "
                "dans un secteur economiquement actif du Morbihan.")
        body["texte_quartier"] = texte_q

    try:
        pdf_buf = generate_dossier_pdf(body)
        fname = "Dossier_Commercial_" + ref + ".pdf"
        return send_file(pdf_buf, mimetype="application/pdf",
                         as_attachment=True, download_name=fname)
    except Exception as e:
        app.logger.error("Dossier error %s: %s", ref, e, exc_info=True)
        return jsonify({"error": str(e)}), 500

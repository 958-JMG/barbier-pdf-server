#!/usr/bin/env python3
"""
Barbier Immobilier - PDF Dossier de Vente v5.1
Flask app deployed on Railway.
Routes: GET /, POST /generate-quartier, POST /dossier
"""

import html
import io
import json
import math
import os
import re
import logging
import base64 as _b64

import requests
from flask import Flask, request, jsonify, send_file
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle

from assets import LOGO_B64, PICTO_SURFACE_B64, PICTO_TYPE_B64, PICTO_LIEU_B64, PICTO_VILLE_B64

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Constants — Charte graphique Barbier Immobilier (couleurs du logo)
# ---------------------------------------------------------------------------
TEAL = colors.HexColor("#16708B")
TEAL_DARK = colors.HexColor("#0D5570")
TEAL_LIGHT = colors.HexColor("#E8F5F8")
ORANGE = colors.HexColor("#F0795B")       # orange logo
ORANGE_DARK = colors.HexColor("#E8632A")  # orange fonce accent
WHITE = colors.white
GRAY_DARK = colors.HexColor("#1F2937")
GRAY_MID = colors.HexColor("#6B7280")
GRAY_LIGHT = colors.HexColor("#F3F4F6")
GRAY_BORDER = colors.HexColor("#D1D5DB")

PAGE_W, PAGE_H = A4
MARGIN_L = 14 * mm
MARGIN_R = 14 * mm
CONTENT_W = PAGE_W - MARGIN_L - MARGIN_R
HEADER_H = 11 * mm
FOOTER_H = 9 * mm


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def _clean_text(html_str):
    """Strip HTML tags and decode HTML entities. Returns clean plain text."""
    if not html_str:
        return ""
    text = html.unescape(str(html_str))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _fmt_price(val):
    """Format a number as '693 000 EUR'."""
    if not val:
        return "\u2014"
    try:
        n = int(float(str(val).replace(" ", "").replace("\u202f", "").replace("\xa0", "")))
        return "{:,}".format(n).replace(",", " ") + " \u20ac"
    except (ValueError, TypeError):
        return str(val)


def _safe(val, fallback="\u2014"):
    if val is None or str(val).strip() == "" or val == 0:
        return fallback
    return str(val)


def _img_reader(b64_str):
    """Create a ReportLab ImageReader from a base64 string."""
    return ImageReader(io.BytesIO(_b64.b64decode(b64_str)))


def _fetch_photo(url_or_data):
    """Load a photo from a data URL (base64) or HTTP URL. Returns ImageReader or None."""
    if not url_or_data:
        return None
    try:
        if str(url_or_data).startswith("data:"):
            _, b64data = str(url_or_data).split(",", 1)
            return ImageReader(io.BytesIO(_b64.b64decode(b64data)))
        resp = requests.get(str(url_or_data), timeout=15, headers={"User-Agent": "BarbierImmo/1.0"})
        if resp.status_code == 200:
            ct = resp.headers.get("Content-Type", "")
            if "image" in ct or resp.content[:4] in (b"\xff\xd8\xff\xe0", b"\x89PNG", b"\xff\xd8\xff\xe1"):
                return ImageReader(io.BytesIO(resp.content))
    except Exception as exc:
        app.logger.error("Photo fetch failed: %s", exc)
    return None


def _get_first_photo(data):
    """Extract the first usable photo from the payload.
    Cockpit sends photos as data:image/... base64 strings in the 'photos' list."""
    photos = data.get("photos") or []
    if photos and isinstance(photos, list):
        for p in photos:
            if p:
                return _fetch_photo(p)
    url = data.get("Photo principale URL", "")
    if url:
        return _fetch_photo(url)
    return None


def _get_all_photos(data):
    """Get all photo ImageReaders from payload."""
    result = []
    photos = data.get("photos") or []
    if photos and isinstance(photos, list):
        for p in photos:
            if p:
                img = _fetch_photo(p)
                if img:
                    result.append(img)
    if not result:
        url = data.get("Photo principale URL", "")
        if url:
            img = _fetch_photo(url)
            if img:
                result.append(img)
    return result


def _geocode(adresse, ville):
    """Geocode via BAN (api-adresse.data.gouv.fr). Returns (lat, lon) or (None, None)."""
    try:
        import urllib.parse
        q = urllib.parse.quote(str(adresse) + ", " + str(ville) + ", France")
        r = requests.get(
            "https://api-adresse.data.gouv.fr/search/?q=" + q + "&limit=1",
            headers={"User-Agent": "BarbierImmo/1.0"},
            timeout=10,
        )
        features = r.json().get("features", [])
        if features:
            lon, lat = features[0]["geometry"]["coordinates"]
            return float(lat), float(lon)
    except Exception as exc:
        app.logger.error("Geocode failed: %s", exc)
    return None, None


def _fetch_map_image(adresse, ville, out_w=840, out_h=400, zoom=16):
    """Fetch OSM tiles and compose a map centered on the address.
    Returns a BytesIO PNG buffer, or None."""
    try:
        lat, lon = _geocode(adresse, ville)
        if lat is None:
            return None, None, None

        T = 256
        lr = math.radians(lat)
        n = 2 ** zoom
        fx = (lon + 180) / 360 * n
        fy = (1 - math.log(math.tan(lr) + 1 / math.cos(lr)) / math.pi) / 2 * n
        tx, ty = int(fx), int(fy)
        sub_x = (fx - tx) * T
        sub_y = (fy - ty) * T

        gc, gr = 5, 4
        ox, oy = tx - 2, ty - 1
        canvas_img = Image.new("RGB", (gc * T, gr * T), (220, 220, 220))
        headers = {"User-Agent": "BarbierImmo/1.0"}
        for dc in range(gc):
            for dr in range(gr):
                url = "https://tile.openstreetmap.org/{}/{}/{}.png".format(zoom, ox + dc, oy + dr)
                tr = requests.get(url, headers=headers, timeout=8)
                if tr.status_code == 200:
                    canvas_img.paste(
                        Image.open(io.BytesIO(tr.content)).convert("RGB"),
                        (dc * T, dr * T),
                    )

        mx = (tx - ox) * T + sub_x
        my = (ty - oy) * T + sub_y
        l = max(0, int(mx - out_w / 2))
        t = max(0, int(my - out_h / 2))
        r2 = l + out_w
        b = t + out_h
        if r2 > gc * T:
            l = gc * T - out_w
            r2 = gc * T
        if b > gr * T:
            t = gr * T - out_h
            b = gr * T
        l = max(0, l)
        t = max(0, t)
        cropped = canvas_img.crop((l, t, r2, b))

        mkx = int(mx - l)
        mky = int(my - t)
        d = ImageDraw.Draw(cropped)
        R = 15
        d.ellipse([mkx - R + 3, mky - R + 3, mkx + R + 3, mky + R + 3], fill=(0, 0, 0, 50))
        d.ellipse([mkx - R, mky - R, mkx + R, mky + R], fill=(240, 121, 91), outline=(255, 255, 255), width=4)
        d.ellipse([mkx - 5, mky - 5, mkx + 5, mky + 5], fill=(255, 255, 255))

        buf = io.BytesIO()
        cropped.save(buf, "PNG")
        buf.seek(0)
        return buf, lat, lon
    except Exception as exc:
        app.logger.error("OSM map error: %s", exc)
        return None, None, None


def _fetch_poi(lat, lon, radius=500):
    """Fetch points of interest near coordinates via Overpass API.
    Returns list of dicts with name, type, distance."""
    pois = []
    try:
        query = (
            "[out:json][timeout:10];"
            "("
            'node["amenity"~"restaurant|cafe|bank|pharmacy|school|post_office|supermarket|hospital|parking"](around:'
            + str(radius) + "," + str(lat) + "," + str(lon) + ");"
            'node["shop"~"supermarket|bakery|convenience"](around:'
            + str(radius) + "," + str(lat) + "," + str(lon) + ");"
            ");"
            "out body 20;"
        )
        resp = requests.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": query},
            timeout=10,
            headers={"User-Agent": "BarbierImmo/1.0"},
        )
        if resp.status_code == 200:
            elements = resp.json().get("elements", [])
            type_labels = {
                "restaurant": "Restaurant",
                "cafe": "Cafe",
                "bank": "Banque",
                "pharmacy": "Pharmacie",
                "school": "Ecole",
                "post_office": "Poste",
                "supermarket": "Supermarche",
                "hospital": "Hopital",
                "parking": "Parking",
                "bakery": "Boulangerie",
                "convenience": "Commerces",
            }
            type_icons = {
                "restaurant": "\U0001F374",
                "cafe": "\u2615",
                "bank": "\U0001F3E6",
                "pharmacy": "\u2695",
                "school": "\U0001F393",
                "post_office": "\U0001F4EE",
                "supermarket": "\U0001F6D2",
                "hospital": "\U0001F3E5",
                "parking": "\U0001F17F",
                "bakery": "\U0001F35E",
                "convenience": "\U0001F6D2",
            }
            for el in elements:
                tags = el.get("tags", {})
                name = tags.get("name", "")
                if not name:
                    continue
                amenity = tags.get("amenity", "") or tags.get("shop", "")
                elat = el.get("lat", lat)
                elon = el.get("lon", lon)
                dist = int(111320 * math.sqrt(
                    (elat - lat) ** 2 + ((elon - lon) * math.cos(math.radians(lat))) ** 2
                ))
                pois.append({
                    "name": name,
                    "type": type_labels.get(amenity, amenity.capitalize()),
                    "icon": type_icons.get(amenity, "\U0001F4CD"),
                    "distance": dist,
                })
            pois.sort(key=lambda x: x["distance"])
    except Exception as exc:
        app.logger.error("POI fetch error: %s", exc)
    return pois[:8]


def _generate_quartier_text(adresse, ville, type_bien):
    """Call OpenAI GPT-4o-mini to generate a neighbourhood description."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return ""
    ville_str = ville or "Vannes"
    adresse_str = adresse or ville_str
    prompt = (
        "Tu es un expert en immobilier commercial dans le Golfe du Morbihan (Bretagne Sud).\n"
        "Redige un texte de presentation de la ville et du secteur, destine a un futur locataire ou acquereur.\n\n"
        "Secteur : " + adresse_str + ", " + ville_str + " (Morbihan, 56)\n"
        "Type de bien : " + (type_bien or "Local commercial") + "\n\n"
        "Le texte doit comporter 5 a 6 phrases riches (160-220 mots), en texte continu, sans titre ni liste.\n"
        "Aborde obligatoirement :\n"
        "1. L'attractivite economique de " + ville_str + "\n"
        "2. Le secteur specifique : " + adresse_str + "\n"
        "3. L'accessibilite : axes routiers, parkings, transports\n"
        "4. L'environnement commercial a proximite\n"
        "5. Pourquoi ce secteur est strategique\n\n"
        "Ton : editorial, valorisant, vendeur. Pas de formule vague."
    )
    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 500,
                "temperature": 0.65,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        app.logger.error("GPT quartier error: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# PDF Drawing Helpers
# ---------------------------------------------------------------------------
def _rrect(c, x, y, w, h, r=4, fill_color=None, stroke_color=None, sw=0.6):
    """Draw a rounded rectangle."""
    c.saveState()
    if fill_color:
        c.setFillColor(fill_color)
    if stroke_color:
        c.setStrokeColor(stroke_color)
        c.setLineWidth(sw)
    p = c.beginPath()
    p.moveTo(x + r, y)
    p.lineTo(x + w - r, y)
    p.arcTo(x + w - 2 * r, y, x + w, y + 2 * r, -90, 90)
    p.lineTo(x + w, y + h - r)
    p.arcTo(x + w - 2 * r, y + h - 2 * r, x + w, y + h, 0, 90)
    p.lineTo(x + r, y + h)
    p.arcTo(x, y + h - 2 * r, x + 2 * r, y + h, 90, 90)
    p.lineTo(x, y + r)
    p.arcTo(x, y, x + 2 * r, y + 2 * r, 180, 90)
    p.close()
    c.drawPath(p, fill=1 if fill_color else 0, stroke=1 if stroke_color else 0)
    c.restoreState()


# ---------------------------------------------------------------------------
# Common page elements
# ---------------------------------------------------------------------------
def _build_header(c, subtitle=""):
    """Draw the teal header bar at the top of pages 2+."""
    c.setFillColor(TEAL)
    c.rect(0, PAGE_H - HEADER_H, PAGE_W, HEADER_H, fill=1, stroke=0)
    c.saveState()
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(MARGIN_L, PAGE_H - 7.5 * mm, "DOSSIER DE PRESENTATION  >  " + subtitle.upper()[:70])
    c.restoreState()
    try:
        bar_h = HEADER_H
        w = 18 * mm
        h = w * (662 / 488)
        if h > bar_h * 0.90:
            h = bar_h * 0.90
            w = h * (488 / 662)
        lx = PAGE_W - w - 4 * mm
        ly = (PAGE_H - bar_h) + (bar_h - h) / 2
        c.drawImage(_img_reader(LOGO_B64), lx, ly, width=w, height=h, mask="auto")
    except Exception as exc:
        app.logger.error("Header logo error: %s", exc)


def _build_footer(c, page_num, total=3):
    """Draw the teal footer bar."""
    c.setFillColor(TEAL)
    c.rect(0, 0, PAGE_W, FOOTER_H, fill=1, stroke=0)
    c.saveState()
    c.setFillColor(WHITE)
    c.setFont("Helvetica", 6.5)
    c.drawString(
        MARGIN_L, 3.5 * mm,
        "Barbier Immobilier \u2014 2 place Albert Einstein, 56000 Vannes \u2014 02.97.47.11.11 \u2014 barbierimmobilier.com",
    )
    c.drawRightString(PAGE_W - MARGIN_R, 3.5 * mm, str(page_num) + " / " + str(total))
    c.restoreState()


def _section_title(c, text, x, y, w=None):
    """Section title bar with orange left accent."""
    sw = w if w is not None else CONTENT_W
    c.saveState()
    c.setFillColor(colors.HexColor("#EBF0F8"))
    c.rect(x, y, sw, 8 * mm, fill=1, stroke=0)
    c.setFillColor(ORANGE)
    c.rect(x, y, 3.5 * mm, 8 * mm, fill=1, stroke=0)
    c.setFillColor(TEAL_DARK)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(x + 8 * mm, y + 2.5 * mm, text)
    c.restoreState()


def _draw_image_cover(c, img, x, y, w, h, radius=3*mm):
    """Draw an image with cover-crop behavior inside a rounded rect."""
    try:
        iw, ih = img.getSize()
        target_ratio = w / h
        img_ratio = iw / ih if ih > 0 else 1
        if img_ratio > target_ratio:
            dh = h
            dw = h * img_ratio
            dx = x - (dw - w) / 2
            dy = y
        else:
            dw = w
            dh = w / img_ratio if img_ratio > 0 else h
            dx = x
            dy = y - (dh - h) / 2
        c.saveState()
        clip = c.beginPath()
        clip.roundRect(x, y, w, h, radius)
        c.clipPath(clip, stroke=0, fill=0)
        c.drawImage(img, dx, dy, dw, dh, mask="auto")
        c.restoreState()
    except Exception as exc:
        app.logger.error("Image cover draw error: %s", exc)


# ---------------------------------------------------------------------------
# PAGE 1 — Couverture
# ---------------------------------------------------------------------------
def _build_page1(c, data):
    # Top half: teal
    c.setFillColor(TEAL)
    c.rect(0, PAGE_H * 0.50, PAGE_W, PAGE_H * 0.50, fill=1, stroke=0)

    # Badge EXCLUSIVITE
    statut = str(data.get("statut_mandat") or "").lower()
    y_shift = 0
    if "exclusi" in statut:
        badge_txt = "EXCLUSIVITE"
        c.saveState()
        c.setFont("Helvetica-Bold", 11)
        bw = c.stringWidth(badge_txt, "Helvetica-Bold", 11) + 12 * mm
        bh = 8 * mm
        c.setFillColor(ORANGE)
        c.roundRect(MARGIN_L, PAGE_H - 28 * mm, bw, bh, 2 * mm, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.drawCentredString(MARGIN_L + bw / 2, PAGE_H - 23.5 * mm, badge_txt)
        c.restoreState()
        y_shift = 12 * mm

    # Type de bien (big title)
    c.saveState()
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 30)
    c.drawString(MARGIN_L, PAGE_H - 38 * mm - y_shift, _safe(data.get("type_bien"), "Bien immobilier"))
    c.restoreState()

    # Orange accent
    c.setFillColor(ORANGE)
    c.rect(MARGIN_L, PAGE_H - 41.5 * mm - y_shift, 40 * mm, 2 * mm, fill=1, stroke=0)

    # Address
    c.saveState()
    c.setFillColor(WHITE)
    c.setFont("Helvetica", 14)
    c.drawString(MARGIN_L, PAGE_H - 50 * mm - y_shift, _safe(data.get("adresse")))
    cp = _safe(data.get("code_postal"), "")
    vi = _safe(data.get("ville"), "")
    c.drawString(MARGIN_L, PAGE_H - 58 * mm - y_shift, (cp + " " + vi).strip())
    c.restoreState()

    # Price
    prix = data.get("prix") or 0
    prix_str = _fmt_price(prix)
    c.saveState()
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 34)
    c.drawString(MARGIN_L, PAGE_H - 84 * mm, prix_str)
    c.restoreState()

    # Honoraires detail
    honoraires = data.get("honoraires")
    honoraires_charge = data.get("honoraires_charge", "")
    if honoraires:
        c.saveState()
        c.setFillColor(colors.HexColor("#FFFFFFBB"))
        c.setFont("Helvetica", 9)
        ht = "Honoraires : " + _fmt_price(honoraires)
        if honoraires_charge:
            ht = ht + " (" + str(honoraires_charge) + ")"
        c.drawString(MARGIN_L, PAGE_H - 91 * mm, ht)
        c.restoreState()

    c.saveState()
    c.setFillColor(WHITE)
    c.setFont("Helvetica", 9)
    c.drawString(MARGIN_L, PAGE_H - (97 if honoraires else 91) * mm, "PRIX DE VENTE FAI")
    c.restoreState()

    # Characteristic pills at the junction
    pills_data = [("SURFACE", _safe(data.get("surface")) + " m\u00b2")]
    pills_data.append(("TYPE", _safe(data.get("type_bien"))))
    if data.get("statut_mandat"):
        pills_data.append(("MANDAT", _safe(data.get("statut_mandat"))))
    if data.get("activite"):
        pills_data.append(("ACTIVITE", _safe(data.get("activite"))))
    if data.get("etat_bien"):
        pills_data.append(("ETAT", _safe(data.get("etat_bien"))))
    pills_data = pills_data[:4]

    pill_count = len(pills_data)
    pill_gap = 2 * mm
    pill_w = (CONTENT_W - (pill_count - 1) * pill_gap) / pill_count
    pill_h = 22 * mm
    pill_y = PAGE_H * 0.50 - pill_h / 2 + 1 * mm

    for i, (lbl, val) in enumerate(pills_data):
        px = MARGIN_L + i * (pill_w + pill_gap)
        # Shadow
        c.saveState()
        c.setFillColor(colors.HexColor("#00000022"))
        c.roundRect(px + 0.5 * mm, pill_y - 0.5 * mm, pill_w, pill_h, 2 * mm, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.roundRect(px, pill_y, pill_w, pill_h, 2 * mm, fill=1, stroke=0)
        c.restoreState()
        # Orange top bar
        c.setFillColor(ORANGE)
        c.rect(px + 2 * mm, pill_y + pill_h - 2 * mm, pill_w - 4 * mm, 2 * mm, fill=1, stroke=0)
        # Label
        c.saveState()
        c.setFillColor(GRAY_MID)
        c.setFont("Helvetica", 7)
        c.drawCentredString(px + pill_w / 2, pill_y + pill_h - 7 * mm, lbl)
        # Value auto-fit
        c.setFillColor(TEAL_DARK)
        for fsz in [12, 10, 8, 7, 6]:
            c.setFont("Helvetica-Bold", fsz)
            if c.stringWidth(val, "Helvetica-Bold", fsz) < pill_w - 4 * mm:
                break
        c.drawCentredString(px + pill_w / 2, pill_y + 5 * mm, val)
        c.restoreState()

    # Bottom half: white + photo
    c.setFillColor(WHITE)
    c.rect(0, 0, PAGE_W, PAGE_H * 0.50, fill=1, stroke=0)

    photo_h = PAGE_H * 0.50 - 22 * mm
    photo_x = MARGIN_L
    photo_y = 20 * mm
    photo_w = CONTENT_W

    img = _get_first_photo(data)
    if img:
        _draw_image_cover(c, img, photo_x, photo_y, photo_w, photo_h)
    else:
        c.saveState()
        c.setFillColor(GRAY_LIGHT)
        c.setStrokeColor(GRAY_BORDER)
        c.setLineWidth(1)
        c.roundRect(photo_x, photo_y, photo_w, photo_h, 3 * mm, fill=1, stroke=1)
        c.setFillColor(GRAY_MID)
        c.setFont("Helvetica", 10)
        c.drawCentredString(photo_x + photo_w / 2, photo_y + photo_h / 2, "[ Photo principale du bien ]")
        c.restoreState()

    # Logo top-right
    try:
        logo_w = 28 * mm
        logo_h = logo_w * (662 / 488)
        logo_x = PAGE_W - logo_w - 8 * mm
        logo_y = PAGE_H - logo_h - 5 * mm
        pad = 2.5 * mm
        c.setFillColor(WHITE)
        c.roundRect(logo_x - pad, logo_y - pad, logo_w + pad * 2, logo_h + pad * 2, 3 * mm, fill=1, stroke=0)
        c.drawImage(_img_reader(LOGO_B64), logo_x, logo_y, width=logo_w, height=logo_h, mask="auto")
    except Exception as exc:
        app.logger.error("Logo P1 error: %s", exc)

    # Bottom info
    c.saveState()
    c.setFillColor(GRAY_DARK)
    c.setFont("Helvetica", 7.5)
    neg = _safe(data.get("negociateur"), "Barbier Immobilier")
    ref = _safe(data.get("reference"))
    c.drawString(MARGIN_L, 13 * mm, "Dossier prepare par  " + neg + "  \u00b7  Ref. " + ref)
    c.restoreState()

    _build_footer(c, 1)


# ---------------------------------------------------------------------------
# PAGE 2 — Quartier & Localisation
# ---------------------------------------------------------------------------
def _build_page2(c, data):
    sub = _safe(data.get("type_bien")) + " \u2014 " + _safe(data.get("adresse")) + ", " + _safe(data.get("ville"))
    _build_header(c, sub)

    # Layout: map + POI anchored at bottom, quartier text on top
    map_h = 68 * mm
    poi_h = 30 * mm
    map_y = FOOTER_H + poi_h + 5 * mm
    map_x = MARGIN_L
    map_w = CONTENT_W

    # Section title "Quartier"
    sec_y = PAGE_H - HEADER_H - 15 * mm
    _section_title(c, "Quartier", MARGIN_L, sec_y)

    # Quartier text
    text_top = sec_y - 3 * mm
    text_bottom = map_y + map_h + 12 * mm
    available_h = text_top - text_bottom

    quartier_text = _clean_text(data.get("texte_quartier", ""))
    if quartier_text:
        style = ParagraphStyle(
            "quartier", fontName="Helvetica", fontSize=9,
            leading=13.5, textColor=GRAY_DARK, alignment=4,
        )
        para = Paragraph(quartier_text.replace("\n", "<br/>"), style)
        pw, ph = para.wrap(CONTENT_W, available_h)
        draw_y = text_top - ph
        if draw_y < text_bottom:
            draw_y = text_bottom
        para.drawOn(c, MARGIN_L, draw_y)

    # Section "Localisation"
    loc_sec_y = map_y + map_h + 2 * mm
    _section_title(c, "Localisation", MARGIN_L, loc_sec_y)

    # OSM Map
    adresse = data.get("adresse", "")
    ville = data.get("ville", "")
    map_buf, lat, lon = _fetch_map_image(adresse, ville, out_w=840, out_h=400, zoom=16)
    if map_buf:
        try:
            c.saveState()
            clip = c.beginPath()
            clip.roundRect(map_x, map_y, map_w, map_h, 2 * mm)
            c.clipPath(clip, stroke=0, fill=0)
            c.drawImage(ImageReader(map_buf), map_x, map_y, width=map_w, height=map_h, preserveAspectRatio=False)
            c.restoreState()
        except Exception as exc:
            app.logger.error("Map draw error: %s", exc)
            _draw_map_placeholder(c, map_x, map_y, map_w, map_h)
    else:
        _draw_map_placeholder(c, map_x, map_y, map_w, map_h)

    # Address label under map
    c.saveState()
    c.setFillColor(TEAL)
    c.setFont("Helvetica-Bold", 7)
    c.drawString(map_x, map_y - 4 * mm, "\u25a0  " + str(adresse) + ", " + str(ville))
    c.restoreState()

    # POI section at bottom
    if lat and lon:
        pois = _fetch_poi(lat, lon, radius=600)
        if pois:
            poi_y_start = FOOTER_H + 3 * mm
            poi_col_w = CONTENT_W / 2
            c.saveState()
            for i, poi in enumerate(pois[:8]):
                col = i % 2
                row = i // 2
                px = MARGIN_L + col * poi_col_w
                py = poi_y_start + (3 - row) * 7 * mm
                c.setFillColor(TEAL_DARK)
                c.setFont("Helvetica-Bold", 7)
                c.drawString(px, py, poi["type"])
                c.setFillColor(GRAY_DARK)
                c.setFont("Helvetica", 7)
                c.drawString(px + 22 * mm, py, poi["name"][:30])
                c.setFillColor(GRAY_MID)
                c.setFont("Helvetica", 6.5)
                c.drawRightString(px + poi_col_w - 2 * mm, py, str(poi["distance"]) + " m")
            c.restoreState()

    _build_footer(c, 2)


def _draw_map_placeholder(c, x, y, w, h):
    _rrect(c, x, y, w, h, fill_color=GRAY_LIGHT, stroke_color=GRAY_BORDER)
    c.saveState()
    c.setFillColor(GRAY_MID)
    c.setFont("Helvetica", 9)
    c.drawCentredString(x + w / 2, y + h / 2, "Carte indisponible")
    c.restoreState()


# ---------------------------------------------------------------------------
# PAGE 3 — Annonce & Donnees financieres
# ---------------------------------------------------------------------------
def _build_page3(c, data):
    sub = _safe(data.get("type_bien")) + " \u2014 " + _safe(data.get("adresse")) + ", " + _safe(data.get("ville"))
    _build_header(c, sub)

    y = PAGE_H - HEADER_H - 15 * mm

    # Section: Annonce
    _section_title(c, "Annonce", MARGIN_L, y)
    y -= 5 * mm

    # Structured annonce with rich formatting
    desc_raw = data.get("description", "")
    desc_text = _clean_text(desc_raw)

    max_annonce_h = 105 * mm
    if desc_text:
        y = _render_annonce(c, desc_text, MARGIN_L, y, CONTENT_W, max_annonce_h)
        y -= 6 * mm
    else:
        y -= 3 * mm

    # Section: Donnees financieres
    _section_title(c, "Donnees financieres", MARGIN_L, y)
    y -= 5 * mm

    # Financial rows
    locataire = data.get("locataire")
    loyer_annuel = data.get("loyer_annuel_ht")

    fin_rows = []
    if locataire or loyer_annuel:
        if locataire:
            fin_rows.append(("Locataire", str(locataire)))
        if data.get("loyer_mensuel"):
            fin_rows.append(("Loyer mensuel HT", _fmt_price(data.get("loyer_mensuel"))))
        if loyer_annuel:
            fin_rows.append(("Loyer annuel HT", _fmt_price(loyer_annuel)))
        if data.get("loyer_initial_ht"):
            fin_rows.append(("Loyer initial HT", _fmt_price(data.get("loyer_initial_ht"))))
        if data.get("evolution_loyer"):
            fin_rows.append(("Evolution loyer", str(data.get("evolution_loyer"))))
        if data.get("duree_bail"):
            fin_rows.append(("Duree du bail", str(data.get("duree_bail"))))

    taxe = data.get("taxe_fonciere")
    if taxe:
        fin_rows.append(("Taxe fonciere", _fmt_price(taxe)))

    if fin_rows:
        row_h = 6.5 * mm
        box_h = len(fin_rows) * row_h + 5 * mm
        box_y = y - box_h
        _rrect(c, MARGIN_L, box_y, CONTENT_W, box_h, r=3, fill_color=colors.HexColor("#F8FAFB"), stroke_color=GRAY_BORDER, sw=0.5)

        ry = y - 4 * mm
        for i, (label, value) in enumerate(fin_rows):
            # Alternate row shading
            if i % 2 == 0:
                c.saveState()
                c.setFillColor(colors.HexColor("#EFF3F6"))
                c.rect(MARGIN_L + 1, ry - 1.5 * mm, CONTENT_W - 2, row_h, fill=1, stroke=0)
                c.restoreState()
            c.saveState()
            c.setFillColor(GRAY_MID)
            c.setFont("Helvetica", 8)
            c.drawString(MARGIN_L + 5 * mm, ry, label)
            c.setFillColor(TEAL_DARK)
            c.setFont("Helvetica-Bold", 9)
            c.drawRightString(MARGIN_L + CONTENT_W - 5 * mm, ry, value)
            c.restoreState()
            ry -= row_h

        y = box_y - 8 * mm

    # Prix recap block
    prix_box_h = 24 * mm
    prix_box_y = max(FOOTER_H + 5 * mm, y - prix_box_h)
    _rrect(c, MARGIN_L, prix_box_y, CONTENT_W, prix_box_h, r=4, fill_color=TEAL)

    c.saveState()
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(MARGIN_L + CONTENT_W / 2, prix_box_y + 13 * mm, _fmt_price(data.get("prix")))
    c.setFont("Helvetica", 7.5)
    parts = ["Prix de vente FAI"]
    hono = data.get("honoraires")
    net = data.get("prix_net_vendeur")
    if hono:
        parts.append("Honoraires : " + _fmt_price(hono))
    if net:
        parts.append("Net vendeur : " + _fmt_price(net))
    c.drawCentredString(MARGIN_L + CONTENT_W / 2, prix_box_y + 4 * mm, "  |  ".join(parts))
    c.restoreState()

    _build_footer(c, 3)


def _render_annonce(c, text, x, y, w, max_h):
    """Render the annonce text with editorial formatting:
    - First line as bold title in teal
    - Lines in CAPS as bold subtitles
    - Bullet-like lines with bullet prefix
    - Regular paragraphs in body style
    """
    lines = text.split("\n")
    cursor = y
    min_y = y - max_h

    style_title = ParagraphStyle(
        "ann_title", fontName="Helvetica-Bold", fontSize=10.5,
        leading=14, textColor=TEAL_DARK, spaceAfter=2,
    )
    style_subtitle = ParagraphStyle(
        "ann_sub", fontName="Helvetica-Bold", fontSize=9,
        leading=13, textColor=TEAL_DARK, spaceBefore=3,
    )
    style_body = ParagraphStyle(
        "ann_body", fontName="Helvetica", fontSize=8.5,
        leading=12, textColor=GRAY_DARK, alignment=4,
    )
    style_bullet = ParagraphStyle(
        "ann_bullet", fontName="Helvetica", fontSize=8.5,
        leading=12, textColor=GRAY_DARK,
        leftIndent=4 * mm, firstLineIndent=-4 * mm,
    )
    style_kv = ParagraphStyle(
        "ann_kv", fontName="Helvetica", fontSize=8.5,
        leading=12, textColor=GRAY_DARK,
    )

    first_line = True
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line:
            continue
        if cursor < min_y:
            break

        # First non-empty line = title
        if first_line:
            first_line = False
            safe_line = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            p = Paragraph("<b>" + safe_line + "</b>", style_title)
            pw, ph = p.wrap(w, 999)
            if cursor - ph < min_y:
                break
            cursor -= ph
            p.drawOn(c, x, cursor)
            cursor -= 2 * mm
            continue

        # ALL CAPS line = subtitle (like "LES POINTS CLES DE L INVESTISSEMENT")
        if line == line.upper() and len(line) > 5 and not line[0].isdigit():
            safe_line = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            # Draw a subtle background
            p = Paragraph(safe_line, style_subtitle)
            pw, ph = p.wrap(w - 6 * mm, 999)
            bg_h = ph + 2 * mm
            if cursor - bg_h < min_y:
                break
            cursor -= 1 * mm
            c.saveState()
            c.setFillColor(colors.HexColor("#EBF0F8"))
            c.roundRect(x, cursor - ph - 1 * mm, w, bg_h, 1 * mm, fill=1, stroke=0)
            c.setFillColor(ORANGE)
            c.rect(x, cursor - ph - 1 * mm, 3 * mm, bg_h, fill=1, stroke=0)
            c.restoreState()
            cursor -= ph
            p.drawOn(c, x + 6 * mm, cursor)
            cursor -= 3 * mm
            continue

        # Key:value lines (like "Locataire en place : FONCIA SOGIV")
        if ":" in line and len(line.split(":")[0]) < 40:
            parts = line.split(":", 1)
            key = parts[0].strip()
            val = parts[1].strip() if len(parts) > 1 else ""
            safe_key = key.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            safe_val = val.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            # Bold amounts
            safe_val = re.sub(
                r"(\d[\d\s]*(?:\u20ac|%|m\u00b2|EUR|euros?|ans?)[^\.,;]*)",
                r"<b>\1</b>",
                safe_val,
            )
            p = Paragraph("<b>" + safe_key + " :</b> " + safe_val, style_kv)
            pw, ph = p.wrap(w, 999)
            if cursor - ph < min_y:
                break
            cursor -= ph
            p.drawOn(c, x, cursor)
            cursor -= 0.5 * mm
            continue

        # Regular paragraph
        safe_line = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        # Bold numbers and amounts
        safe_line = re.sub(
            r"(\d[\d\s]*(?:\u20ac|%|m\u00b2|EUR|euros?)[^\.,;]*)",
            r"<b>\1</b>",
            safe_line,
        )
        p = Paragraph(safe_line, style_body)
        pw, ph = p.wrap(w, 999)
        if cursor - ph < min_y:
            break
        cursor -= ph
        p.drawOn(c, x, cursor)
        cursor -= 1 * mm

    return cursor


# ---------------------------------------------------------------------------
# PDF Generation
# ---------------------------------------------------------------------------
def generate_dossier_pdf(data):
    """Generate the 3-page dossier PDF."""
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=A4)
    ref = data.get("reference", "")
    c.setTitle("Dossier de Presentation \u2014 " + ref + " \u2014 Barbier Immobilier")
    c.setAuthor("Barbier Immobilier")

    _build_page1(c, data)
    c.showPage()

    _build_page2(c, data)
    c.showPage()

    _build_page3(c, data)
    c.showPage()

    c.save()
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# Flask Routes
# ---------------------------------------------------------------------------
@app.route("/")
def health():
    return jsonify({"service": "Barbier PDF Generator", "status": "ok", "version": "5.1"})


@app.route("/generate-quartier", methods=["POST"])
def generate_quartier():
    """Generate neighbourhood text via GPT."""
    body = request.get_json(silent=True) or {}
    adresse = body.get("adresse", "")
    ville = body.get("ville", "")
    type_bien = body.get("type_bien", "")
    if not ville:
        return jsonify({"error": "Champ 'ville' requis"}), 400
    texte = _generate_quartier_text(adresse, ville, type_bien)
    return jsonify({"texte_quartier": texte})


@app.route("/dossier", methods=["POST"])
def dossier():
    """Generate and return a 3-page PDF dossier."""
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "Body JSON manquant"}), 400

    reference = body.get("reference", "inconnu")
    app.logger.info("Generating dossier for %s", reference)

    try:
        pdf_buf = generate_dossier_pdf(body)
        filename = "Dossier_Commercial_" + reference + ".pdf"
        return send_file(
            pdf_buf,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename,
        )
    except Exception as exc:
        app.logger.error("Dossier generation error for %s: %s", reference, exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500

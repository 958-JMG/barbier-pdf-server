#!/usr/bin/env python3
"""
Barbier Immobilier - PDF Dossier de Vente v5.0
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

from assets import LOGO_B64, PICTO_SURFACE_B64, PICTO_TYPE_B64, PICTO_LIEU_B64, PICTO_VILLE_B64

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Constants - Charte graphique Barbier Immobilier
# ---------------------------------------------------------------------------
TEAL = colors.HexColor("#16708B")
TEAL_DARK = colors.HexColor("#0D5570")
TEAL_LIGHT = colors.HexColor("#E8F5F8")
ORANGE = colors.HexColor("#E8472A")
WHITE = colors.white
GRAY_DARK = colors.HexColor("#1F2937")
GRAY_MID = colors.HexColor("#6B7280")
GRAY_LIGHT = colors.HexColor("#F3F4F6")
GRAY_BORDER = colors.HexColor("#D1D5DB")

PAGE_W, PAGE_H = A4  # 595.27 x 841.89 points
MARGIN_L = 14 * mm
MARGIN_R = 14 * mm
MARGIN_T = 12 * mm
MARGIN_B = 12 * mm
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
        return "—"
    try:
        n = int(float(str(val).replace(" ", "").replace("\u202f", "")))
        return "{:,}".format(n).replace(",", " ") + " \u20ac"
    except (ValueError, TypeError):
        return str(val)


def _safe(val, fallback="—"):
    if val is None or val == "" or val == 0:
        return fallback
    return str(val)


def _img_reader(b64_str):
    """Create a ReportLab ImageReader from a base64 string."""
    import base64
    return ImageReader(io.BytesIO(base64.b64decode(b64_str)))


def _fetch_photo(url):
    """Download a photo URL and return an ImageReader, or None on failure."""
    if not url:
        return None
    try:
        if url.startswith("data:"):
            import base64
            _, b64data = url.split(",", 1)
            return ImageReader(io.BytesIO(base64.b64decode(b64data)))
        resp = requests.get(url, timeout=15, headers={"User-Agent": "BarbierImmo/1.0"})
        if resp.status_code == 200:
            ct = resp.headers.get("Content-Type", "")
            if "image" in ct or resp.content[:4] in (b"\xff\xd8\xff\xe0", b"\x89PNG", b"\xff\xd8\xff\xe1"):
                return ImageReader(io.BytesIO(resp.content))
    except Exception as exc:
        app.logger.error("Photo fetch failed for %s: %s", url[:80], exc)
    return None


def _geocode(adresse, ville):
    """Geocode via BAN (api-adresse.data.gouv.fr). Returns (lat, lon) or (None, None)."""
    try:
        import urllib.parse
        q = urllib.parse.quote(adresse + ", " + ville + ", France")
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
    """Fetch OSM tiles and compose a map image centered on the address.
    Returns a BytesIO PNG buffer, or None on failure."""
    try:
        lat, lon = _geocode(adresse, ville)
        if lat is None:
            return None

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
        d.ellipse([mkx - R, mky - R, mkx + R, mky + R], fill=(232, 71, 42), outline=(255, 255, 255), width=4)
        d.ellipse([mkx - 5, mky - 5, mkx + 5, mky + 5], fill=(255, 255, 255))

        buf = io.BytesIO()
        cropped.save(buf, "PNG")
        buf.seek(0)
        return buf
    except Exception as exc:
        app.logger.error("OSM map error: %s", exc)
        return None


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
def _draw_rounded_rect(c, x, y, w, h, r=4, fill_color=None, stroke_color=None, sw=0.6):
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


def _draw_pill(c, x, y, w, h, picto_b64, label, value):
    """Draw a characteristic pill with icon, label, and value."""
    _draw_rounded_rect(c, x, y, w, h, r=2 * mm, fill_color=GRAY_LIGHT, stroke_color=GRAY_BORDER, sw=0.5)
    icon_r = 5.5 * mm
    icon_cx = x + icon_r + 2 * mm
    icon_cy = y + h / 2
    c.saveState()
    c.setFillColor(colors.HexColor("#F0F4F8"))
    c.circle(icon_cx, icon_cy, icon_r, fill=1, stroke=0)
    c.restoreState()
    try:
        ico = _img_reader(picto_b64)
        s = icon_r * 1.2
        c.drawImage(ico, icon_cx - s / 2, icon_cy - s / 2, width=s, height=s, mask="auto")
    except Exception:
        pass
    text_x = x + icon_r * 2 + 5 * mm
    max_text_w = w - icon_r * 2 - 8 * mm
    # Label
    c.saveState()
    c.setFillColor(GRAY_MID)
    c.setFont("Helvetica", 6.5)
    lbl = label.upper()
    while lbl and c.stringWidth(lbl, "Helvetica", 6.5) > max_text_w:
        lbl = lbl[:-1]
    c.drawString(text_x, y + h - 4.5 * mm, lbl)
    c.restoreState()
    # Value
    c.saveState()
    c.setFillColor(TEAL_DARK)
    val_str = str(value)
    for fsz in [9.5, 9, 8, 7]:
        c.setFont("Helvetica-Bold", fsz)
        if c.stringWidth(val_str, "Helvetica-Bold", fsz) <= max_text_w:
            break
    c.drawString(text_x, y + 3.5 * mm, val_str)
    c.restoreState()


# ---------------------------------------------------------------------------
# Common page elements
# ---------------------------------------------------------------------------
def _build_header(c, subtitle=""):
    """Draw the teal header bar at the top of pages 2-3."""
    c.setFillColor(TEAL)
    c.rect(0, PAGE_H - HEADER_H, PAGE_W, HEADER_H, fill=1, stroke=0)
    c.saveState()
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(MARGIN_L, PAGE_H - 7.5 * mm, "DOSSIER DE PRESENTATION  >  " + subtitle.upper())
    c.restoreState()
    # Small logo top-right
    try:
        bar_h = HEADER_H
        w = 18 * mm
        h = w * (662 / 488)
        if h > bar_h * 0.90:
            h = bar_h * 0.90
            w = h * (488 / 662)
        bar_top = PAGE_H - bar_h
        lx = PAGE_W - w - 4 * mm
        ly = bar_top + (bar_h - h) / 2
        c.drawImage(_img_reader(LOGO_B64), lx, ly, width=w, height=h, mask="auto")
    except Exception as exc:
        app.logger.error("Header logo error: %s", exc)


def _build_footer(c, page_num, total=3):
    """Draw the teal footer bar with agency info and page number."""
    c.setFillColor(TEAL)
    c.rect(0, 0, PAGE_W, FOOTER_H, fill=1, stroke=0)
    c.saveState()
    c.setFillColor(WHITE)
    c.setFont("Helvetica", 6.5)
    c.drawString(
        MARGIN_L, 3.5 * mm,
        "Barbier Immobilier — 2 place Albert Einstein, 56000 Vannes — 02.97.47.11.11 — barbierimmobilier.com",
    )
    c.drawRightString(PAGE_W - MARGIN_R, 3.5 * mm, str(page_num) + " / " + str(total))
    c.restoreState()


def _section_title(c, text, x, y, w=None):
    """Draw a section title bar with orange left accent. Returns y unchanged."""
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


# ---------------------------------------------------------------------------
# Page Builders
# ---------------------------------------------------------------------------
def _build_page1(c, data):
    """Page 1: Cover - photo, prix, caracteristiques, adresse."""
    # Top half: teal background
    c.setFillColor(TEAL)
    c.rect(0, PAGE_H * 0.50, PAGE_W, PAGE_H * 0.50, fill=1, stroke=0)

    # Badge EXCLUSIVITE
    statut = str(data.get("statut_mandat") or "").lower()
    y_shift = 0
    if "exclusi" in statut:
        badge_txt = "EXCLUSIVITE"
        bh = 8 * mm
        c.saveState()
        c.setFont("Helvetica-Bold", 11)
        bw = c.stringWidth(badge_txt, "Helvetica-Bold", 11) + 12 * mm
        c.setFillColor(ORANGE)
        c.roundRect(MARGIN_L, PAGE_H - 28 * mm, bw, bh, 2 * mm, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.drawCentredString(MARGIN_L + bw / 2, PAGE_H - 23.5 * mm, badge_txt)
        c.restoreState()
        y_shift = 12 * mm

    # Type de bien title
    c.saveState()
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 30)
    c.drawString(MARGIN_L, PAGE_H - 38 * mm - y_shift, _safe(data.get("type_bien"), "Bien immobilier"))
    c.restoreState()

    # Orange accent line
    c.setFillColor(ORANGE)
    c.rect(MARGIN_L, PAGE_H - 41.5 * mm - y_shift, 40 * mm, 2 * mm, fill=1, stroke=0)

    # Address
    c.saveState()
    c.setFillColor(WHITE)
    c.setFont("Helvetica", 14)
    c.drawString(MARGIN_L, PAGE_H - 50 * mm - y_shift, _safe(data.get("adresse")))
    code_postal = _safe(data.get("code_postal"), "")
    ville = _safe(data.get("ville"), "")
    c.drawString(MARGIN_L, PAGE_H - 58 * mm - y_shift, (code_postal + " " + ville).strip())
    c.restoreState()

    # Price block
    prix_str = _fmt_price(data.get("prix"))
    c.saveState()
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 34)
    c.drawString(MARGIN_L, PAGE_H - 84 * mm, prix_str)
    c.restoreState()

    # Honoraires info
    honoraires = data.get("honoraires")
    honoraires_charge = data.get("honoraires_charge", "")
    if honoraires:
        c.saveState()
        c.setFillColor(colors.HexColor("#FFFFFFBB"))
        c.setFont("Helvetica", 9)
        hono_text = "Honoraires : " + _fmt_price(honoraires)
        if honoraires_charge:
            hono_text = hono_text + " (" + str(honoraires_charge) + ")"
        c.drawString(MARGIN_L, PAGE_H - 91 * mm, hono_text)
        c.restoreState()

    # Label
    c.saveState()
    c.setFillColor(WHITE)
    c.setFont("Helvetica", 9)
    c.drawString(MARGIN_L, PAGE_H - 97 * mm, "PRIX DE VENTE FAI")
    c.restoreState()

    # Characteristic pills at the junction
    pills = [
        ("SURFACE", _safe(data.get("surface")) + " m\u00b2", PICTO_SURFACE_B64),
        ("TYPE", _safe(data.get("type_bien")), PICTO_TYPE_B64),
    ]
    if data.get("statut_mandat"):
        pills.append(("MANDAT", _safe(data.get("statut_mandat")), PICTO_LIEU_B64))
    if data.get("activite"):
        pills.append(("ACTIVITE", _safe(data.get("activite")), PICTO_VILLE_B64))
    pills = pills[:4]

    pill_count = len(pills)
    pill_gap = 2 * mm
    pill_w = (CONTENT_W - (pill_count - 1) * pill_gap) / pill_count
    pill_h = 16 * mm
    pill_y = PAGE_H * 0.50 - pill_h / 2

    for i, (lbl, val, picto) in enumerate(pills):
        px = MARGIN_L + i * (pill_w + pill_gap)
        # White pill with shadow effect
        c.saveState()
        c.setFillColor(colors.HexColor("#00000022"))
        c.roundRect(px + 0.5 * mm, pill_y - 0.5 * mm, pill_w, pill_h, 2 * mm, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.roundRect(px, pill_y, pill_w, pill_h, 2 * mm, fill=1, stroke=0)
        c.restoreState()
        # Orange top accent
        c.setFillColor(ORANGE)
        c.rect(px + 2 * mm, pill_y + pill_h - 2 * mm, pill_w - 4 * mm, 2 * mm, fill=1, stroke=0)
        # Label and value
        c.saveState()
        c.setFillColor(GRAY_MID)
        c.setFont("Helvetica", 7)
        c.drawCentredString(px + pill_w / 2, pill_y + pill_h - 7 * mm, lbl)
        c.setFillColor(TEAL_DARK)
        # Auto-fit value
        for fsz in [12, 10, 8, 7, 6]:
            c.setFont("Helvetica-Bold", fsz)
            if c.stringWidth(val, "Helvetica-Bold", fsz) < pill_w - 4 * mm:
                break
        c.drawCentredString(px + pill_w / 2, pill_y + 4 * mm, val)
        c.restoreState()

    # Bottom half: white + photo
    c.setFillColor(WHITE)
    c.rect(0, 0, PAGE_W, PAGE_H * 0.50, fill=1, stroke=0)

    photo_h = PAGE_H * 0.50 - 22 * mm
    photo_x = MARGIN_L
    photo_y = 20 * mm
    photo_w = CONTENT_W

    photo_url = data.get("Photo principale URL") or ""
    if not photo_url:
        photos = data.get("photos") or []
        if photos:
            photo_url = photos[0]

    img = _fetch_photo(photo_url)
    if img:
        try:
            iw, ih = img.getSize()
            target_ratio = photo_w / photo_h
            img_ratio = iw / ih if ih > 0 else 1
            if img_ratio > target_ratio:
                dh = photo_h
                dw = photo_h * img_ratio
                dx = photo_x - (dw - photo_w) / 2
                dy = photo_y
            else:
                dw = photo_w
                dh = photo_w / img_ratio if img_ratio > 0 else photo_h
                dx = photo_x
                dy = photo_y - (dh - photo_h) / 2
            c.saveState()
            clip = c.beginPath()
            clip.roundRect(photo_x, photo_y, photo_w, photo_h, 3 * mm)
            c.clipPath(clip, stroke=0, fill=0)
            c.drawImage(img, dx, dy, dw, dh, mask="auto")
            c.restoreState()
        except Exception as exc:
            app.logger.error("Photo draw error: %s", exc)
            _draw_photo_placeholder(c, photo_x, photo_y, photo_w, photo_h)
    else:
        _draw_photo_placeholder(c, photo_x, photo_y, photo_w, photo_h)

    # Logo top-right on teal
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

    # Bottom info line
    c.saveState()
    c.setFillColor(GRAY_DARK)
    c.setFont("Helvetica", 7.5)
    neg = _safe(data.get("negociateur"), "Barbier Immobilier")
    ref = _safe(data.get("reference"))
    c.drawString(MARGIN_L, 13 * mm, "Dossier prepare par  " + neg + "  -  Ref. " + ref)
    c.restoreState()

    _build_footer(c, 1)


def _draw_photo_placeholder(c, x, y, w, h):
    """Draw a placeholder when no photo is available."""
    c.saveState()
    c.setFillColor(GRAY_LIGHT)
    c.setStrokeColor(GRAY_BORDER)
    c.setLineWidth(1)
    c.roundRect(x, y, w, h, 3 * mm, fill=1, stroke=1)
    c.setFillColor(GRAY_MID)
    c.setFont("Helvetica", 10)
    c.drawCentredString(x + w / 2, y + h / 2 + 3 * mm, "[ Photo principale du bien ]")
    c.setFont("Helvetica", 8)
    c.drawCentredString(x + w / 2, y + h / 2 - 6 * mm, "Ajoutez une photo depuis le cockpit")
    c.restoreState()


def _build_page2(c, data):
    """Page 2: Quartier & Localisation."""
    sub = _safe(data.get("type_bien")) + " — " + _safe(data.get("adresse")) + ", " + _safe(data.get("ville"))
    _build_header(c, sub)

    # Fixed zones: text zone on top, map anchored at bottom
    map_h = 75 * mm
    map_y = FOOTER_H + 5 * mm  # anchored near bottom
    map_x = MARGIN_L
    map_w = CONTENT_W

    # Section title "Quartier"
    sec_y = PAGE_H - HEADER_H - 15 * mm
    _section_title(c, "Quartier", MARGIN_L, sec_y)

    # Quartier text - fills space between section title and map
    text_top = sec_y - 3 * mm
    text_bottom = map_y + map_h + 8 * mm
    available_h = text_top - text_bottom

    quartier_text = _clean_text(data.get("texte_quartier", ""))
    if quartier_text:
        style = ParagraphStyle(
            "quartier",
            fontName="Helvetica",
            fontSize=9,
            leading=13,
            textColor=GRAY_DARK,
            alignment=4,  # justified
        )
        para = Paragraph(quartier_text.replace("\n", "<br/>"), style)
        pw, ph = para.wrap(CONTENT_W, available_h)
        draw_y = text_top - ph
        if draw_y < text_bottom:
            draw_y = text_bottom
        para.drawOn(c, MARGIN_L, draw_y)

    # Section title "Localisation"
    loc_sec_y = map_y + map_h + 2 * mm
    _section_title(c, "Localisation", MARGIN_L, loc_sec_y)

    # OSM Map
    adresse = data.get("adresse", "")
    ville = data.get("ville", "")
    map_buf = _fetch_map_image(adresse, ville, out_w=840, out_h=400, zoom=16)
    if map_buf:
        try:
            c.drawImage(
                ImageReader(map_buf), map_x, map_y,
                width=map_w, height=map_h,
                preserveAspectRatio=False,
            )
        except Exception as exc:
            app.logger.error("Map draw error: %s", exc)
            _draw_map_placeholder(c, map_x, map_y, map_w, map_h)
    else:
        _draw_map_placeholder(c, map_x, map_y, map_w, map_h)

    # Address label under the map
    c.saveState()
    c.setFillColor(TEAL)
    c.setFont("Helvetica-Bold", 7)
    c.drawString(map_x, map_y - 4 * mm, "\u25a0  " + adresse + ", " + ville)
    c.restoreState()

    _build_footer(c, 2)


def _draw_map_placeholder(c, x, y, w, h):
    _draw_rounded_rect(c, x, y, w, h, fill_color=GRAY_LIGHT, stroke_color=GRAY_BORDER)
    c.saveState()
    c.setFillColor(GRAY_MID)
    c.setFont("Helvetica", 9)
    c.drawCentredString(x + w / 2, y + h / 2, "Carte indisponible")
    c.restoreState()


def _build_page3(c, data):
    """Page 3: Annonce & Donnees financieres."""
    sub = _safe(data.get("type_bien")) + " — " + _safe(data.get("adresse")) + ", " + _safe(data.get("ville"))
    _build_header(c, sub)

    y = PAGE_H - HEADER_H - 15 * mm

    # Section: Annonce
    _section_title(c, "Annonce", MARGIN_L, y)
    y -= 5 * mm

    # Clean description text
    desc_raw = data.get("description", "")
    desc_text = _clean_text(desc_raw)

    # Truncate to avoid overflow - max ~60 lines at 9pt
    max_annonce_h = 120 * mm
    if desc_text:
        style_ann = ParagraphStyle(
            "annonce",
            fontName="Helvetica",
            fontSize=8.5,
            leading=12,
            textColor=GRAY_DARK,
            alignment=4,
        )
        # Wrap to measure, then truncate if needed
        para = Paragraph(desc_text.replace("\n", "<br/>"), style_ann)
        pw, ph = para.wrap(CONTENT_W, max_annonce_h + 100 * mm)
        if ph > max_annonce_h:
            # Truncate text to fit
            lines = desc_text.split("\n")
            truncated = ""
            for line in lines:
                test = truncated + ("\n" if truncated else "") + line
                p_test = Paragraph(test.replace("\n", "<br/>"), style_ann)
                _, th = p_test.wrap(CONTENT_W, max_annonce_h + 100 * mm)
                if th > max_annonce_h:
                    truncated = truncated + "..."
                    break
                truncated = test
            para = Paragraph(truncated.replace("\n", "<br/>"), style_ann)
            pw, ph = para.wrap(CONTENT_W, max_annonce_h)

        para.drawOn(c, MARGIN_L, y - ph)
        y -= ph + 8 * mm
    else:
        y -= 5 * mm

    # Section: Donnees financieres
    _section_title(c, "Donnees financieres", MARGIN_L, y)
    y -= 5 * mm

    # Determine if location (rental) or vente (sale) data
    locataire = data.get("locataire")
    loyer_annuel = data.get("loyer_annuel_ht")

    fin_style_label = ParagraphStyle("fl", fontName="Helvetica", fontSize=8, textColor=GRAY_MID)
    fin_style_value = ParagraphStyle("fv", fontName="Helvetica-Bold", fontSize=9, textColor=GRAY_DARK)

    # Build financial data rows
    fin_rows = []
    if locataire or loyer_annuel:
        # Location mode
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
        row_h = 6 * mm
        box_h = len(fin_rows) * row_h + 6 * mm
        box_y = y - box_h
        _draw_rounded_rect(c, MARGIN_L, box_y, CONTENT_W, box_h, r=3, fill_color=GRAY_LIGHT, stroke_color=GRAY_BORDER, sw=0.5)

        ry = y - 4 * mm
        for label, value in fin_rows:
            c.saveState()
            c.setFillColor(GRAY_MID)
            c.setFont("Helvetica", 8)
            c.drawString(MARGIN_L + 5 * mm, ry, label)
            c.setFillColor(GRAY_DARK)
            c.setFont("Helvetica-Bold", 9)
            c.drawRightString(MARGIN_L + CONTENT_W - 5 * mm, ry, value)
            c.restoreState()
            ry -= row_h

        y = box_y - 8 * mm

    # Prix recap block
    prix_box_h = 28 * mm
    prix_box_y = y - prix_box_h
    _draw_rounded_rect(c, MARGIN_L, prix_box_y, CONTENT_W, prix_box_h, r=4, fill_color=TEAL)

    c.saveState()
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(MARGIN_L + CONTENT_W / 2, prix_box_y + 15 * mm, _fmt_price(data.get("prix")))
    c.setFont("Helvetica", 8)
    hono = data.get("honoraires")
    net = data.get("prix_net_vendeur")
    detail_parts = ["Prix de vente FAI"]
    if hono:
        detail_parts.append("Honoraires : " + _fmt_price(hono))
    if net:
        detail_parts.append("Net vendeur : " + _fmt_price(net))
    c.drawCentredString(MARGIN_L + CONTENT_W / 2, prix_box_y + 5 * mm, "  |  ".join(detail_parts))
    c.restoreState()

    _build_footer(c, 3)


# ---------------------------------------------------------------------------
# PDF Generation
# ---------------------------------------------------------------------------
def generate_dossier_pdf(data):
    """Generate the 3-page dossier PDF and return a BytesIO buffer."""
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=A4)
    ref = data.get("reference", "")
    c.setTitle("Dossier de Presentation — " + ref + " — Barbier Immobilier")
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
    return jsonify({"service": "Barbier PDF Generator", "status": "ok", "version": "5.0"})


@app.route("/generate-quartier", methods=["POST"])
def generate_quartier():
    """Generate neighbourhood text via GPT. Called by the cockpit before PDF generation."""
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
    """Generate and return a 3-page PDF dossier. Expects full payload from cockpit."""
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

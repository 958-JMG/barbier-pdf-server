#!/usr/bin/env python3
"""
Barbier Immobilier - PDF Dossier de Vente v5.16
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

# Uniform spacing constants (premium feel)
# Rhythm: Header → title(close) → content(very tight) → BIG BREATH → next title
SP_AFTER_HEADER = 9 * mm    # header bar → first section title
SP_BEFORE_FOOTER = 6 * mm   # last content → footer bar
SP_AFTER_SEC = 1.5 * mm     # section title bar bottom → content start (very tight)
SP_BETWEEN_BLOCS = 12 * mm  # content end → next section title (breathing room)
SEC_H = 7 * mm              # section title bar height

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


def _pdf_to_image(raw_bytes):
    """Convert a PDF's first page to a PIL Image using pymupdf (fitz)."""
    try:
        import fitz
        doc = fitz.open(stream=raw_bytes, filetype="pdf")
        page = doc[0]
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        img_data = pix.tobytes("png")
        doc.close()
        return ImageReader(io.BytesIO(img_data))
    except Exception as e:
        app.logger.error("PDF to image: %s", e)
        return None


def _fix_exif_orientation(pil_img):
    """Apply EXIF orientation tag and return corrected PIL image."""
    try:
        from PIL import ExifTags
        exif = pil_img._getexif()
        if exif:
            for tag, val in exif.items():
                if ExifTags.TAGS.get(tag) == "Orientation":
                    if val == 3:
                        pil_img = pil_img.rotate(180, expand=True)
                    elif val == 6:
                        pil_img = pil_img.rotate(270, expand=True)
                    elif val == 8:
                        pil_img = pil_img.rotate(90, expand=True)
                    break
    except Exception:
        pass
    return pil_img


def _bytes_to_image_reader(raw):
    """Convert raw image bytes to ImageReader, fixing EXIF orientation."""
    pil = Image.open(io.BytesIO(raw))
    pil = _fix_exif_orientation(pil)
    if pil.mode == "RGBA":
        bg = Image.new("RGB", pil.size, (255, 255, 255))
        bg.paste(pil, mask=pil.split()[3])
        pil = bg
    elif pil.mode != "RGB":
        pil = pil.convert("RGB")
    buf = io.BytesIO()
    pil.save(buf, format="JPEG", quality=92)
    buf.seek(0)
    return ImageReader(buf)


def _fetch_photo(url_or_data):
    if not url_or_data:
        return None
    try:
        s = str(url_or_data)
        if s.startswith("data:"):
            _, b = s.split(",", 1)
            raw = _b64.b64decode(b)
            # PDF? Convert to image
            if raw[:4] == b"%PDF" or s.startswith("data:application/pdf"):
                return _pdf_to_image(raw)
            return _bytes_to_image_reader(raw)
        resp = requests.get(s, timeout=15, headers={"User-Agent": "BarbierImmo/1.0"})
        if resp.status_code == 200:
            ct = resp.headers.get("Content-Type", "")
            if "pdf" in ct or resp.content[:4] == b"%PDF":
                return _pdf_to_image(resp.content)
            return _bytes_to_image_reader(resp.content)
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


GOOGLE_MAPS_KEY = os.environ.get("GOOGLE_MAPS_KEY", "AIzaSyBBmTUkFXvCLMZqfCk26o6axikt98SY058")

# Google Maps marker colors per POI category
_GM_MARKER_COLORS = {
    "Parking": "0x1B3A5C", "Transport": "0x0D5570", "Restauration": "0xE8472A",
    "Banque": "0x1B5C3A", "Formation": "0x5C3A1B", "Commerce": "0x3A1B5C",
    "Sante": "0x5C1B3A",
}
_GM_MARKER_LABELS = {
    "Parking": "P", "Transport": "T", "Restauration": "R",
    "Banque": "B", "Formation": "F", "Commerce": "C", "Sante": "S",
}


def _osm_map(adresse, ville, zoom=16, tiles=3):
    """Returns (PIL Image, lat, lon) or (None, None, None). Fallback if Google fails."""
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
    """Fetch POI via Overpass. Returns list of (category, name, color_hex, poi_lat, poi_lon)."""
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
                '[out:json][timeout:10];(node["' + key + '"~' + val_filter
                + '](around:' + str(radius) + ',' + str(lat) + ',' + str(lon)
                + '););out 3;'
            )
            enc = urllib.parse.quote(query)
            resp = requests.get(
                "https://overpass-api.de/api/interpreter?data=" + enc,
                headers={"User-Agent": "BarbierImmo/1.0"}, timeout=12)
            if resp.status_code != 200:
                continue
            elements = resp.json().get("elements", [])
            for el in elements:
                nom = el.get("tags", {}).get("name", "")
                plat = el.get("lat")
                plon = el.get("lon")
                if nom and plat and plon:
                    results.append((label, nom[:28], color, float(plat), float(plon)))
                    break  # one per category
            if len(results) >= 6:
                break
    except Exception as e:
        app.logger.error("POI fetch: %s", e)
    return results


def _google_static_map(adresse, ville, poi_list, w=640, h=400, zoom=16):
    """Generate a Google Maps Static API image with POI markers.
    poi_list: [(category, name, color_hex, lat, lon), ...]
    Returns (PIL Image, lat, lon) or falls back to OSM.
    """
    if not GOOGLE_MAPS_KEY:
        return None, None, None
    try:
        lat, lon = _geocode(adresse, ville)
        if lat is None:
            return None, None, None

        import urllib.parse
        params = {
            "center": "{},{}".format(lat, lon),
            "zoom": str(zoom),
            "size": "{}x{}".format(w, h),
            "scale": "2",
            "maptype": "roadmap",
            "key": GOOGLE_MAPS_KEY,
        }
        # Main marker (bien location) — orange
        markers = ["color:0xF0795B|size:mid|label:X|{},{}".format(lat, lon)]
        # POI markers
        for cat, nom, col_hex, plat, plon in (poi_list or [])[:6]:
            mc = _GM_MARKER_COLORS.get(cat, "0x16708B")
            ml = _GM_MARKER_LABELS.get(cat, "")
            markers.append("color:{}|size:small|label:{}|{},{}".format(mc, ml, plat, plon))

        url = "https://maps.googleapis.com/maps/api/staticmap?" + urllib.parse.urlencode(params)
        for m in markers:
            url += "&markers=" + urllib.parse.quote(m)

        app.logger.info("Google Maps URL: %s", url[:200])
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200 and "image" in resp.headers.get("Content-Type", ""):
            img = Image.open(io.BytesIO(resp.content)).convert("RGB")
            return img, lat, lon
        else:
            app.logger.warning("Google Maps failed (%s): %s", resp.status_code, resp.text[:200])
            return None, None, None
    except Exception as e:
        app.logger.error("Google Static Map: %s", e)
        return None, None, None


def _get_poi_gpt(adresse, ville, type_bien):
    """Fallback: ask GPT for nearby POI when Overpass fails."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return []
    prompt = (
        "Tu es expert en immobilier commercial dans le Morbihan."
        " Pour : " + (type_bien or "local") + " au " + (adresse or "") + ", " + (ville or "Vannes") + ","
        " liste les points d'interet REELS certains dans un rayon de 500m."
        " Reponds UNIQUEMENT en JSON (sans backticks) :"
        ' [{"categorie":"Parking","nom":"Nom exact"}]'
        " Categories : Parking, Transport, Restauration, Commerce, Banque, Sante."
        " Maximum 5 elements. N'inclus QUE ce dont tu es certain."
    )
    cat_colors = {"Parking": "#1B3A5C", "Transport": "#0D5570", "Restauration": "#E8472A",
                  "Commerce": "#3A1B5C", "Banque": "#1B5C3A", "Sante": "#5C1B3A",
                  "Formation": "#5C3A1B"}
    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 300, "temperature": 0.1},
            timeout=20)
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        match = re.search(r"\[.*?\]", raw, re.DOTALL)
        if match:
            items = json.loads(match.group(0))
            results = []
            for it in items[:5]:
                cat = it.get("categorie", "")
                nom = it.get("nom", "")
                if cat and nom:
                    results.append((cat, nom[:28], cat_colors.get(cat, "#16708B"), 0, 0))
            return results
    except Exception as e:
        app.logger.error("POI GPT fallback: %s", e)
    return []


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
    c.drawRightString(PAGE_W - MR, 3.5 * mm, "v5.16  " + str(n) + " / " + str(total))
    c.restoreState()


def _sec(c, text, x, y, w=None):
    sw = w if w is not None else CW
    c.setFillColor(colors.HexColor("#EBF0F8"))
    c.rect(x, y, sw, SEC_H, fill=1, stroke=0)
    c.setFillColor(ORANGE)
    c.rect(x, y, 3.5 * mm, SEC_H, fill=1, stroke=0)
    c.setFillColor(TEAL_DARK)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(x + 8 * mm, y + 2 * mm, text)


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


def _is_portrait(img):
    """Check if an image is portrait (taller than wide)."""
    try:
        iw, ih = img.getSize()
        return ih > iw * 1.15  # at least 15% taller than wide
    except Exception:
        return False


def _draw_photo_fit(c, img, x, y, w, h):
    """Draw image fitted inside rect (no crop), with light gray background.
    Used for portrait photos so they don't get distorted."""
    try:
        iw, ih = img.getSize()
        ir = iw / ih if ih > 0 else 1
        tr = w / h
        if ir > tr:
            dw = w
            dh = w / ir
        else:
            dh = h
            dw = h * ir
        dx = x + (w - dw) / 2
        dy = y + (h - dh) / 2
        # Background
        c.saveState()
        clip = c.beginPath()
        clip.roundRect(x, y, w, h, 3 * mm)
        c.clipPath(clip, stroke=0, fill=0)
        c.setFillColor(colors.HexColor("#F0F2F5"))
        c.rect(x, y, w, h, fill=1, stroke=0)
        c.drawImage(img, dx, dy, dw, dh, mask="auto")
        c.restoreState()
        # Border
        c.setStrokeColor(GRAY_BDR)
        c.setLineWidth(0.4)
        c.roundRect(x, y, w, h, 3 * mm, fill=0, stroke=1)
    except Exception as e:
        app.logger.error("draw_photo_fit: %s", e)


def _draw_poi_icon(c, cat, cx, cy, r):
    """Draw a small vector icon inside a colored circle (already drawn by caller)."""
    c.setFillColor(WHITE)
    c.setStrokeColor(WHITE)
    cu = cat.upper()
    lw = max(r * 0.18, 0.8)
    c.setLineWidth(lw)
    if "PARKING" in cu:
        # Bold P — parking standard symbol
        c.setFont("Helvetica-Bold", r * 1.6)
        c.drawCentredString(cx, cy - r * 0.52, "P")
    elif "TRANSPORT" in cu:
        # Simple bus: rectangle body + two wheels
        bw, bh = r * 1.1, r * 0.85
        c.roundRect(cx - bw / 2, cy - bh * 0.55, bw, bh, r * 0.18, fill=1, stroke=0)
        c.setFillColor(colors.HexColor("#00000040"))
        wr = r * 0.22
        c.circle(cx - bw * 0.28, cy - bh * 0.55 - wr * 0.1, wr, fill=1, stroke=0)
        c.circle(cx + bw * 0.28, cy - bh * 0.55 - wr * 0.1, wr, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.rect(cx - bw * 0.4, cy, bw * 0.8, bh * 0.26, fill=1, stroke=0)
    elif "RESTAURATION" in cu:
        # Fork + knife shape (two vertical lines with serifs)
        fw = r * 0.18
        fh = r * 1.1
        c.setLineWidth(fw)
        # Fork (left) - line with 3 small horizontal ticks at top
        fx = cx - r * 0.3
        c.line(fx, cy - fh * 0.5, fx, cy + fh * 0.5)
        for k in [0.5, 0.28, 0.06]:
            c.line(fx - r * 0.22, cy + fh * k, fx + r * 0.22, cy + fh * k)
        # Knife (right) - line with diagonal cut top
        kx = cx + r * 0.3
        c.line(kx, cy - fh * 0.5, kx, cy + fh * 0.5)
        p = c.beginPath()
        p.moveTo(kx, cy + fh * 0.5)
        p.lineTo(kx + r * 0.35, cy + fh * 0.05)
        p.lineTo(kx, cy + fh * 0.05)
        p.close()
        c.drawPath(p, fill=1, stroke=0)
    elif "COMMERCE" in cu:
        # Shopping bag
        bw, bh = r * 1.0, r * 0.9
        bx, by = cx - bw / 2, cy - bh * 0.6
        c.roundRect(bx, by, bw, bh, r * 0.15, fill=0, stroke=1)
        # Handle
        c.setLineWidth(lw * 1.2)
        hw = bw * 0.5
        c.arc(cx - hw / 2, cy + bh * 0.3, cx + hw / 2, cy + bh * 0.7, 0, 180)
    elif "BANQUE" in cu:
        # ‚Ç¨ symbol
        c.setFont("Helvetica-Bold", r * 1.5)
        c.drawCentredString(cx + r * 0.05, cy - r * 0.52, "\u20ac")
    elif "SANTE" in cu:
        # Medical cross (+ shape)
        arm = r * 0.65
        thick = r * 0.32
        c.rect(cx - thick / 2, cy - arm, thick, arm * 2, fill=1, stroke=0)
        c.rect(cx - arm, cy - thick / 2, arm * 2, thick, fill=1, stroke=0)
    elif "FORMATION" in cu:
        # Book: rectangle with spine line
        bw, bh = r * 1.0, r * 0.85
        c.roundRect(cx - bw / 2, cy - bh / 2, bw, bh, r * 0.1, fill=0, stroke=1)
        c.setLineWidth(lw * 1.3)
        c.line(cx, cy - bh / 2, cx, cy + bh / 2)
    else:
        # Generic location pin dot
        c.circle(cx, cy, r * 0.35, fill=1, stroke=0)


def _draw_poi_card(c, bx, by, bw, bh, label, valeur, color_hex):
    """POI card: white rounded rect, colored circle icon, category + name."""
    col = colors.HexColor(color_hex) if color_hex else TEAL
    # White card background with subtle border
    c.setFillColor(WHITE)
    c.setStrokeColor(colors.HexColor("#E0E4EA"))
    c.setLineWidth(0.5)
    c.roundRect(bx, by, bw, bh, 2 * mm, fill=1, stroke=1)
    # Icon circle — large, centered vertically
    r = min(bh * 0.32, 5.5 * mm)
    icx = bx + r + 4 * mm
    icy = by + bh / 2
    c.saveState()
    c.setFillColor(col)
    c.circle(icx, icy, r, fill=1, stroke=0)
    _draw_poi_icon(c, label, icx, icy, r)
    c.restoreState()
    # Text: category label small gray, then POI name bold dark
    tx = icx + r + 4 * mm
    avail_w = bx + bw - tx - 3 * mm
    c.saveState()
    c.setFillColor(GRAY_MID)
    c.setFont("Helvetica", 6)
    cat_txt = label.upper()
    while cat_txt and c.stringWidth(cat_txt, "Helvetica", 6) > avail_w:
        cat_txt = cat_txt[:-1]
    c.drawString(tx, by + bh - 4.5 * mm, cat_txt)
    c.setFillColor(TEAL_DARK)
    nom = str(valeur)
    for fsz in [9, 8, 7.5, 7]:
        c.setFont("Helvetica-Bold", fsz)
        if c.stringWidth(nom, "Helvetica-Bold", fsz) <= avail_w:
            break
    else:
        while nom and c.stringWidth(nom + "\u2026", "Helvetica-Bold", 7) > avail_w:
            nom = nom[:-1]
        nom = nom + "\u2026"
    c.drawString(tx, by + 3.5 * mm, nom)
    c.restoreState()


# ---------------------------------------------------------------------------
# PAGE 1 — Couverture
# ---------------------------------------------------------------------------
def _page1(c, d):
    # 1) Draw both backgrounds
    c.setFillColor(TEAL)
    c.rect(0, PAGE_H * 0.50, PAGE_W, PAGE_H * 0.50, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.rect(0, 0, PAGE_W, PAGE_H * 0.50, fill=1, stroke=0)

    # 2) Draw photo FIRST (below pills z-order)
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

    # 3) Now draw teal zone content (on top of everything)
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

    # Pills: Surface, Type, Activite — drawn AFTER both backgrounds
    pills = [
        ("SURFACE", _safe(d.get("surface")) + " m\u00b2"),
        ("TYPE", _safe(d.get("type_bien"))),
    ]
    if d.get("activite"):
        pills.append(("ACTIVITE", _safe(d.get("activite"))))
    else:
        pills.append(("ETAT", _safe(d.get("etat_bien"), "---")))
    pills = pills[:4]

    npills = len(pills)
    pg = 2 * mm
    pw = (CW - (npills - 1) * pg) / npills
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

    # -- 1) "Pourquoi Barbier Immobilier" at TOP
    pourquoi_h = 26 * mm
    pq_top = PAGE_H - HEADER_H - SP_AFTER_HEADER
    pq_bot = pq_top - pourquoi_h
    c.setFillColor(TEAL)
    c.roundRect(ML, pq_bot, CW, pourquoi_h, 2 * mm, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(ML + 5 * mm, pq_top - 5 * mm, "Pourquoi Barbier Immobilier ?")
    c.setFont("Helvetica", 7.5)
    lines_pq = [
        "Plus de 30 ans d'expertise en immobilier commercial dans le Morbihan.",
        "Un accompagnement personnalis\u00e9 pour chaque projet d'investissement.",
        "Une connaissance approfondie du tissu \u00e9conomique local et des opportunit\u00e9s.",
    ]
    for i_pq, lpq in enumerate(lines_pq):
        c.drawString(ML + 5 * mm, pq_top - 13 * mm - i_pq * 5 * mm,
                     "\u2022  " + lpq)

    # -- 2) "Le quartier" section — SP_BETWEEN_BLOCS gap
    ville = _safe(d.get("ville"), "Vannes")
    tb = d.get("type_bien") or ""
    quartier_sec_y = pq_bot - SP_BETWEEN_BLOCS
    _sec(c, "Le quartier", ML, quartier_sec_y)

    if tb and tb != "\u2014":
        chapeau = "Un emplacement strat\u00e9gique pour votre " + tb.lower() + " au c\u0153ur de " + ville + "."
    else:
        chapeau = "Un emplacement strat\u00e9gique au c\u0153ur de " + ville + "."

    chapeau_y = quartier_sec_y - SEC_H - SP_AFTER_SEC
    c.setFillColor(ORANGE)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(ML, chapeau_y, chapeau)

    # Quartier text
    texte = d.get("texte_quartier") or (
        "Situ\u00e9 a " + ville + ", ce bien b\u00e9n\u00e9ficie d'une localisation strat\u00e9gique "
        "dans un secteur \u00e9conomiquement actif du Morbihan. L'accessibilit\u00e9 est optimale gr\u00e2ce a la "
        "proximit\u00e9 de la rocade et des axes principaux. Le secteur compte de nombreux commerces, "
        "services et \u00e9quipements a proximit\u00e9 imm\u00e9diate, offrant un environnement favorable a "
        "l'exploitation d'une activit\u00e9 commerciale ou professionnelle."
    )

    parts = re.split(r"(?<=[.!?])\s+", texte.strip(), maxsplit=1)
    if len(parts) == 2:
        p1 = parts[0].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        p2 = parts[1].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        texte_xml = "<b>" + p1 + "</b> " + p2
    else:
        texte_xml = texte.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    text_top = chapeau_y - 5 * mm
    # Reserve: map zone (min 75mm) + section header (10mm) + footer
    map_min_h = 75 * mm
    bottom_reserved = FOOTER_H + SP_BEFORE_FOOTER + map_min_h + SP_BETWEEN_BLOCS + SEC_H
    max_text_h = text_top - bottom_reserved
    if max_text_h < 8 * mm:
        max_text_h = 8 * mm

    sty = ParagraphStyle("qt", fontName="Helvetica", fontSize=9,
                         textColor=GRAY_DARK, leading=14, alignment=4)
    para = Paragraph(texte_xml, sty)
    _, ph = para.wrap(CW, max_text_h)
    if ph > max_text_h:
        for fsz in [8.5, 8, 7.5, 7]:
            sty2 = ParagraphStyle("qt" + str(fsz), fontName="Helvetica", fontSize=fsz,
                                  textColor=GRAY_DARK, leading=fsz * 1.55, alignment=4)
            para = Paragraph(texte_xml, sty2)
            _, ph = para.wrap(CW, max_text_h)
            if ph <= max_text_h:
                break
    text_draw_y = text_top - ph
    para.drawOn(c, ML, text_draw_y)

    # -- 3) Localisation & Environnement — single full-width map with POI markers
    zone_bot = FOOTER_H + SP_BEFORE_FOOTER
    sec2_y = text_draw_y - SP_BETWEEN_BLOCS
    zone_h = sec2_y - SEC_H - zone_bot
    if zone_h < 40 * mm:
        zone_h = 40 * mm
    zone_top = zone_bot + zone_h

    _sec(c, "Localisation & environnement", ML, zone_top + 1 * mm)

    mx = ML
    mw = CW
    mh = zone_h
    my = zone_bot
    lat = lon = None

    # 1) Fetch POI (with coordinates)
    adresse_str = _safe(d.get("adresse"), "")
    ville_str = _safe(d.get("ville"), "Vannes")
    lat, lon = _geocode(adresse_str, ville_str)
    poi_blocks = []
    if lat and lon:
        poi_blocks = _get_poi_osm(lat, lon, radius=500)

    # 2) Try Google Maps Static (full width, all POI as markers)
    map_img = None
    if GOOGLE_MAPS_KEY:
        try:
            gmap_img, glat, glon = _google_static_map(
                adresse_str, ville_str, poi_blocks, w=640, h=380, zoom=16)
            if gmap_img:
                map_img = gmap_img
                if glat:
                    lat, lon = glat, glon
        except Exception as e:
            app.logger.error("Google map attempt: %s", e)

    # 3) Fallback to OSM tiles if Google failed
    if map_img is None:
        try:
            osm_img, olat, olon = _osm_map(adresse_str, ville_str)
            if osm_img:
                map_img = osm_img
                if olat:
                    lat, lon = olat, olon
        except Exception as e:
            app.logger.error("OSM fallback: %s", e)

    # 4) Draw the map
    if map_img:
        try:
            iw2, ih2 = map_img.size
            tr = mw / mh
            ir = iw2 / ih2
            if ir > tr:
                nw = int(ih2 * tr)
                map_img = map_img.crop(((iw2 - nw) // 2, 0, (iw2 - nw) // 2 + nw, ih2))
            else:
                nh = int(iw2 / tr)
                map_img = map_img.crop((0, (ih2 - nh) // 2, iw2, (ih2 - nh) // 2 + nh))
            buf2 = io.BytesIO()
            map_img.save(buf2, format="PNG")
            buf2.seek(0)
            c.saveState()
            clip = c.beginPath()
            clip.roundRect(mx, my, mw, mh, 3 * mm)
            c.clipPath(clip, stroke=0, fill=0)
            c.drawImage(ImageReader(buf2), mx, my, width=mw, height=mh)
            c.restoreState()
            # Subtle border
            c.setStrokeColor(colors.HexColor("#BBBBBB"))
            c.setLineWidth(0.6)
            c.roundRect(mx, my, mw, mh, 3 * mm, fill=0, stroke=1)
            # Address chip centered at bottom of map
            adr = adresse_str + ", " + ville_str
            chip_w = min(c.stringWidth(adr, "Helvetica-Bold", 7) + 10 * mm, mw - 20 * mm)
            chip_h = 8 * mm
            chip_x = mx + (mw - chip_w) / 2
            chip_y = my + 6 * mm
            c.setFillColor(WHITE)
            c.setStrokeColor(colors.HexColor("#CCCCCC"))
            c.setLineWidth(0.4)
            c.roundRect(chip_x, chip_y, chip_w, chip_h, 2 * mm, fill=1, stroke=1)
            c.setFillColor(TEAL_DARK)
            c.setFont("Helvetica-Bold", 7)
            c.drawCentredString(mx + mw / 2, chip_y + 2.5 * mm, adr[:65])
        except Exception as e:
            app.logger.error("Map draw: %s", e)
    else:
        c.setFillColor(colors.HexColor("#E8F0F4"))
        c.roundRect(mx, my, mw, mh, 3 * mm, fill=1, stroke=0)
        c.setFillColor(colors.HexColor("#AAAAAA"))
        c.setFont("Helvetica", 8)
        c.drawCentredString(mx + mw / 2, my + mh / 2, "Carte indisponible")

    # 5) POI legend below map (compact row of labels if we have POI)
    if poi_blocks:
        legend_y = my - 5 * mm
        c.setFont("Helvetica", 6)
        lx = ML
        for i_poi, (cat, nom, col_hex, *_coords) in enumerate(poi_blocks[:6]):
            col = colors.HexColor(col_hex) if col_hex else TEAL
            # Colored dot + text
            c.setFillColor(col)
            c.circle(lx + 2 * mm, legend_y + 1.5 * mm, 1.5 * mm, fill=1, stroke=0)
            c.setFillColor(GRAY_DARK)
            label_txt = cat[0] + " " + nom
            c.drawString(lx + 5 * mm, legend_y, label_txt[:25])
            lx += c.stringWidth(label_txt[:25], "Helvetica", 6) + 9 * mm
            if lx > ML + CW - 20 * mm:
                break

    _footer(c, 2)


# ---------------------------------------------------------------------------
# PAGE 3 — Annonce + Donnees financieres + Prix
# ---------------------------------------------------------------------------
def _page3(c, d):
    _header(c, _safe(d.get("type_bien")) + " \u2014 " + _safe(d.get("adresse")) + ", " + _safe(d.get("ville")))

    # ── Gather data ──
    loc = d.get("locataire") or ""
    lht = d.get("loyer_annuel_ht") or 0
    linit = d.get("loyer_initial_ht") or 0
    evol = d.get("evolution_loyer") or ""
    duree = d.get("duree_bail") or ""
    taxe = d.get("taxe_fonciere") or 0
    is_bail = bool(loc or lht or linit or evol or duree)

    prix_brut = d.get("prix") or 0
    if not prix_brut:
        pnv = d.get("prix_net_vendeur") or 0
        hnr = d.get("honoraires") or 0
        if pnv and hnr:
            try:
                prix_brut = int(float(str(pnv))) + int(float(str(hnr)))
            except Exception:
                pass
    has_prix = bool(prix_brut)

    pills_data = [
        (PICTO_SURFACE_B64, "Surface", _safe(d.get("surface")) + " m\u00b2"),
        (PICTO_TYPE_B64, "Type de bien", _safe(d.get("type_bien"))),
        (PICTO_LIEU_B64, "Adresse", _safe(d.get("adresse"))),
        (PICTO_VILLE_B64, "Ville", _safe(d.get("ville"))),
    ]
    if d.get("activite"):
        pills_data.append((PICTO_TYPE_B64, "Activite", _safe(d.get("activite"))))
    n_pill_rows = math.ceil(len(pills_data) / 3)
    pw2 = 57 * mm; ph2 = 16 * mm; pgy = 3 * mm
    pills_total_h = n_pill_rows * ph2 + (n_pill_rows - 1) * pgy

    bail_rows = []
    if is_bail:
        if loc:    bail_rows.append(("Locataire", loc))
        if lht:    bail_rows.append(("Loyer annuel HT", _pfmt(lht) + " HT/an"))
        if linit:  bail_rows.append(("Loyer initial", _pfmt(linit) + " HT"))
        if evol:   bail_rows.append(("Evolution loyer", str(evol)))
        if duree:  bail_rows.append(("Dur\u00e9e du bail", str(duree)))
        if taxe:   bail_rows.append(("Taxe fonci\u00e8re", _pfmt(taxe) + "/an"))
    elif taxe:
        bail_rows.append(("Taxe fonci\u00e8re", _pfmt(taxe) + "/an"))
    row_h_bail = 12 * mm
    bail_bloc_h = (math.ceil(len(bail_rows) / 2) * row_h_bail + 4 * mm) if bail_rows else 0

    prix_block_h = 28 * mm if has_prix else 0

    # ── PRE-COMPUTE: what desc can use ──
    # Everything below desc is: pills + bail + prix, each with (SP_BETWEEN_BLOCS + SEC_H + SP_AFTER_SEC) overhead
    bottom_used = FOOTER_H + SP_BEFORE_FOOTER
    if has_prix:
        bottom_used += prix_block_h + SP_BETWEEN_BLOCS + SEC_H + SP_AFTER_SEC
    if bail_rows:
        bottom_used += bail_bloc_h + SP_BETWEEN_BLOCS + SEC_H + SP_AFTER_SEC
    bottom_used += pills_total_h + SP_BETWEEN_BLOCS + SEC_H + SP_AFTER_SEC

    # ── BLOC 1: Présentation du bien ──
    cursor = PAGE_H - HEADER_H - SP_AFTER_HEADER
    _sec(c, "Pr\u00e9sentation du bien", ML, cursor)
    cursor -= SEC_H + SP_AFTER_SEC

    desc = _clean(d.get("description", ""))
    desc_lines = desc.split("\n")
    titre_annonce = ""
    desc_body = desc
    if desc_lines:
        titre_annonce = desc_lines[0].strip()
        if len(titre_annonce) < 120:
            desc_body = "\n".join(desc_lines[1:]).strip()
        else:
            titre_annonce = _safe(d.get("type_bien"), "Bien immobilier")
            desc_body = desc

    sty_titre = ParagraphStyle("pt", fontName="Helvetica-Bold", fontSize=11,
                                textColor=TEAL_DARK, leading=14)
    p_titre = Paragraph(titre_annonce.replace("&", "&amp;").replace("<", "&lt;"), sty_titre)
    _, th = p_titre.wrap(CW, 30 * mm)
    cursor -= th
    p_titre.drawOn(c, ML, cursor)
    cursor -= 2 * mm

    desc_stop_y = bottom_used + 2 * mm
    desc_bot = _render_desc(c, desc_body, cursor, 999, stop_y=desc_stop_y)
    cursor = desc_bot

    # ── BLOC 2: Caractéristiques ──
    cursor -= SP_BETWEEN_BLOCS
    _sec(c, "Caract\u00e9ristiques", ML, cursor)
    cursor -= SEC_H + SP_AFTER_SEC
    pgx = 3 * mm
    cols = 3
    pill_y = cursor - ph2
    for i, (b64, lbl, val) in enumerate(pills_data):
        col_i = i % cols
        row_i = i // cols
        _pill(c, ML + col_i * (pw2 + pgx), pill_y - row_i * (ph2 + pgy), b64, lbl, val, pw2, ph2)
    cursor = pill_y - (n_pill_rows - 1) * (ph2 + pgy)

    # ── BLOC 3: Données du bail ──
    if bail_rows:
        cursor -= SP_BETWEEN_BLOCS
        bail_label = "Donn\u00e9es du bail" if is_bail else "Donn\u00e9es financi\u00e8res"
        _sec(c, bail_label, ML, cursor)
        cursor -= SEC_H + SP_AFTER_SEC
        # Background aplat
        c.setFillColor(colors.HexColor("#EBF0F8"))
        c.roundRect(ML, cursor - bail_bloc_h, CW, bail_bloc_h, 2 * mm, fill=1, stroke=0)
        cols2 = 2
        col_bail_w = (CW - 4 * mm) / cols2
        for idx2, (label, valeur) in enumerate(bail_rows):
            col2 = idx2 % cols2
            row2 = idx2 // cols2
            cx2 = ML + 6 * mm + col2 * (col_bail_w + 4 * mm)
            cy2 = cursor - 4 * mm - row2 * row_h_bail
            c.setFillColor(GRAY_MID)
            c.setFont("Helvetica", 6.5)
            c.drawString(cx2, cy2, label.upper())
            c.setStrokeColor(colors.HexColor("#C0CBD8"))
            c.setLineWidth(0.4)
            c.line(cx2, cy2 - 1 * mm, cx2 + col_bail_w - 6 * mm, cy2 - 1 * mm)
            c.setFillColor(TEAL_DARK)
            c.setFont("Helvetica-Bold", 9)
            c.drawString(cx2, cy2 - 6.5 * mm, str(valeur))
        cursor -= bail_bloc_h

    # ── BLOC 4: Prix ──
    if has_prix:
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

            cursor -= SP_BETWEEN_BLOCS
            _sec(c, "Prix", ML, cursor)
            cursor -= SEC_H + SP_AFTER_SEC
            bw3 = CW / 3 - 2 * mm
            hcharge = d.get("honoraires_charge") or "Acqu\u00e9reur"
            bloc_y = cursor - prix_block_h
            items = [
                ("PRIX DE VENTE FAI", _pfmt(prix_fai), TEAL),
                ("HONORAIRES (" + str(hcharge)[:12] + ")", _pfmt(hono_v), ORANGE),
                ("PRIX NET VENDEUR", _pfmt(pnv_v), TEAL_DARK),
            ]
            for ip, (lbl, val, col) in enumerate(items):
                bxp = ML + ip * (bw3 + 3 * mm)
                c.setFillColor(col)
                c.roundRect(bxp, bloc_y, bw3, prix_block_h, 2.5 * mm, fill=1, stroke=0)
                c.setFillColor(WHITE)
                c.setFont("Helvetica", 6)
                c.drawString(bxp + 4 * mm, bloc_y + prix_block_h - 8 * mm, lbl)
                c.setStrokeColor(colors.HexColor("#FFFFFF55"))
                c.setLineWidth(0.5)
                c.line(bxp + 4 * mm, bloc_y + prix_block_h - 10 * mm, bxp + bw3 - 4 * mm, bloc_y + prix_block_h - 10 * mm)
                c.setFont("Helvetica-Bold", 13)
                c.drawString(bxp + 4 * mm, bloc_y + 6 * mm, val)
        except Exception as e:
            app.logger.error("Prix block: %s", e)

    _footer(c, 3)


def _render_desc(c, desc_txt, start_y, max_h, stop_y=None):
    """Render description with editorial formatting. Returns y of bottom."""
    if not desc_txt:
        return start_y
    if stop_y is None:
        stop_y = 18 * mm
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
                if y - ph < stop_y:
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
                    if y - ph < stop_y:
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
                    if y - ph < stop_y:
                        break
                    y -= ph
                    p.drawOn(c, x, y)
                    y -= gap
    else:
        blocs = [b.strip() for b in desc_txt.split("\n\n") if b.strip()]
        for idx, bloc in enumerate(blocs):
            if y < stop_y or idx >= 4:
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
                    if y - ph < stop_y:
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
                    if y - ph < stop_y:
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
                if y - bh < stop_y:
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
                    if y - ph < stop_y:
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
                if y - ph < stop_y:
                    break
                y -= ph
                p.drawOn(c, x, y)
                y -= gap
    return y


def _is_plan(url_or_data):
    """Check if a photo URL/data is a cadastre/PDF document."""
    s = str(url_or_data or "")
    return ("cadastr" in s.lower() or s.startswith("data:application/pdf")
            or (s.startswith("data:") and "pdf" in s.lower()[:40]))


# ---------------------------------------------------------------------------
# PAGE 4 — Photos du bien (images only, no cadastre)
# ---------------------------------------------------------------------------
def _page_photos(c, d, page_num=4, total=4):
    _header(c, _safe(d.get("type_bien")) + " \u2014 " + _safe(d.get("adresse")) + ", " + _safe(d.get("ville")))
    sec_y = PAGE_H - HEADER_H - SP_AFTER_HEADER
    _sec(c, "Photos du bien", ML, sec_y)

    photos = d.get("photos") or []
    # Filter: skip first (cover) + skip PDFs/cadastre
    real_photos = []
    for i, p in enumerate(photos):
        if i == 0:
            continue
        if not _is_plan(p):
            real_photos.append(p)

    zone_top = sec_y - SEC_H - SP_AFTER_SEC
    zone_bot = FOOTER_H + SP_BEFORE_FOOTER
    available_h = zone_top - zone_bot
    gap_y = 4 * mm

    if not real_photos:
        c.setFillColor(GRAY_LIGHT)
        c.roundRect(ML, zone_bot, CW, available_h, 3 * mm, fill=1, stroke=0)
        c.setFillColor(GRAY_MID)
        c.setFont("Helvetica", 10)
        c.drawCentredString(ML + CW / 2, zone_bot + available_h / 2, "Aucune photo suppl\u00e9mentaire")
        _footer(c, page_num, total=total)
        return

    # Full width, stacked, same height — 3 photos minimum (landscape format)
    n = min(len(real_photos), 3)
    ph_each = (available_h - (n - 1) * gap_y) / n

    for i in range(n):
        img = _fetch_photo(real_photos[i])
        py = zone_top - (i + 1) * ph_each - i * gap_y
        if img:
            if _is_portrait(img):
                _draw_photo_fit(c, img, ML, py, CW, ph_each)
            else:
                _draw_cover(c, img, ML, py, CW, ph_each)
        else:
            c.setFillColor(GRAY_LIGHT)
            c.setStrokeColor(GRAY_BDR)
            c.setLineWidth(0.5)
            c.roundRect(ML, py, CW, ph_each, 3 * mm, fill=1, stroke=1)
            c.setFillColor(GRAY_MID)
            c.setFont("Helvetica", 9)
            c.drawCentredString(ML + CW / 2, py + ph_each / 2, "Photo " + str(i + 2))

    _footer(c, page_num, total=total)


# ---------------------------------------------------------------------------
# PAGE 5 — Plan cadastral & informations parcelle
# ---------------------------------------------------------------------------
def _page_cadastre(c, d, page_num=5, total=5):
    _header(c, "Plan cadastral \u2014 " + _safe(d.get("adresse")) + ", " + _safe(d.get("ville")))
    sec_y = PAGE_H - HEADER_H - SP_AFTER_HEADER
    _sec(c, "Plan cadastral", ML, sec_y)

    photos = d.get("photos") or []
    # Find cadastre images (PDFs)
    cadastre_imgs = []
    for p in photos:
        if _is_plan(p):
            img = _fetch_photo(p)
            if img:
                cadastre_imgs.append(img)

    zone_top = sec_y - SEC_H - SP_AFTER_SEC
    zone_bot = FOOTER_H + 20 * mm  # extra space for parcel info
    available_h = zone_top - zone_bot
    gap_y = 4 * mm

    if not cadastre_imgs:
        c.setFillColor(GRAY_LIGHT)
        c.roundRect(ML, zone_bot, CW, available_h, 3 * mm, fill=1, stroke=0)
        c.setFillColor(GRAY_MID)
        c.setFont("Helvetica", 10)
        c.drawCentredString(ML + CW / 2, zone_bot + available_h / 2, "Plan cadastral non disponible")
        _footer(c, page_num, total=total)
        return

    # Display cadastre images — full width, stacked
    n = min(len(cadastre_imgs), 2)
    ph_each = (available_h - (n - 1) * gap_y) / n

    for i in range(n):
        py = zone_top - (i + 1) * ph_each - i * gap_y
        # For cadastre, draw with white background + border (no cover-crop)
        img = cadastre_imgs[i]
        try:
            iw, ih = img.getSize()
            ir_ratio = iw / ih if ih > 0 else 1
            target_r = CW / ph_each
            if ir_ratio > target_r:
                dw = CW
                dh = CW / ir_ratio
            else:
                dh = ph_each
                dw = ph_each * ir_ratio
            dx = ML + (CW - dw) / 2
            dy = py + (ph_each - dh) / 2
            # White background
            c.setFillColor(WHITE)
            c.setStrokeColor(colors.HexColor("#CCCCCC"))
            c.setLineWidth(0.5)
            c.roundRect(ML, py, CW, ph_each, 3 * mm, fill=1, stroke=1)
            c.drawImage(img, dx, dy, width=dw, height=dh, mask="auto")
        except Exception as e:
            app.logger.error("Cadastre draw: %s", e)
            c.setFillColor(GRAY_LIGHT)
            c.roundRect(ML, py, CW, ph_each, 3 * mm, fill=1, stroke=0)

    # Parcel info at bottom
    ref_cad = d.get("reference_cadastrale") or ""
    parcelle = d.get("parcelle") or ""
    section = d.get("section_cadastrale") or ""
    surface_terrain = d.get("surface_terrain") or ""
    if ref_cad or parcelle or section or surface_terrain:
        info_y = zone_bot - 14 * mm
        _sec(c, "Informations parcelle", ML, info_y + 2 * mm)
        c.setFillColor(colors.HexColor("#EBF0F8"))
        c.roundRect(ML, info_y - 12 * mm, CW, 12 * mm, 1.5 * mm, fill=1, stroke=0)
        ix = ML + 5 * mm
        c.setFillColor(GRAY_MID)
        c.setFont("Helvetica", 6.5)
        c.setFillColor(TEAL_DARK)
        c.setFont("Helvetica-Bold", 8)
        infos = []
        if ref_cad:
            infos.append("R\u00e9f. cadastrale : " + str(ref_cad))
        if parcelle:
            infos.append("Parcelle : " + str(parcelle))
        if section:
            infos.append("Section : " + str(section))
        if surface_terrain:
            infos.append("Surface terrain : " + str(surface_terrain) + " m\u00b2")
        c.drawString(ix, info_y - 7 * mm, "  \u00b7  ".join(infos))

    _footer(c, page_num, total=total)


# ---------------------------------------------------------------------------
# PDF Generation
# ---------------------------------------------------------------------------
def generate_dossier_pdf(d):
    buf = io.BytesIO()
    cv = rl_canvas.Canvas(buf, pagesize=A4)
    cv.setTitle("Dossier \u2014 " + str(d.get("reference", "")))

    photos = d.get("photos") or []
    # Separate real photos from cadastre/PDFs
    real_photos = [p for i, p in enumerate(photos) if i > 0 and not _is_plan(p)]
    cadastre_photos = [p for p in photos if _is_plan(p)]

    has_photos = len(real_photos) > 0
    has_cadastre = len(cadastre_photos) > 0
    total = 3 + (1 if has_photos else 0) + (1 if has_cadastre else 0)

    _page1(cv, d)
    cv.showPage()
    _page2(cv, d)
    cv.showPage()
    _page3(cv, d)
    cv.showPage()
    pn = 4
    if has_photos:
        _page_photos(cv, d, page_num=pn, total=total)
        cv.showPage()
        pn += 1
    if has_cadastre:
        _page_cadastre(cv, d, page_num=pn, total=total)
        cv.showPage()

    cv.save()
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def health():
    return jsonify({"service": "Barbier PDF Generator", "status": "ok", "version": "5.11"})


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
    photos = body.get("photos") or []
    real = [p for i, p in enumerate(photos) if i > 0 and not _is_plan(p)]
    plans = [p for p in photos if _is_plan(p)]
    app.logger.info(
        "Dossier v5.16 for %s — keys: %s — photos total=%d, real=%d, cadastre=%d",
        ref, list(body.keys()), len(photos), len(real), len(plans))
    # Log first 80 chars of each photo to debug
    for idx, p in enumerate(photos):
        app.logger.info("  photo[%d]: %s", idx, str(p)[:80])

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

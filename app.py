#!/usr/bin/env python3
"""
Barbier Immobilier - PDF Dossier de Vente v5.22
Flask app deployed on Railway.
Routes: GET /, POST /generate-quartier, POST /dossier, POST /mandat, POST /avis-valeur, POST /urbanisme
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


def _haversine_km(lat1, lon1, lat2, lon2):
    """Distance orthodromique entre 2 points (km). Formule haversine standard."""
    import math
    if None in (lat1, lon1, lat2, lon2):
        return float("inf")
    try:
        lat1, lon1, lat2, lon2 = map(float, (lat1, lon1, lat2, lon2))
    except (TypeError, ValueError):
        return float("inf")
    R = 6371.0  # rayon Terre km
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def _gpt_quartier(adresse, ville, type_bien):
    """Texte de présentation ville & quartier. Double-paragraphe, spécifique à l'adresse.
    Retry 1× si la sortie est < 120 mots (signe d'échec GPT)."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        app.logger.warning("GPT quartier: OPENAI_API_KEY manquante — fallback")
        return ""
    v = ville or "Vannes"
    a = adresse or v
    tb = type_bien or "Local commercial"
    prompt = (
        "Tu es un expert en immobilier commercial dans le Golfe du Morbihan (Bretagne Sud).\n"
        "Tu rédiges un texte de présentation en DEUX paragraphes distincts, destiné à un acquéreur "
        "d'un " + tb.lower() + " situé au " + a + ", " + v + " (Morbihan).\n\n"
        "FORMAT STRICT :\n"
        "Paragraphe 1 — LA VILLE (80-100 mots) : dynamique économique de " + v + ", démographie, "
        "attractivité, position dans le Golfe du Morbihan. Cite 1 ou 2 éléments concrets (port, "
        "université, zones d'activité, tourisme…).\n\n"
        "Paragraphe 2 — LE QUARTIER (100-140 mots) : cible précisément la rue ou le secteur de "
        "l'adresse '" + a + "'. Parle du TYPE de quartier (centre historique, péricentre commerçant, "
        "zone d'activité, quartier résidentiel, proximité gare/port…), du tissu commercial environnant, "
        "de l'accessibilité (parkings, transports, axes), et de la pertinence pour un " + tb.lower() + ".\n\n"
        "RÈGLES :\n"
        "- Texte continu, pas de titre, pas de liste, pas de phrase vague.\n"
        "- Sépare les deux paragraphes par une ligne vide.\n"
        "- Évite les formules creuses ('emplacement stratégique', 'cadre privilégié') sans justification.\n"
        "- Nomme la rue ou le secteur concerné au moins une fois dans le paragraphe 2."
    )

    def _call():
        try:
            resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
                json={"model": "gpt-4o-mini",
                      "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 700, "temperature": 0.55},
                timeout=30)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            app.logger.error("GPT quartier call: %s", e)
            return ""

    out = _call()
    # Retry 1× si sortie trop courte (signe d'échec ou troncature)
    if out and len(out.split()) < 120:
        app.logger.warning("GPT quartier: sortie trop courte (%d mots), retry", len(out.split()))
        out2 = _call()
        if out2 and len(out2.split()) >= len(out.split()):
            out = out2
    if not out:
        app.logger.error("GPT quartier: FALLBACK utilisé (API indisponible ou vide)")
    return out


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


def _header(c, sub="", prefix="DOSSIER DE PRESENTATION"):
    c.setFillColor(TEAL)
    c.rect(0, PAGE_H - HEADER_H, PAGE_W, HEADER_H, fill=1, stroke=0)
    c.saveState()
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 8.5)
    header_text = prefix + ("  >  " + sub.upper()[:70] if sub else "")
    c.drawString(ML, PAGE_H - 7.5 * mm, header_text)
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
    c.drawRightString(PAGE_W - MR, 3.5 * mm, "v5.32  " + str(n) + " / " + str(total))
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
def _page1(c, d, page_num=1, total=3):
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

    # Price — show prix net vendeur when available, else prix FAI
    # En mode estimation (show_honoraires=False), on affiche uniquement la valeur estimée sans distinction FAI/net
    show_hono = d.get("show_honoraires", True)
    is_estim = (str(d.get("mode", "")).lower() == "estimation")
    pnv_cover = d.get("prix_net_vendeur") or 0
    prix_cover = int(float(str(pnv_cover))) if pnv_cover else (d.get("prix") or 0)
    if is_estim and not show_hono:
        # En estimation sans honoraires : afficher la valeur estimée (prix_retenu) si dispo
        prix_retenu = d.get("prix_retenu")
        if prix_retenu:
            try:
                prix_cover = int(float(str(prix_retenu)))
            except (ValueError, TypeError):
                pass
        prix_label = "VALEUR ESTIMÉE"
    else:
        prix_label = "PRIX NET VENDEUR" if pnv_cover else "PRIX DE VENTE FAI"
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 34)
    c.drawString(ML, PAGE_H - 84 * mm, _pfmt(prix_cover))
    # Honoraires (masqués si show_honoraires=False)
    hono = d.get("honoraires")
    display_hono = hono and show_hono
    if display_hono:
        c.setFillColor(colors.HexColor("#FFFFFFBB"))
        c.setFont("Helvetica", 9)
        ht = "Honoraires : " + _pfmt(hono) + " HT"
        hc = d.get("honoraires_charge", "")
        if hc:
            ht = ht + " (" + str(hc) + ")"
        c.drawString(ML, PAGE_H - 91 * mm, ht)
    c.setFillColor(WHITE)
    c.setFont("Helvetica", 9)
    c.drawString(ML, PAGE_H - (97 if display_hono else 91) * mm, prix_label)

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

    # Logo top-right (bord carré)
    try:
        lw2 = 28 * mm
        lh2 = lw2 * (662 / 488)
        lx2 = PAGE_W - lw2 - 8 * mm
        ly2 = PAGE_H - lh2 - 5 * mm
        pad = 2.5 * mm
        c.setFillColor(WHITE)
        c.rect(lx2 - pad, ly2 - pad, lw2 + pad * 2, lh2 + pad * 2, fill=1, stroke=0)
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
    _footer(c, page_num, total=total)


# ---------------------------------------------------------------------------
# PAGE 2 — Quartier & Localisation (50/50 carte + POI)
# ---------------------------------------------------------------------------
def _page2(c, d, page_num=2, total=3):
    _header(c, "Quartier & environnement")

    # -- 1) "Pourquoi Barbier Immobilier" at TOP
    pourquoi_h = 30 * mm
    pq_top = PAGE_H - HEADER_H - SP_AFTER_HEADER
    pq_bot = pq_top - pourquoi_h
    c.setFillColor(TEAL)
    c.roundRect(ML, pq_bot, CW, pourquoi_h, 2 * mm, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(ML + 5 * mm, pq_top - 5 * mm, "Pourquoi Barbier Immobilier ?")
    c.setFont("Helvetica", 7.5)
    lines_pq = [
        "Plus de 35 ans d'expertise en immobilier commercial dans le Morbihan.",
        "Un accompagnement personnalis\u00e9 pour chaque projet d'investissement.",
        "Une connaissance approfondie du tissu \u00e9conomique local et des opportunit\u00e9s.",
    ]
    for i_pq, lpq in enumerate(lines_pq):
        c.drawString(ML + 5 * mm, pq_top - 14 * mm - i_pq * 6 * mm,
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
                         textColor=GRAY_DARK, leading=15, alignment=4)
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

    _footer(c, page_num, total=total)


# ---------------------------------------------------------------------------
# PAGE 3 — Annonce + Donnees financieres + Prix
# ---------------------------------------------------------------------------
def _page3(c, d, page_num=3, total=3, skip_bail_prix=False):
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

    # When bail_details is provided, bail+prix go on a dedicated page
    if skip_bail_prix:
        bail_rows = []
        bail_bloc_h = 0
        prix_block_h = 0
        has_prix_inline = False
    else:
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
        bail_bloc_h = (math.ceil(len(bail_rows) / 2) * 12 * mm + 4 * mm) if bail_rows else 0
        prix_block_h = 28 * mm if has_prix else 0
        has_prix_inline = has_prix

    # ── PRE-COMPUTE: what desc can use ──
    bottom_used = FOOTER_H + SP_BEFORE_FOOTER
    if has_prix and not skip_bail_prix:
        bottom_used += prix_block_h + SP_BETWEEN_BLOCS + SEC_H + SP_AFTER_SEC
    if bail_rows:
        bottom_used += bail_bloc_h + SP_BETWEEN_BLOCS + SEC_H + SP_AFTER_SEC
    bottom_used += pills_total_h + SP_BETWEEN_BLOCS + SEC_H + SP_AFTER_SEC

    # ── BLOC 1: Présentation du bien ──
    cursor = PAGE_H - HEADER_H - SP_AFTER_HEADER
    _sec(c, "Pr\u00e9sentation du bien", ML, cursor)
    cursor -= SEC_H + SP_AFTER_SEC

    desc = _clean(d.get("description", ""))
    # Filter out "augmentation" phrase if present
    desc = re.sub(r",?\s*avec une augmentation de [0-9,.]+ ?% sur la p\u00e9riode r\u00e9cente,?", "", desc)
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

    # ── BLOC 3: Données du bail (inline, skipped when bail_details page exists) ──
    if bail_rows and not skip_bail_prix:
        row_h_bail = 12 * mm
        cursor -= SP_BETWEEN_BLOCS
        bail_label = "Donn\u00e9es du bail" if is_bail else "Donn\u00e9es financi\u00e8res"
        _sec(c, bail_label, ML, cursor)
        cursor -= SEC_H + SP_AFTER_SEC
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

    # ── BLOC 4: Prix (inline, skipped when bail_details page exists) ──
    if has_prix and not skip_bail_prix:
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
            # Determine layout: 3 boxes + optional rentabilité
            # Si show_honoraires=False (mode estimation), n'afficher que PRIX FAI (pas honoraires ni net vendeur)
            show_hono3 = d.get("show_honoraires", True)
            taux = d.get("taux_rentabilite") or ""
            if show_hono3:
                n_boxes = 4 if taux else 3
            else:
                n_boxes = 2 if taux else 1
            bw3 = CW / n_boxes - 2 * mm
            hcharge = d.get("honoraires_charge") or "Acqu\u00e9reur"
            bloc_y = cursor - prix_block_h
            if show_hono3:
                items = [
                    ("PRIX FAI HT", _pfmt(prix_fai), TEAL),
                    ("HONORAIRES HT (" + str(hcharge)[:12] + ")", _pfmt(hono_v), ORANGE),
                    ("PRIX NET VENDEUR", _pfmt(pnv_v), TEAL_DARK),
                ]
            else:
                items = [("VALEUR ESTIMÉE", _pfmt(prix_fai), TEAL_DARK)]
            if taux:
                items.append(("RENTABILIT\u00c9 BRUTE", str(taux), colors.HexColor("#2E7D32")))
            for ip, (lbl, val, col) in enumerate(items):
                bxp = ML + ip * (bw3 + 2 * mm)
                c.setFillColor(col)
                c.roundRect(bxp, bloc_y, bw3, prix_block_h, 2.5 * mm, fill=1, stroke=0)
                c.setFillColor(WHITE)
                c.setFont("Helvetica", 5.5 if n_boxes == 4 else 6)
                c.drawString(bxp + 3 * mm, bloc_y + prix_block_h - 8 * mm, lbl)
                c.setStrokeColor(colors.HexColor("#FFFFFF55"))
                c.setLineWidth(0.5)
                c.line(bxp + 3 * mm, bloc_y + prix_block_h - 10 * mm, bxp + bw3 - 3 * mm, bloc_y + prix_block_h - 10 * mm)
                c.setFont("Helvetica-Bold", 11 if n_boxes == 4 else 13)
                c.drawString(bxp + 3 * mm, bloc_y + 6 * mm, val)
        except Exception as e:
            app.logger.error("Prix block: %s", e)

    _footer(c, page_num, total=total)


# ---------------------------------------------------------------------------
# PAGE — Détails du bail (dedicated page for comprehensive bail data) + Prix
# ---------------------------------------------------------------------------
def _page_bail_details(c, d, page_num, total):
    """Dedicated page for extensive bail details + prix section."""
    _header(c, _safe(d.get("type_bien")) + " \u2014 " + _safe(d.get("adresse")) + ", " + _safe(d.get("ville")))

    cursor = PAGE_H - HEADER_H - SP_AFTER_HEADER
    _sec(c, "D\u00e9tails du bail", ML, cursor)
    cursor -= SEC_H + SP_AFTER_SEC

    bail_items = d.get("bail_details") or []
    if not bail_items:
        # Fallback: build from legacy fields
        loc = d.get("locataire") or ""
        lht = d.get("loyer_annuel_ht") or 0
        linit = d.get("loyer_initial_ht") or 0
        evol = d.get("evolution_loyer") or ""
        duree = d.get("duree_bail") or ""
        taxe = d.get("taxe_fonciere") or 0
        if loc:   bail_items.append({"label": "Locataire", "value": loc})
        if lht:   bail_items.append({"label": "Loyer annuel HT", "value": _pfmt(lht) + " HT/an"})
        if linit:  bail_items.append({"label": "Loyer initial", "value": _pfmt(linit) + " HT"})
        if evol:   bail_items.append({"label": "\u00c9volution loyer", "value": str(evol)})
        if duree:  bail_items.append({"label": "Dur\u00e9e du bail", "value": str(duree)})
        if taxe:   bail_items.append({"label": "Taxe fonci\u00e8re", "value": _pfmt(taxe) + "/an"})

    if bail_items:
        # Calculate heights: use paragraph wrapping for long values
        label_w = 52 * mm
        val_x = ML + label_w + 3 * mm
        val_w = CW - label_w - 8 * mm
        row_gap = 1.5 * mm

        # Pre-compute row heights
        sty_val = ParagraphStyle("bv", fontName="Helvetica-Bold", fontSize=8,
                                 textColor=TEAL_DARK, leading=10)
        row_heights = []
        for item in bail_items:
            val_txt = str(item.get("value", "")).replace("&", "&amp;").replace("<", "&lt;")
            p = Paragraph(val_txt, sty_val)
            _, rh = p.wrap(val_w, 200 * mm)
            row_heights.append(max(rh + 3 * mm, 9 * mm))

        total_bail_h = sum(row_heights) + len(row_heights) * row_gap + 6 * mm

        # Background
        c.setFillColor(colors.HexColor("#EBF0F8"))
        c.roundRect(ML, cursor - total_bail_h, CW, total_bail_h, 2.5 * mm, fill=1, stroke=0)

        ry = cursor - 4 * mm
        for idx, item in enumerate(bail_items):
            label = str(item.get("label", ""))
            value = str(item.get("value", ""))
            rh = row_heights[idx]

            # Label (left column)
            c.setFillColor(GRAY_MID)
            c.setFont("Helvetica", 6.5)
            c.drawString(ML + 5 * mm, ry - 2 * mm, label.upper())

            # Separator line
            c.setStrokeColor(colors.HexColor("#C0CBD8"))
            c.setLineWidth(0.3)
            c.line(ML + 5 * mm, ry - 3.5 * mm, ML + CW - 5 * mm, ry - 3.5 * mm)

            # Value (right column, wrapped)
            val_txt = value.replace("&", "&amp;").replace("<", "&lt;")
            p = Paragraph(val_txt, sty_val)
            _, ph = p.wrap(val_w, 200 * mm)
            p.drawOn(c, val_x, ry - 3 * mm - ph)

            ry -= rh + row_gap

        cursor -= total_bail_h

    # ── Prix section ──
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

            cursor -= SP_BETWEEN_BLOCS
            _sec(c, "Prix", ML, cursor)
            cursor -= SEC_H + SP_AFTER_SEC
            # Si show_honoraires=False (mode estimation), n'afficher que la valeur sans détail
            show_hono_b = d.get("show_honoraires", True)
            taux = d.get("taux_rentabilite") or ""
            if show_hono_b:
                n_boxes = 4 if taux else 3
            else:
                n_boxes = 2 if taux else 1
            prix_block_h = 28 * mm
            bw3 = CW / n_boxes - 2 * mm
            hcharge = d.get("honoraires_charge") or "Acqu\u00e9reur"
            bloc_y = cursor - prix_block_h
            if show_hono_b:
                items = [
                    ("PRIX FAI HT", _pfmt(prix_fai), TEAL),
                    ("HONORAIRES HT (" + str(hcharge)[:12] + ")", _pfmt(hono_v), ORANGE),
                    ("PRIX NET VENDEUR", _pfmt(pnv_v), TEAL_DARK),
                ]
            else:
                items = [("VALEUR ESTIMÉE", _pfmt(prix_fai), TEAL_DARK)]
            if taux:
                items.append(("RENTABILIT\u00c9 BRUTE", str(taux), colors.HexColor("#2E7D32")))
            for ip, (lbl, val, col) in enumerate(items):
                bxp = ML + ip * (bw3 + 2 * mm)
                c.setFillColor(col)
                c.roundRect(bxp, bloc_y, bw3, prix_block_h, 2.5 * mm, fill=1, stroke=0)
                c.setFillColor(WHITE)
                c.setFont("Helvetica", 5.5 if n_boxes == 4 else 6)
                c.drawString(bxp + 3 * mm, bloc_y + prix_block_h - 8 * mm, lbl)
                c.setStrokeColor(colors.HexColor("#FFFFFF55"))
                c.setLineWidth(0.5)
                c.line(bxp + 3 * mm, bloc_y + prix_block_h - 10 * mm, bxp + bw3 - 3 * mm, bloc_y + prix_block_h - 10 * mm)
                c.setFont("Helvetica-Bold", 11 if n_boxes == 4 else 13)
                c.drawString(bxp + 3 * mm, bloc_y + 6 * mm, val)
        except Exception as e:
            app.logger.error("Prix block (bail page): %s", e)

    _footer(c, page_num, total=total)


# ---------------------------------------------------------------------------
# PAGE — Plans des locaux (floor plans)
# ---------------------------------------------------------------------------
def _page_plans_locaux(c, d, page_num, total):
    """Page displaying floor plans of the property."""
    _header(c, "Plans des locaux \u2014 " + _safe(d.get("adresse")) + ", " + _safe(d.get("ville")))
    sec_y = PAGE_H - HEADER_H - SP_AFTER_HEADER
    _sec(c, "Plans des locaux", ML, sec_y)

    plans = d.get("plans") or []
    zone_top = sec_y - SEC_H - SP_AFTER_SEC
    zone_bot = FOOTER_H + SP_BEFORE_FOOTER
    available_h = zone_top - zone_bot
    gap_y = 4 * mm

    if not plans:
        c.setFillColor(GRAY_LIGHT)
        c.roundRect(ML, zone_bot, CW, available_h, 3 * mm, fill=1, stroke=0)
        c.setFillColor(GRAY_MID)
        c.setFont("Helvetica", 10)
        c.drawCentredString(ML + CW / 2, zone_bot + available_h / 2, "Plans non disponibles")
        _footer(c, page_num, total=total)
        return

    n = min(len(plans), 2)
    ph_each = (available_h - (n - 1) * gap_y) / n

    for i in range(n):
        img = _fetch_photo(plans[i])
        py = zone_top - (i + 1) * ph_each - i * gap_y
        if img:
            try:
                iw, ih = img.getSize() if hasattr(img, "getSize") else img.size
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
                # White background + border
                c.setFillColor(WHITE)
                c.setStrokeColor(colors.HexColor("#CCCCCC"))
                c.setLineWidth(0.5)
                c.roundRect(ML, py, CW, ph_each, 3 * mm, fill=1, stroke=1)
                c.drawImage(img if hasattr(img, "getSize") else ImageReader(img), dx, dy, width=dw, height=dh, mask="auto")
            except Exception as e:
                app.logger.error("Plan draw: %s", e)
                c.setFillColor(GRAY_LIGHT)
                c.roundRect(ML, py, CW, ph_each, 3 * mm, fill=1, stroke=0)
        else:
            c.setFillColor(GRAY_LIGHT)
            c.setStrokeColor(GRAY_BDR)
            c.setLineWidth(0.5)
            c.roundRect(ML, py, CW, ph_each, 3 * mm, fill=1, stroke=1)
            c.setFillColor(GRAY_MID)
            c.setFont("Helvetica", 9)
            c.drawCentredString(ML + CW / 2, py + ph_each / 2, "Plan " + str(i + 1))

    _footer(c, page_num, total=total)


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
    plans_locaux = d.get("plans") or []
    cad_photos = d.get("cadastre_photos") or []
    _plan_set = set(plans_locaux) if plans_locaux else set()
    _cad_set = set(cad_photos)
    # Filter: skip first (cover) + skip cadastre + skip plans locaux
    real_photos = []
    for i, p in enumerate(photos):
        if i == 0:
            continue
        if p in _cad_set or _is_plan(p):
            continue
        if p in _plan_set:
            continue
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

    # Use explicit cadastre_photos from cockpit, fallback to _is_plan detection
    cad_photos = d.get("cadastre_photos") or [p for p in (d.get("photos") or []) if _is_plan(p)]
    cadastre_imgs = []
    for p in cad_photos:
        img = _fetch_photo(p)
        if img:
            cadastre_imgs.append(img)

    # Parcel info data
    ref_cad = d.get("reference_cadastrale") or d.get("ref_cadastrale") or ""
    parcelle = d.get("parcelle") or ""
    section = d.get("section_cadastrale") or ""
    surface_terrain = d.get("surface_terrain") or ""
    has_info = bool(ref_cad or parcelle or section or surface_terrain)

    # Reserve clean space for parcel info block ABOVE footer
    INFO_BAR_H = 8 * mm
    INFO_GAP = 1.5 * mm
    INFO_MARGIN_BOTTOM = 5 * mm   # gap between info bar and footer
    INFO_MARGIN_TOP = 5 * mm      # gap between plan and info block
    if has_info:
        info_block_h = SEC_H + INFO_GAP + INFO_BAR_H
        zone_bot = FOOTER_H + INFO_MARGIN_BOTTOM + info_block_h + INFO_MARGIN_TOP
    else:
        zone_bot = FOOTER_H + 8 * mm

    zone_top = sec_y - SEC_H - SP_AFTER_SEC
    available_h = zone_top - zone_bot
    gap_y = 4 * mm

    if not cadastre_imgs:
        c.setFillColor(GRAY_LIGHT)
        c.roundRect(ML, zone_bot, CW, available_h, 3 * mm, fill=1, stroke=0)
        c.setFillColor(GRAY_MID)
        c.setFont("Helvetica", 10)
        c.drawCentredString(ML + CW / 2, zone_bot + available_h / 2, "Plan cadastral non disponible")
    else:
        # Display cadastre images — full width, stacked
        n = min(len(cadastre_imgs), 2)
        ph_each = (available_h - (n - 1) * gap_y) / n

        for i in range(n):
            py = zone_top - (i + 1) * ph_each - i * gap_y
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

    # Parcel info block — positioned cleanly above footer
    if has_info:
        bar_y = FOOTER_H + INFO_MARGIN_BOTTOM
        title_y = bar_y + INFO_BAR_H + INFO_GAP
        _sec(c, "Informations parcelle", ML, title_y)
        c.setFillColor(colors.HexColor("#EBF0F8"))
        c.roundRect(ML, bar_y, CW, INFO_BAR_H, 1.5 * mm, fill=1, stroke=0)
        c.setFillColor(TEAL_DARK)
        c.setFont("Helvetica-Bold", 9)
        infos = []
        if ref_cad:
            infos.append("R\u00e9f. cadastrale : " + str(ref_cad))
        if parcelle:
            infos.append("Parcelle : " + str(parcelle))
        if section:
            infos.append("Section : " + str(section))
        if surface_terrain:
            infos.append("Surface terrain : " + str(surface_terrain) + " m\u00b2")
        # PLU : priorité saisie manuelle (plu_manuel), sinon zone PLU Airtable
        plu_txt = (d.get("plu_manuel") or "").strip() or (d.get("Zone PLU") or d.get("zone_plu") or "").strip()
        if plu_txt:
            infos.append("PLU : " + plu_txt[:60])
        c.drawString(ML + 5 * mm, bar_y + INFO_BAR_H / 2 - 1.2 * mm,
                     "  \u00b7  ".join(infos))

    _footer(c, page_num, total=total)


# ---------------------------------------------------------------------------
# PAGE — Biens comparables (estimation mode)
# ---------------------------------------------------------------------------
def _page_comparables(c, d, page_num, total):
    ville = _safe(d.get("ville"), "Vannes")
    _header(c, "Biens comparables \u2014 " + ville)

    cursor = PAGE_H - HEADER_H - SP_AFTER_HEADER
    _sec(c, "Analyse des biens comparables", ML, cursor)
    cursor -= SEC_H + SP_AFTER_SEC

    # Intro text
    intro = ("S\u00e9lection des transactions les plus r\u00e9centes permettant de "
             "positionner ce bien dans son march\u00e9 local.")
    c.setFillColor(GRAY_DARK)
    c.setFont("Helvetica", 8.5)
    c.drawString(ML, cursor, intro)
    cursor -= 8 * mm

    # -- Comparable cards 2x2 grid --
    comps = (d.get("comparables") or [])[:4]
    while len(comps) < 4:
        comps.append(None)

    card_gap_x = 6 * mm
    card_gap_y = 6 * mm
    card_w = (CW - card_gap_x) / 2
    card_h = 52 * mm

    for idx in range(4):
        col = idx % 2
        row = idx // 2
        cx = ML + col * (card_w + card_gap_x)
        cy = cursor - row * (card_h + card_gap_y) - card_h

        comp = comps[idx]
        # Card border
        _rrect(c, cx, cy, card_w, card_h, r=3, stroke=GRAY_BDR)

        # Circle with number
        circ_r = 4 * mm
        circ_x = cx + 6 * mm
        circ_y = cy + card_h - 8 * mm
        c.setFillColor(TEAL_DARK)
        c.circle(circ_x, circ_y, circ_r, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont("Helvetica-Bold", 9)
        c.drawCentredString(circ_x, circ_y - 3, str(idx + 1))

        # "VENDU" badge top right
        badge_w = 16 * mm
        badge_h = 5 * mm
        badge_x = cx + card_w - badge_w - 4 * mm
        badge_y = cy + card_h - 9 * mm
        c.setFillColor(TEAL_DARK)
        c.roundRect(badge_x, badge_y, badge_w, badge_h, 1.5 * mm, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont("Helvetica-Bold", 6.5)
        c.drawCentredString(badge_x + badge_w / 2, badge_y + 1.5 * mm, "VENDU")

        if comp:
            adr = comp.get("adresse") or "\u2014"
            v = comp.get("ville") or ""
            prix = comp.get("prix") or 0
            surface = comp.get("surface") or 0
            prix_m2 = comp.get("prix_m2") or 0
            annee = comp.get("annee") or ""
            source = comp.get("source") or "DVF"

            # Address
            c.setFillColor(GRAY_DARK)
            c.setFont("Helvetica-Bold", 8)
            adr_trunc = adr[:38]
            c.drawString(cx + 5 * mm, cy + card_h - 17 * mm, adr_trunc)

            # Ville
            c.setFillColor(GRAY_MID)
            c.setFont("Helvetica", 7)
            c.drawString(cx + 5 * mm, cy + card_h - 22 * mm, str(v))

            # Price (large, orange)
            c.setFillColor(ORANGE)
            c.setFont("Helvetica-Bold", 14)
            c.drawString(cx + 5 * mm, cy + card_h - 32 * mm, _pfmt(prix))

            # Price per m2
            c.setFillColor(GRAY_MID)
            c.setFont("Helvetica", 7.5)
            pm2_str = _pfmt(prix_m2).replace(" \u20ac", "") + " \u20ac/m\u00b2" if prix_m2 else "\u2014"
            c.drawString(cx + 5 * mm, cy + card_h - 38 * mm, pm2_str)

            # Surface (+ type de bien sur la même ligne)
            surf_str = str(surface) + " m\u00b2" if surface else "\u2014"
            type_comp = (comp.get("type_bien") or "").strip()
            if type_comp:
                # Tronquer le type si trop long pour tenir dans la carte
                t_short = type_comp[:22]
                line = "Surface : " + surf_str + "  \u00b7  " + t_short
            else:
                line = "Surface : " + surf_str
            c.drawString(cx + 5 * mm, cy + card_h - 43 * mm, line)

            # Source line
            c.setFillColor(GRAY_MID)
            c.setFont("Helvetica", 6.5)
            src_txt = str(source) + " " + str(annee) if annee else str(source)
            c.drawString(cx + 5 * mm, cy + 3 * mm, src_txt)
        else:
            # Empty card
            c.setFillColor(GRAY_MID)
            c.setFont("Helvetica", 10)
            c.drawCentredString(cx + card_w / 2, cy + card_h / 2 - 6 * mm, "\u2014")

    cursor -= 2 * (card_h + card_gap_y)

    # -- Synthese marche --
    cursor -= SP_BETWEEN_BLOCS
    _sec(c, "Synth\u00e8se march\u00e9", ML, cursor)
    cursor -= SEC_H + SP_AFTER_SEC

    # Compute averages from available comparables
    valid = [comp for comp in comps if comp]
    if valid:
        avg_prix = int(sum(comp.get("prix", 0) for comp in valid) / len(valid))
        avg_m2 = int(sum(comp.get("prix_m2", 0) for comp in valid) / len(valid))
        annees = [comp.get("annee", 0) for comp in valid if comp.get("annee")]
        most_recent = str(max(annees)) if annees else "\u2014"
    else:
        avg_prix = 0
        avg_m2 = 0
        most_recent = "\u2014"

    box_w = (CW - 2 * 5 * mm) / 3
    box_h = 18 * mm
    synth_items = [
        ("Prix moyen constat\u00e9", _pfmt(avg_prix)),
        ("Prix moyen au m\u00b2", _pfmt(avg_m2).replace(" \u20ac", "") + " \u20ac/m\u00b2" if avg_m2 else "\u2014"),
        ("Ann\u00e9e r\u00e9f. la + r\u00e9cente", most_recent),
    ]
    for i, (lbl, val) in enumerate(synth_items):
        bx = ML + i * (box_w + 5 * mm)
        by = cursor - box_h
        _rrect(c, bx, by, box_w, box_h, r=3, fill=TEAL_DARK)
        c.setFillColor(WHITE)
        c.setFont("Helvetica", 6.5)
        c.drawString(bx + 5 * mm, by + box_h - 6 * mm, lbl.upper())
        c.setStrokeColor(colors.HexColor("#FFFFFF44"))
        c.setLineWidth(0.4)
        c.line(bx + 5 * mm, by + box_h - 8 * mm, bx + box_w - 5 * mm, by + box_h - 8 * mm)
        c.setFillColor(WHITE)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(bx + 5 * mm, by + 4 * mm, val)

    # Source line
    cursor -= box_h + 5 * mm
    c.setFillColor(GRAY_MID)
    c.setFont("Helvetica", 6)
    c.drawString(ML, cursor,
                 "Sources : DVF (data.gouv.fr) \u2014 Mutations de valeurs fonci\u00e8res, donn\u00e9es officielles.")

    _footer(c, page_num, total=total)


# ---------------------------------------------------------------------------
# PAGE — Notre estimation de valeur (estimation mode)
# ---------------------------------------------------------------------------
def _page_estimation(c, d, page_num, total):
    ville = _safe(d.get("ville"), "Vannes")
    _header(c, "Notre estimation de valeur \u2014 " + ville)

    cursor = PAGE_H - HEADER_H - SP_AFTER_HEADER
    _sec(c, "Positionnement prix", ML, cursor)
    cursor -= SEC_H + SP_AFTER_SEC

    prix_min = d.get("prix_estime_min") or 0
    prix_max = d.get("prix_estime_max") or 0
    prix_ret = d.get("prix_retenu") or 0
    surface = d.get("surface") or 0

    # -- 3 price boxes --
    side_w = 52 * mm
    center_w = 62 * mm
    gap_x = (CW - 2 * side_w - center_w) / 2
    side_h = 42 * mm
    center_h = 50 * mm

    boxes = [
        ("FOURCHETTE BASSE", prix_min, "Conditions d\u00e9favorables", TEAL_LIGHT, TEAL_DARK, side_w, side_h),
        ("VALEUR ESTIM\u00c9E", prix_ret, "Recommand\u00e9e", TEAL_DARK, WHITE, center_w, center_h),
        ("FOURCHETTE HAUTE", prix_max, "March\u00e9 porteur", TEAL_LIGHT, TEAL_DARK, side_w, side_h),
    ]

    # Vertically align boxes bottom
    boxes_base_y = cursor - center_h
    box_x = ML
    for i, (label, prix, subtitle, bg, fg, bw, bh) in enumerate(boxes):
        by = cursor - bh  # top-align center box, bottom-align sides
        if i != 1:
            by = boxes_base_y  # align bottom with center box
        _rrect(c, box_x, by, bw, bh, r=4, fill=bg)
        # Label
        c.setFillColor(fg)
        c.setFont("Helvetica", 6.5)
        c.drawCentredString(box_x + bw / 2, by + bh - 8 * mm, label)
        # Separator
        sep_color = colors.HexColor("#FFFFFF44") if bg == TEAL_DARK else colors.HexColor("#0D557033")
        c.setStrokeColor(sep_color)
        c.setLineWidth(0.4)
        c.line(box_x + 6 * mm, by + bh - 10 * mm, box_x + bw - 6 * mm, by + bh - 10 * mm)
        # Price
        c.setFillColor(fg if bg == TEAL_DARK else ORANGE)
        fsz = 18 if i == 1 else 14
        c.setFont("Helvetica-Bold", fsz)
        c.drawCentredString(box_x + bw / 2, by + bh / 2 - 6 * mm, _pfmt(prix))
        # Subtitle
        c.setFillColor(fg)
        c.setFont("Helvetica", 7)
        c.drawCentredString(box_x + bw / 2, by + 5 * mm, subtitle)

        box_x += bw + gap_x

    # Green triangle below center box
    tri_cx = ML + side_w + gap_x + center_w / 2
    tri_y = boxes_base_y - 3 * mm
    tri_size = 4 * mm
    c.setFillColor(colors.HexColor("#22C55E"))
    p = c.beginPath()
    p.moveTo(tri_cx, tri_y)
    p.lineTo(tri_cx - tri_size, tri_y - tri_size * 1.2)
    p.lineTo(tri_cx + tri_size, tri_y - tri_size * 1.2)
    p.close()
    c.drawPath(p, fill=1, stroke=0)

    # Value per m2 text
    cursor = tri_y - tri_size * 1.2 - 5 * mm
    try:
        prix_m2 = int(float(prix_ret) / float(surface)) if surface else 0
    except (ValueError, TypeError, ZeroDivisionError):
        prix_m2 = 0
    pm2_txt = ("Valeur estim\u00e9e au m\u00b2 : " + _pfmt(prix_m2).replace(" \u20ac", "") +
               " \u20ac/m\u00b2 \u00b7 Surface : " + str(surface) + " m\u00b2")
    c.setFillColor(GRAY_DARK)
    c.setFont("Helvetica", 8.5)
    c.drawCentredString(ML + CW / 2, cursor, pm2_txt)

    # -- Pourquoi investir ici? --
    cursor -= SP_BETWEEN_BLOCS
    _sec(c, "Pourquoi investir ici ?", ML, cursor)
    cursor -= SEC_H + SP_AFTER_SEC

    type_bien = _safe(d.get("type_bien"), "bien")
    args = d.get("arguments_investissement") or [
        {"titre": "Emplacement strat\u00e9gique",
         "texte": "Situ\u00e9 au c\u0153ur de " + ville + ", ce " + type_bien.lower() + " b\u00e9n\u00e9ficie d'une localisation de premier choix avec une forte visibilit\u00e9."},
        {"titre": "Dynamisme \u00e9conomique",
         "texte": "Le secteur profite d'un tissu commercial actif et d'une demande locative soutenue dans la r\u00e9gion."},
        {"titre": "Potentiel de valorisation",
         "texte": "Les fondamentaux du march\u00e9 local et les projets d'am\u00e9nagement offrent un potentiel d'appr\u00e9ciation int\u00e9ressant."},
        {"titre": "Rentabilit\u00e9 attractive",
         "texte": "Le ratio prix/loyer de ce bien permet d'envisager un rendement comp\u00e9titif par rapport au march\u00e9."},
    ]
    args = args[:4]

    arg_gap_x = 6 * mm
    arg_gap_y = 5 * mm
    arg_w = (CW - arg_gap_x) / 2
    arg_h = 28 * mm
    border_w = 3 * mm

    for idx, arg in enumerate(args):
        col = idx % 2
        row = idx // 2
        ax = ML + col * (arg_w + arg_gap_x)
        ay = cursor - row * (arg_h + arg_gap_y) - arg_h

        # Card background
        _rrect(c, ax, ay, arg_w, arg_h, r=3, fill=GRAY_LIGHT)
        # Left border accent
        c.setFillColor(TEAL_DARK)
        c.rect(ax, ay, border_w, arg_h, fill=1, stroke=0)

        # Title
        c.setFillColor(ORANGE)
        c.setFont("Helvetica-Bold", 8.5)
        titre = arg.get("titre", "") if isinstance(arg, dict) else str(arg)
        c.drawString(ax + border_w + 4 * mm, ay + arg_h - 8 * mm, titre[:45])

        # Body text
        texte = arg.get("texte", "") if isinstance(arg, dict) else ""
        if texte:
            sty = ParagraphStyle("arg" + str(idx), fontName="Helvetica", fontSize=7,
                                 textColor=GRAY_DARK, leading=9.5)
            para = Paragraph(texte.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), sty)
            pw = arg_w - border_w - 8 * mm
            _, ph = para.wrap(pw, arg_h - 12 * mm)
            para.drawOn(c, ax + border_w + 4 * mm, ay + arg_h - 11 * mm - ph)

    cursor -= 2 * (arg_h + arg_gap_y)

    # -- Pourquoi cet ecart avec les DVF? --
    cursor -= SP_BETWEEN_BLOCS
    _sec(c, "POURQUOI CET \u00c9CART AVEC LES DVF ?", ML, cursor)
    cursor -= SEC_H + SP_AFTER_SEC

    expl_h = 30 * mm
    expl_y = cursor - expl_h
    _rrect(c, ML, expl_y, CW, expl_h, r=3, stroke=GRAY_BDR)

    expl_text = d.get("explication_dvf") or (
        "Les donn\u00e9es DVF (Demandes de Valeurs Fonci\u00e8res) refl\u00e8tent les transactions "
        "pass\u00e9es enregistr\u00e9es par les notaires. Notre estimation int\u00e8gre des facteurs "
        "compl\u00e9mentaires : \u00e9tat du bien, travaux r\u00e9alis\u00e9s, potentiel locatif, "
        "dynamique du march\u00e9 actuel et positionnement strat\u00e9gique. L'\u00e9cart entre la "
        "valeur DVF et notre estimation traduit la valeur ajout\u00e9e li\u00e9e \u00e0 l'expertise "
        "terrain et \u00e0 la connaissance fine du march\u00e9 local."
    )
    sty_expl = ParagraphStyle("expl", fontName="Helvetica", fontSize=7.5,
                              textColor=GRAY_DARK, leading=11, alignment=4)
    para_expl = Paragraph(expl_text.replace("&", "&amp;"), sty_expl)
    pw_expl = CW - 10 * mm
    _, ph_expl = para_expl.wrap(pw_expl, expl_h - 4 * mm)
    para_expl.drawOn(c, ML + 5 * mm, expl_y + expl_h - 3 * mm - ph_expl)

    _footer(c, page_num, total=total)


# ---------------------------------------------------------------------------
# PAGE — Estimation LOYER (page optionnelle, mode estimation)
# ---------------------------------------------------------------------------
def _page_estimation_loyer(c, d, page_num, total):
    ville = _safe(d.get("ville"), "Vannes")
    _header(c, "Estimation de loyer — " + ville)

    cursor = PAGE_H - HEADER_H - SP_AFTER_HEADER
    _sec(c, "Loyer mensuel recommandé", ML, cursor)
    cursor -= SEC_H + SP_AFTER_SEC

    try:
        loyer_min = int(float(str(d.get("loyer_min") or 0)))
    except (ValueError, TypeError):
        loyer_min = 0
    try:
        loyer_max = int(float(str(d.get("loyer_max") or 0)))
    except (ValueError, TypeError):
        loyer_max = 0
    try:
        loyer_retenu = int(float(str(d.get("loyer_retenu") or 0)))
    except (ValueError, TypeError):
        loyer_retenu = 0
    surface = d.get("surface") or 0

    # 3 price boxes (même style que l'estimation vente)
    side_w = 52 * mm
    center_w = 62 * mm
    gap_x = (CW - 2 * side_w - center_w) / 2
    side_h = 42 * mm
    center_h = 50 * mm

    boxes = [
        ("LOYER MIN",     loyer_min,    "/mois HT",  TEAL_LIGHT, TEAL_DARK, side_w, side_h),
        ("LOYER RECOMMANDÉ", loyer_retenu, "/mois HT", TEAL_DARK, WHITE, center_w, center_h),
        ("LOYER MAX",     loyer_max,    "/mois HT",  TEAL_LIGHT, TEAL_DARK, side_w, side_h),
    ]

    boxes_base_y = cursor - center_h
    box_x = ML
    for i, (label, val, subtitle, bg, fg, bw, bh) in enumerate(boxes):
        by = cursor - bh
        if i != 1:
            by = boxes_base_y
        _rrect(c, box_x, by, bw, bh, r=4, fill=bg)
        c.setFillColor(fg)
        c.setFont("Helvetica", 6.5)
        c.drawCentredString(box_x + bw / 2, by + bh - 8 * mm, label)
        sep_color = colors.HexColor("#FFFFFF44") if bg == TEAL_DARK else colors.HexColor("#0D557033")
        c.setStrokeColor(sep_color)
        c.setLineWidth(0.4)
        c.line(box_x + 6 * mm, by + bh - 10 * mm, box_x + bw - 6 * mm, by + bh - 10 * mm)
        c.setFillColor(fg if bg == TEAL_DARK else ORANGE)
        fsz = 18 if i == 1 else 14
        c.setFont("Helvetica-Bold", fsz)
        c.drawCentredString(box_x + bw / 2, by + bh / 2 - 6 * mm, _pfmt(val))
        c.setFillColor(fg)
        c.setFont("Helvetica", 7)
        c.drawCentredString(box_x + bw / 2, by + 5 * mm, subtitle)
        box_x += bw + gap_x

    # Loyer au m² + annuel
    cursor = boxes_base_y - 8 * mm
    if loyer_retenu and surface:
        try:
            loyer_m2 = int(loyer_retenu / float(surface))
        except (ValueError, TypeError, ZeroDivisionError):
            loyer_m2 = 0
        loyer_annuel = loyer_retenu * 12
        sub_txt = (
            "Loyer au m² : " + _pfmt(loyer_m2).replace(" €", "") + " €/m²/mois · "
            "Loyer annuel HT : " + _pfmt(loyer_annuel) + " HT/an · "
            "Surface : " + str(surface) + " m²")
        c.setFillColor(GRAY_DARK)
        c.setFont("Helvetica", 8.5)
        c.drawCentredString(ML + CW / 2, cursor, sub_txt)
        cursor -= 6 * mm

    # Rendement brut (si prix_retenu dispo)
    prix_retenu = d.get("prix_retenu") or 0
    try:
        prix_retenu = float(prix_retenu)
    except (ValueError, TypeError):
        prix_retenu = 0
    if loyer_retenu and prix_retenu > 0:
        loyer_annuel = loyer_retenu * 12
        rendement = (loyer_annuel / prix_retenu) * 100
        cursor -= SP_BETWEEN_BLOCS
        _sec(c, "Rendement brut estimé", ML, cursor)
        cursor -= SEC_H + SP_AFTER_SEC
        rend_box_h = 20 * mm
        rend_y = cursor - rend_box_h
        _rrect(c, ML, rend_y, CW, rend_box_h, r=3, fill=colors.HexColor("#F0FDF4"), stroke=colors.HexColor("#22C55E"))
        c.setFillColor(colors.HexColor("#15803D"))
        c.setFont("Helvetica-Bold", 22)
        c.drawCentredString(ML + CW / 2, rend_y + rend_box_h / 2 - 2, f"{rendement:.2f} %")
        c.setFillColor(GRAY_DARK)
        c.setFont("Helvetica", 8)
        c.drawCentredString(ML + CW / 2, rend_y + 4 * mm,
                            "Loyer annuel HT / Prix d'acquisition estimé (hors charges, taxes et vacance locative)")
        cursor = rend_y - SP_BETWEEN_BLOCS

    # Méthodologie
    cursor -= 2 * mm
    _sec(c, "Méthodologie", ML, cursor)
    cursor -= SEC_H + SP_AFTER_SEC
    meth_h = 28 * mm
    meth_y = cursor - meth_h
    _rrect(c, ML, meth_y, CW, meth_h, r=3, stroke=GRAY_BDR)
    meth_text = d.get("loyer_methodologie") or (
        "Estimation fondée sur notre connaissance du marché local de la location commerciale, "
        "les pratiques tarifaires du secteur, et le positionnement du bien (emplacement, surface, "
        "état, type d'activité compatible). Cette fourchette est indicative et devra être confirmée "
        "par les conditions de marché au moment de la commercialisation.")
    sty = ParagraphStyle("meth", fontName="Helvetica", fontSize=7.5,
                         textColor=GRAY_DARK, leading=11, alignment=4)
    para = Paragraph(meth_text.replace("&", "&amp;"), sty)
    _, ph = para.wrap(CW - 10 * mm, meth_h - 4 * mm)
    para.drawOn(c, ML + 5 * mm, meth_y + meth_h - 3 * mm - ph)

    _footer(c, page_num, total=total)


# ---------------------------------------------------------------------------
# PDF Generation
# ---------------------------------------------------------------------------
def generate_dossier_pdf(d):
    buf = io.BytesIO()
    cv = rl_canvas.Canvas(buf, pagesize=A4)
    cv.setTitle("Dossier \u2014 " + str(d.get("reference", "")))

    photos = d.get("photos") or []
    plans_locaux = d.get("plans") or []
    # Cadastre photos: use explicit list from cockpit, fallback to _is_plan detection
    cadastre_photos = d.get("cadastre_photos") or [p for p in photos if _is_plan(p)]
    _cad_set = set(cadastre_photos)
    _plan_set = set(plans_locaux) if plans_locaux else set()
    # Real photos = all except cover (idx 0), cadastre, and plans
    real_photos = [p for i, p in enumerate(photos)
                   if i > 0 and p not in _cad_set and p not in _plan_set
                   and not _is_plan(p)]

    has_photos = len(real_photos) > 0
    has_cadastre = len(cadastre_photos) > 0
    has_plans = len(plans_locaux) > 0
    has_bail_details = bool(d.get("bail_details"))
    is_estimation = (str(d.get("mode", "")).lower() == "estimation"
                      or bool(d.get("comparables"))
                      or bool(d.get("prix_estime_min"))
                      or bool(d.get("prix_retenu")))
    include_loyer = bool(d.get("include_loyer")) and (
        d.get("loyer_retenu") or d.get("loyer_min") or d.get("loyer_max"))

    # Count total pages
    total = 2  # cover + quartier
    if has_cadastre:
        total += 1
    if is_estimation:
        total += 2
    if include_loyer:
        total += 1
    total += 1  # page3 (annonce + caract\u00e9ristiques)
    if has_bail_details:
        total += 1
    if has_plans:
        total += 1
    if has_photos:
        total += 1

    # Page 1 — Cover
    _page1(cv, d, page_num=1, total=total)
    cv.showPage()

    # Page 2 — Quartier & Environnement
    _page2(cv, d, page_num=2, total=total)
    cv.showPage()

    pn = 3

    # Page 3 — Plan cadastral (moved right after quartier per client request)
    if has_cadastre:
        _page_cadastre(cv, d, page_num=pn, total=total)
        cv.showPage()
        pn += 1

    # Estimation pages (if applicable)
    if is_estimation:
        _page_comparables(cv, d, page_num=pn, total=total)
        cv.showPage()
        pn += 1
        _page_estimation(cv, d, page_num=pn, total=total)
        cv.showPage()
        pn += 1
        # Estimation loyer (page optionnelle)
        if include_loyer:
            _page_estimation_loyer(cv, d, page_num=pn, total=total)
            cv.showPage()
            pn += 1

    # Annonce + Caract\u00e9ristiques (+ inline bail/prix if no bail_details)
    _page3(cv, d, page_num=pn, total=total, skip_bail_prix=has_bail_details)
    cv.showPage()
    pn += 1

    # Dedicated bail details + prix page (when bail_details provided)
    if has_bail_details:
        _page_bail_details(cv, d, page_num=pn, total=total)
        cv.showPage()
        pn += 1

    # Plans des locaux (floor plans)
    if has_plans:
        _page_plans_locaux(cv, d, page_num=pn, total=total)
        cv.showPage()
        pn += 1

    # Photos du bien
    if has_photos:
        _page_photos(cv, d, page_num=pn, total=total)
        cv.showPage()
        pn += 1

    cv.save()
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def health():
    return jsonify({"service": "Barbier PDF Generator", "status": "ok", "version": "5.32"})


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
        "Dossier v5.32 for %s — keys: %s — photos total=%d, real=%d, cadastre=%d",
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


# ---------------------------------------------------------------------------
# MANDAT PDF Generation — 5 pages (couverture + 4 pages legales)
# ---------------------------------------------------------------------------

def _mandat_draw_field(c, label, value, x, y, w, label_w=42):
    """Draw a label: value pair for the mandat. Returns new y after drawing."""
    c.setFont("Helvetica", 7)
    c.setFillColor(GRAY_MID)
    c.drawString(x, y + 3.5 * mm, label)
    c.setFont("Helvetica", 9)
    c.setFillColor(GRAY_DARK)
    c.drawString(x + label_w * mm, y + 3.5 * mm, str(value or "\u2014"))
    c.setStrokeColor(GRAY_BDR)
    c.setLineWidth(0.3)
    c.line(x, y, x + w, y)
    return y - 7 * mm


def _mandat_section(c, title, y):
    """Draw a mandat section header with gap above. Returns y below the section bar."""
    y -= 3 * mm  # gap before section bar
    _sec(c, title, ML, y)
    return y - SEC_H - SP_AFTER_SEC


def _mandat_paraphes(c, page_num, total_content=4):
    """Draw paraphes box and page number at bottom of each content page."""
    c.setFont("Helvetica", 7)
    c.setFillColor(GRAY_MID)
    c.drawString(ML, FOOTER_H + 6 * mm, "Paraphes")
    # Two signature boxes
    bw, bh = 25 * mm, 12 * mm
    for i in range(2):
        bx = ML + 25 * mm + i * (bw + 8 * mm)
        _rrect(c, bx, FOOTER_H + 2 * mm, bw, bh, r=2, stroke=GRAY_BDR)
    c.drawRightString(ML + CW, FOOTER_H + 6 * mm, f"Page {page_num} sur {total_content}")


def _mandat_paragraph(c, text, y, font="Helvetica", size=8, leading=10.5, indent=0):
    """Draw a paragraph of text, returns new y after drawing.
    Always draws — never returns None. Clips if necessary."""
    style = ParagraphStyle("mp", fontName=font, fontSize=size,
                           leading=leading, textColor=GRAY_DARK,
                           leftIndent=indent)
    p = Paragraph(text.replace("\n", "<br/>"), style)
    pw, ph = p.wrap(CW - indent, 500 * mm)
    p.drawOn(c, ML + indent, y - ph)
    return y - ph - 3 * mm, 0, None


def generate_mandat_pdf(d):
    """Generate a mandat de vente PDF — 8 pages (couverture + 7 pages legales)."""
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=A4)

    type_m = d.get("type_mandat", "Simple")
    is_moral = d.get("mandant_type", "physique") == "moral"
    is_exclusif = type_m.lower() == "exclusif"
    num = d.get("num_mandat", "")
    date_s = d.get("date_signature", "")
    duree = d.get("duree_mois", 12)
    nego = d.get("negociatrice", "")
    mandant_name = d.get("mandant_societe") if is_moral else d.get("mandant_nom", "")

    type_label = "NON EXCLUSIF" if not is_exclusif else "EXCLUSIF"

    # ══════════════════════════════════════════════════════════════════════════
    # PAGE 1 — COUVERTURE
    # ══════════════════════════════════════════════════════════════════════════
    _header(c, prefix=f"MANDAT {type_label} DE VENTE")
    _footer(c, 1, total=8)

    cy = PAGE_H / 2 + 40 * mm
    c.setFont("Helvetica-Bold", 22)
    c.setFillColor(TEAL_DARK)
    c.drawCentredString(PAGE_W / 2, cy, "MANDAT " + type_label + " DE VENTE")
    cy -= 10 * mm
    c.setFont("Helvetica", 13)
    c.setFillColor(ORANGE)
    c.drawCentredString(PAGE_W / 2, cy, "IMMOBILIER PROFESSIONNEL")
    cy -= 20 * mm

    # Decorative line
    c.setStrokeColor(ORANGE)
    c.setLineWidth(1.5)
    c.line(PAGE_W / 2 - 40 * mm, cy, PAGE_W / 2 + 40 * mm, cy)
    cy -= 15 * mm

    c.setFont("Helvetica", 10)
    c.setFillColor(GRAY_MID)
    if num:
        c.drawCentredString(PAGE_W / 2, cy, f"Mandat N\u00b0 {num}")
        cy -= 7 * mm
    if date_s:
        c.drawCentredString(PAGE_W / 2, cy, f"En date du {date_s}")

    c.showPage()

    # ══════════════════════════════════════════════════════════════════════════
    # PAGE 2 — PARTIES (Page 1 sur 6)
    # ══════════════════════════════════════════════════════════════════════════
    _header(c, sub=f"N\u00b0 {num}", prefix=f"MANDAT {type_label} DE VENTE")
    cursor = PAGE_H - HEADER_H - SP_AFTER_HEADER

    # Titre
    c.setFont("Helvetica-Bold", 12)
    c.setFillColor(TEAL_DARK)
    c.drawCentredString(PAGE_W / 2, cursor, f"MANDAT {type_label} DE VENTE N\u00b0 {num}")
    cursor -= 12 * mm

    # ── ENTRE LES SOUSSIGNES ──
    cursor = _mandat_section(c, "ENTRE LES SOUSSIGN\u00c9S", cursor)

    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(TEAL_DARK)
    c.drawString(ML, cursor, "Le Mandant")
    cursor -= 5 * mm

    fw = CW
    if is_moral:
        cursor = _mandat_draw_field(c, "Soci\u00e9t\u00e9", d.get("mandant_societe"), ML, cursor, fw)
        cursor = _mandat_draw_field(c, "Forme juridique", d.get("mandant_forme"), ML, cursor, fw)
        cursor = _mandat_draw_field(c, "SIREN", d.get("mandant_siren"), ML, cursor, fw)
        cursor = _mandat_draw_field(c, "Capital", d.get("mandant_capital"), ML, cursor, fw)
        cursor = _mandat_draw_field(c, "Repr\u00e9sent\u00e9 par", d.get("mandant_representant"), ML, cursor, fw)
    else:
        cursor = _mandat_draw_field(c, "Nom", d.get("mandant_nom"), ML, cursor, fw)

    cursor = _mandat_draw_field(c, "Adresse", d.get("mandant_adresse"), ML, cursor, fw)
    cursor = _mandat_draw_field(c, "Code postal / Ville",
                                f"{d.get('mandant_cp', '')} {d.get('mandant_ville', '')}".strip(),
                                ML, cursor, fw)
    cursor -= 3 * mm

    c.setFont("Helvetica", 8)
    c.setFillColor(GRAY_MID)
    c.drawString(ML, cursor, 'Ci-apr\u00e8s "le MANDANT", D\'UNE PART,')
    cursor -= 8 * mm

    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(TEAL_DARK)
    c.drawString(ML, cursor, "Le Mandataire")
    cursor -= 5 * mm

    nego_display = nego or "Marina LE PALLEC"
    mandataire_txt = (
        "<b>barbier immobilier</b>, situ\u00e9e 2 place Albert Einstein 56000 Vannes, "
        "t\u00e9l\u00e9phone +33297471111, adresse mail contact@barbierimmobilier.com, "
        "exploit\u00e9e par la soci\u00e9t\u00e9 <b>RESOLIMMO</b>, SARL au capital de 10 000,0 euros, "
        "dont le si\u00e8ge social est situ\u00e9 2 place Albert Einstein 56000 Vannes, "
        "RCS VANNES n\u00b0 833871585, titulaire de la carte professionnelle "
        "<b>Transactions sur immeubles et fonds de commerce</b> n\u00b0 CPI 5605 2018 000 027 030 "
        "d\u00e9livr\u00e9e par MORBIHAN, num\u00e9ro de TVA FR93833871585, "
        "assur\u00e9e en responsabilit\u00e9 civile professionnelle par <b>MMA ENTREPRISE</b> "
        "dont le si\u00e8ge est sis 14 boulevard Marie et Alexandre Oyon LE MANS, "
        "sur le territoire national sous le n\u00b0 120137405, "
        "Adh\u00e9rente de la caisse de Garantie <b>GALIAN</b> dont le si\u00e8ge est sis "
        "89 rue de la Bo\u00e9tie VANNES sous le n\u00b0 A11060563, "
        "Titulaire du compte s\u00e9questre n\u00b0 00021578201 ouvert aupr\u00e8s <b>CIC VANNES</b>."
        f"<br/><br/>Repr\u00e9sent\u00e9e par <b>{nego_display}</b>, ayant le statut de salari\u00e9, "
        f"dument habilit\u00e9(e) \u00e0 l\u2019effet des pr\u00e9sentes,"
    )
    cursor, _, _ = _mandat_paragraph(c, mandataire_txt, cursor, size=7.5, leading=9.5)
    cursor -= 2 * mm

    c.setFont("Helvetica", 8)
    c.setFillColor(GRAY_MID)
    c.drawString(ML, cursor, 'Ci-apr\u00e8s "l\'Agence" ou "le MANDATAIRE", D\'AUTRE PART,')
    cursor -= 6 * mm

    # Phrase d'intro
    intro = (f"Par les pr\u00e9sentes, le MANDANT conf\u00e8re au MANDATAIRE, qui l\u2019accepte, "
             f"le MANDAT {type_label} DE VENDRE les biens ci-apr\u00e8s d\u00e9sign\u00e9s aux prix, "
             f"charges et conditions convenus.")
    cursor, _, _ = _mandat_paragraph(c, f"<b>{intro}</b>", cursor, font="Helvetica-Bold", size=8.5, leading=11)

    _mandat_paraphes(c, 1, 7)
    c.showPage()

    # ══════════════════════════════════════════════════════════════════════════
    # PAGE 3 — BIEN + PRIX (Page 2 sur 7)
    # ══════════════════════════════════════════════════════════════════════════
    _header(c, sub=f"N\u00b0 {num}", prefix=f"MANDAT {type_label} DE VENTE")
    cursor = PAGE_H - HEADER_H - SP_AFTER_HEADER

    # ── DESIGNATION DES BIENS ──
    cursor = _mandat_section(c, "D\u00c9SIGNATION DES BIENS \u00c0 VENDRE", cursor)
    cursor = _mandat_draw_field(c, "Adresse", d.get("bien_adresse"), ML, cursor, fw)
    cursor = _mandat_draw_field(c, "Occupation", d.get("bien_occupation", "Libre"), ML, cursor, fw)

    desc = d.get("bien_description", "")
    if desc:
        desc_clean = desc[:400]
        if len(desc) > 400:
            desc_clean = desc_clean[:desc_clean.rfind(" ")] + "..."
        c.setFont("Helvetica", 7)
        c.setFillColor(GRAY_MID)
        c.drawString(ML, cursor + 3.5 * mm, "Description")
        cursor -= 1 * mm
        style_desc = ParagraphStyle("mdesc", fontName="Helvetica", fontSize=7.5,
                                    leading=9.5, textColor=GRAY_DARK)
        pd = Paragraph(desc_clean.replace("\n", "<br/>"), style_desc)
        pw, ph = pd.wrap(CW - 42 * mm, 55 * mm)
        ph = min(ph, 55 * mm)
        pd.drawOn(c, ML + 42 * mm, cursor - ph + 3 * mm)
        cursor -= max(ph, 8 * mm) + 2 * mm
        c.setStrokeColor(GRAY_BDR); c.setLineWidth(0.3)
        c.line(ML, cursor + 2 * mm, ML + fw, cursor + 2 * mm)

    # ── PRIX ET HONORAIRES ──
    cursor = _mandat_section(c, "PRIX DE VENTE \u2014 HONORAIRES", cursor)

    prix_nv = d.get("prix_net_vendeur", "")
    prix_vt = d.get("prix_de_vente", "")
    hono = d.get("honoraires", "")
    charge = d.get("honoraires_charge", "Acqu\u00e9reur")

    cursor = _mandat_draw_field(c, "Prix net vendeur", _pfmt(prix_nv), ML, cursor, fw)
    cursor = _mandat_draw_field(c, "Honoraires", _pfmt(hono) + f" \u00e0 la charge de l\u2019{charge.lower()}", ML, cursor, fw)
    cursor = _mandat_draw_field(c, "Prix de vente FAI", _pfmt(prix_vt), ML, cursor, fw)
    cursor -= 2 * mm

    prix_txt = ("Le prix sera r\u00e9gl\u00e9 comptant au plus tard le jour de la signature de "
                "l\u2019acte d\u00e9finitif de vente. Le MANDANT est inform\u00e9 qu\u2019il pourra le cas "
                "\u00e9ch\u00e9ant \u00eatre assujetti \u00e0 l\u2019imp\u00f4t sur les plus-values immobili\u00e8res.")
    cursor, _, _ = _mandat_paragraph(c, prix_txt, cursor, size=7, leading=9)

    hono_txt = ("Ces honoraires seront pay\u00e9s le jour de la signature de l\u2019acte authentique "
                "de vente. Le taux de TVA appliqu\u00e9 aux honoraires sera le taux en vigueur \u00e0 la "
                "date de leur exigibilit\u00e9. En cas d\u2019exercice d\u2019un droit de pr\u00e9emption ou "
                "d\u2019une facult\u00e9 de substitution, son b\u00e9n\u00e9ficiaire sera subrog\u00e9 dans tous "
                "les droits et obligations de l\u2019acqu\u00e9reur. \u00c0 ce titre, il sera notamment tenu "
                "de r\u00e9gler ces honoraires si leur paiement lui incombe.")
    cursor, _, _ = _mandat_paragraph(c, hono_txt, cursor, size=7, leading=9)

    _mandat_paraphes(c, 2, 7)
    c.showPage()

    # ══════════════════════════════════════════════════════════════════════════
    # PAGE 4 — DUREE + CONDITIONS GENERALES (Page 3 sur 7)
    # ══════════════════════════════════════════════════════════════════════════
    _header(c, sub=f"N\u00b0 {num}", prefix=f"MANDAT {type_label} DE VENTE")
    cursor = PAGE_H - HEADER_H - SP_AFTER_HEADER

    # ── DUREE DU MANDAT ──
    cursor = _mandat_section(c, "DUR\u00c9E DU MANDAT", cursor)

    if is_exclusif:
        duree_txt = (f"Le pr\u00e9sent mandat est consenti pour une dur\u00e9e de <b>{duree} mois</b> \u00e0 "
                     f"compter de sa signature ({date_s}). Il est irr\u00e9vocable pendant les "
                     f"trois (3) premiers mois. Pass\u00e9 ce d\u00e9lai, il pourra \u00eatre d\u00e9nonc\u00e9 "
                     f"\u00e0 tout moment par lettre recommand\u00e9e avec accus\u00e9 de r\u00e9ception "
                     f"moyennant un pr\u00e9avis de 15 jours.")
    else:
        duree_txt = (f"Le pr\u00e9sent mandat est consenti pour une dur\u00e9e de <b>{duree} mois</b> \u00e0 "
                     f"compter de sa signature ({date_s}). Il pourra \u00eatre d\u00e9nonc\u00e9 "
                     f"\u00e0 tout moment par l\u2019une ou l\u2019autre des parties par lettre recommand\u00e9e "
                     f"avec accus\u00e9 de r\u00e9ception moyennant un pr\u00e9avis de 15 jours. "
                     f"\u00c0 d\u00e9faut de d\u00e9nonciation, il se renouvellera par tacite reconduction "
                     f"pour des p\u00e9riodes successives de m\u00eame dur\u00e9e.")
    cursor, _, _ = _mandat_paragraph(c, duree_txt, cursor, size=8, leading=10.5)
    cursor -= 4 * mm

    # ── CONDITIONS GENERALES ──
    cursor = _mandat_section(c, "CONDITIONS G\u00c9N\u00c9RALES DU MANDAT CONCERNANT LE MANDANT", cursor)

    # Declarations
    decl_title = "<b>Le MANDANT d\u00e9clare, sous sa propre responsabilit\u00e9 :</b>"
    cursor, _, _ = _mandat_paragraph(c, decl_title, cursor, size=8, leading=10.5)

    decls = [
        "avoir la capacit\u00e9 juridique de disposer desdits biens et ne faire l\u2019objet d\u2019aucune "
        "mesure restreignant sa capacit\u00e9 \u00e0 agir (tutelle, curatelle, etc.),",
        "que les biens objets du pr\u00e9sent mandat sont librement cessibles et ne font l\u2019objet "
        "d\u2019aucune proc\u00e9dure de saisie immobili\u00e8re."
    ]
    for decl in decls:
        cursor, _, _ = _mandat_paragraph(c, f"\u2022 {decl}", cursor, size=7.5, leading=9.5, indent=4*mm)

    cursor -= 3 * mm

    # Engagements du mandant
    eng_title = "<b>Le MANDANT s\u2019engage :</b>"
    cursor, _, _ = _mandat_paragraph(c, eng_title, cursor, size=8, leading=10.5)

    engagements = [
        "\u00e0 remettre au MANDATAIRE dans les meilleurs d\u00e9lais au plus tard dans les huit (8) "
        "jours de la signature du pr\u00e9sent mandat tous les documents n\u00e9cessaires \u00e0 "
        "l\u2019ex\u00e9cution de son mandat, notamment le titre de propri\u00e9t\u00e9, les diagnostics, "
        "certificats et justificatifs rendus obligatoires,",
        "\u00e0 informer le MANDATAIRE de tous les \u00e9l\u00e9ments nouveaux, notamment juridiques "
        "et mat\u00e9riels, susceptibles de modifier les conditions de la vente,",
        "s\u2019il accepte une offre d\u2019achat ou s\u2019il signe tout contrat pr\u00e9paratoire \u00e0 la "
        "vente ou s\u2019il vend les biens sans l\u2019interm\u00e9diaire du MANDATAIRE, \u00e0 l\u2019en "
        "informer imm\u00e9diatement et \u00e0 lui communiquer les coordonn\u00e9es de l\u2019Offrant ou "
        "de l\u2019Acqu\u00e9reur, le prix de la vente, les nom et adresse du notaire charg\u00e9 "
        "d\u2019\u00e9tablir l\u2019acte de vente ainsi que, le cas \u00e9ch\u00e9ant, les coordonn\u00e9es de "
        "l\u2019interm\u00e9diaire qui aura concouru \u00e0 la r\u00e9alisation de la vente.",
        "\u00e0 r\u00e9pondre \u00e0 toute offre d\u2019achat transmise par le MANDATAIRE dans un d\u00e9lai "
        "maximum de huit (8) jours."
    ]
    for eng in engagements:
        cursor, _, _ = _mandat_paragraph(c, f"\u2022 {eng}", cursor, size=7.5, leading=9.5, indent=4*mm)

    cursor -= 3 * mm

    gardien_txt = ("Le MANDANT s\u2019engage, en sa qualit\u00e9 de gardien, \u00e0 prendre toutes "
                   "dispositions pour assurer la bonne conservation de ses biens et \u00e0 souscrire, "
                   "\u00e0 cette fin, toutes les assurances requises.")
    cursor, _, _ = _mandat_paragraph(c, gardien_txt, cursor, size=7.5, leading=9.5)

    _mandat_paraphes(c, 3, 7)
    c.showPage()

    # ══════════════════════════════════════════════════════════════════════════
    # PAGE 5 — AUTORISATIONS + INTERDICTIONS + CLAUSE PENALE (Page 4 sur 7)
    # ══════════════════════════════════════════════════════════════════════════
    _header(c, sub=f"N\u00b0 {num}", prefix=f"MANDAT {type_label} DE VENTE")
    cursor = PAGE_H - HEADER_H - SP_AFTER_HEADER

    # Autorisations (suite conditions generales)
    auth_title = "<b>Le MANDANT autorise le MANDATAIRE :</b>"
    cursor, _, _ = _mandat_paragraph(c, auth_title, cursor, size=8, leading=10.5)

    autorisations = [
        "\u00e0 entreprendre toutes les actions de communication qu\u2019il jugera utiles,",
        "\u00e0 r\u00e9clamer aupr\u00e8s de toutes personnes publiques ou priv\u00e9es toutes les pi\u00e8ces "
        "justificatives concernant les biens \u00e0 vendre,",
        "\u00e0 pr\u00e9senter et \u00e0 faire visiter le bien \u00e9tant pr\u00e9cis\u00e9 et accept\u00e9 par le "
        "MANDANT que le MANDATAIRE ne pourra, en aucun cas, \u00eatre consid\u00e9r\u00e9 comme "
        "le gardien juridique des biens \u00e0 vendre,",
        "\u00e0 faire appel, en tant que de besoin et sous sa responsabilit\u00e9, \u00e0 tout concours "
        "ext\u00e9rieur en vue de r\u00e9aliser la vente,",
        "\u00e0 \u00e9tablir tout acte sous seing priv\u00e9 aux clauses et conditions n\u00e9cessaires \u00e0 "
        "l\u2019accomplissement des pr\u00e9sentes, la vente pouvant \u00eatre assortie d\u2019une condition "
        "suspensive d\u2019obtention de pr\u00eat, et \u00e0 recueillir la signature de l\u2019acqu\u00e9reur,",
        "en cas d\u2019exercice d\u2019un droit de pr\u00e9emption, \u00e0 n\u00e9gocier avec le b\u00e9n\u00e9ficiaire de ce droit."
    ]
    for auth in autorisations:
        cursor, _, _ = _mandat_paragraph(c, f"\u2022 {auth}", cursor, size=7.5, leading=9.5, indent=4*mm)

    cursor -= 4 * mm
    bf_txt = "Le MANDANT s\u2019engage \u00e0 ex\u00e9cuter le pr\u00e9sent mandat de bonne foi."
    cursor, _, _ = _mandat_paragraph(c, f"<b>{bf_txt}</b>", cursor, size=8, leading=10.5)
    cursor -= 2 * mm

    interdit_title = "<b>Le MANDANT s\u2019interdit :</b>"
    cursor, _, _ = _mandat_paragraph(c, interdit_title, cursor, size=8, leading=10.5)

    interdits = [
        "pendant la dur\u00e9e du mandat, de n\u00e9gocier directement ou indirectement la vente "
        "des biens ci-dessus d\u00e9sign\u00e9s avec une personne pr\u00e9sent\u00e9e par le MANDATAIRE,",
        "durant les douze (12) mois suivant sa r\u00e9vocation ou son expiration, de traiter, "
        "directement ou indirectement, avec une personne physique ou morale ayant un lien "
        "quelconque avec une personne \u00e0 laquelle ce bien aura \u00e9t\u00e9 pr\u00e9sent\u00e9 par le "
        "MANDATAIRE, ou un mandataire qu\u2019il se sera substitu\u00e9, et dont l\u2019identit\u00e9 aura "
        "\u00e9t\u00e9 communiqu\u00e9e au MANDANT."
    ]
    for interdit in interdits:
        cursor, _, _ = _mandat_paragraph(c, f"\u2022 {interdit}", cursor, size=7.5, leading=9.5, indent=4*mm)

    cursor -= 2 * mm
    oblige_txt = ("Le MANDANT s\u2019oblige, s\u2019il vend les biens pendant la dur\u00e9e du pr\u00e9sent "
                  "mandat ou durant ce m\u00eame d\u00e9lai de douze (12) mois suivant la r\u00e9vocation "
                  "ou l\u2019expiration du mandat, \u00e0 communiquer imm\u00e9diatement au MANDATAIRE "
                  "la date et le prix de la vente, les nom et adresse de l\u2019acqu\u00e9reur et, le cas "
                  "\u00e9ch\u00e9ant, de l\u2019interm\u00e9diaire qui aura permis sa conclusion, ainsi que les "
                  "coordonn\u00e9es du notaire r\u00e9dacteur de l\u2019acte de vente.")
    cursor, _, _ = _mandat_paragraph(c, oblige_txt, cursor, size=7.5, leading=9.5)
    cursor -= 3 * mm

    # Clause penale en encadre
    penale_txt = ("EN CAS DE MANQUEMENT \u00c0 L\u2019UNE OU L\u2019AUTRE DE CES INTERDICTIONS ou "
                  "OBLIGATIONS, LE MANDANT S\u2019OBLIGE EXPRESS\u00c9MENT ET DE MANI\u00c8RE "
                  "IRR\u00c9VOCABLE \u00c0 VERSER AU MANDATAIRE UNE SOMME \u00c9GALE AU MONTANT "
                  "TOTAL, TVA INCLUSE, DE LA R\u00c9MUN\u00c9RATION PR\u00c9VUE AUX PR\u00c9SENTES ET "
                  "CE, \u00c0 TITRE D\u2019INDEMNIT\u00c9 FORFAITAIRE ET D\u00c9FINITIVE.")
    style_pen = ParagraphStyle("pen", fontName="Helvetica-Bold", fontSize=7.5,
                               leading=10, textColor=colors.HexColor("#991b1b"),
                               borderColor=colors.HexColor("#991b1b"), borderWidth=1,
                               borderPadding=6, backColor=colors.HexColor("#FEF2F2"))
    pp = Paragraph(penale_txt, style_pen)
    _, pph = pp.wrap(CW, 40 * mm)
    pp.drawOn(c, ML, cursor - pph)
    cursor -= pph + 8 * mm

    _mandat_paraphes(c, 4, 7)
    c.showPage()

    # ══════════════════════════════════════════════════════════════════════════
    # PAGE 6 — ACTIONS + REDDITION + TRACFIN + NON-DISCRIM (Page 5 sur 7)
    # ══════════════════════════════════════════════════════════════════════════
    _header(c, sub=f"N\u00b0 {num}", prefix=f"MANDAT {type_label} DE VENTE")
    cursor = PAGE_H - HEADER_H - SP_AFTER_HEADER

    # ── ACTIONS COMMERCIALES ──
    cursor = _mandat_section(c, "ACTIONS COMMERCIALES", cursor)
    actions_txt = ("Le MANDATAIRE s\u2019engage \u00e0 r\u00e9aliser \u00e0 ses frais les actions de "
                   "communication suivantes : diffusion de l\u2019annonce sur les portails "
                   "immobiliers professionnels, site internet de l\u2019agence, r\u00e9seaux sociaux, "
                   "vitrine de l\u2019agence, prospection cibl\u00e9e aupr\u00e8s de sa base acqu\u00e9reurs.")
    cursor, _, _ = _mandat_paragraph(c, actions_txt, cursor, size=8, leading=10.5)
    cursor -= 4 * mm

    # ── REDDITION DE COMPTES ──
    cursor = _mandat_section(c, "REDDITION DE COMPTES", cursor)
    reddition_txt = ("Le MANDATAIRE rendra compte r\u00e9guli\u00e8rement au MANDANT des actions "
                     "entreprises et de leur r\u00e9sultat, notamment du nombre de contacts, "
                     "de visites et du retour des acqu\u00e9reurs potentiels.")
    cursor, _, _ = _mandat_paragraph(c, reddition_txt, cursor, size=8, leading=10.5)
    cursor -= 4 * mm

    # ── TRACFIN ──
    cursor = _mandat_section(c, "INFORMATIONS TRACFIN", cursor)
    tracfin_txt = ("Le MANDATAIRE informe le MANDANT qu\u2019il est tenu de se conformer aux "
                   "dispositions de l\u2019article L. 562-1 du code mon\u00e9taire et financier, "
                   "relatives au traitement du renseignement et \u00e0 l\u2019action contre les circuits "
                   "financiers clandestins et d\u00e9di\u00e9es \u00e0 la lutte contre le blanchiment d\u2019argent.")
    cursor, _, _ = _mandat_paragraph(c, tracfin_txt, cursor, size=8, leading=10.5)
    cursor -= 4 * mm

    # ── NON-DISCRIMINATION ──
    cursor = _mandat_section(c, "ENGAGEMENT DE NON-DISCRIMINATION", cursor)
    discrim_txt = ("Constitue une discrimination toute distinction op\u00e9r\u00e9e entre les personnes "
                   "sur le fondement de leur origine, de leur sexe, de leur situation de famille, "
                   "de leur grossesse, de leur apparence physique, de la particuli\u00e8re "
                   "vuln\u00e9rabilit\u00e9 r\u00e9sultant de leur situation \u00e9conomique, apparente ou connue "
                   "de son auteur, de leur patronyme, de leur lieu de r\u00e9sidence, de leur \u00e9tat "
                   "de sant\u00e9, de leur perte d\u2019autonomie, de leur handicap, de leurs "
                   "caract\u00e9ristiques g\u00e9n\u00e9tiques, de leurs m\u0153urs, de leur orientation sexuelle, "
                   "de leur identit\u00e9 de genre, de leur \u00e2ge, de leurs opinions politiques, de leurs "
                   "activit\u00e9s syndicales, de leur qualit\u00e9 de lanceur d\u2019alerte, de la "
                   "r\u00e9glementation, de leur capacit\u00e9 \u00e0 s\u2019exprimer dans une langue autre que "
                   "le fran\u00e7ais, de leur appartenance ou de leur non-appartenance, vraie ou "
                   "suppos\u00e9e, \u00e0 une ethnie, une Nation, une pr\u00e9tendue race ou une religion "
                   "d\u00e9termin\u00e9e.")
    cursor, _, _ = _mandat_paragraph(c, discrim_txt, cursor, size=7.5, leading=9.5)
    cursor -= 1 * mm

    discrim2 = ("Le MANDATAIRE informe le MANDANT que toute discrimination commise \u00e0 "
                "l\u2019\u00e9gard d\u2019une personne est punie p\u00e9nalement. En cons\u00e9quence, les parties "
                "prennent l\u2019engagement expres de n\u2019opposer \u00e0 un candidat \u00e0 l\u2019acquisition "
                "des pr\u00e9sents biens aucun refus fond\u00e9 sur un motif discriminatoire. "
                "Par ailleurs, le MANDANT s\u2019interdit express\u00e9ment de donner au MANDATAIRE "
                "des directives et consignes, verbales ou \u00e9crites, tendant \u00e0 refuser la vente "
                "pour des motifs discriminatoires.")
    cursor, _, _ = _mandat_paragraph(c, discrim2, cursor, size=7.5, leading=9.5)

    _mandat_paraphes(c, 5, 7)
    c.showPage()

    # ══════════════════════════════════════════════════════════════════════════
    # PAGE 7 — RGPD + DOMICILE + SIGNATURES (Page 6 sur 7)
    # ══════════════════════════════════════════════════════════════════════════
    _header(c, sub=f"N\u00b0 {num}", prefix=f"MANDAT {type_label} DE VENTE")
    cursor = PAGE_H - HEADER_H - SP_AFTER_HEADER

    # ── DONNEES PERSONNELLES ──
    cursor = _mandat_section(c, "COLLECTE ET EXPLOITATION DES DONN\u00c9ES PERSONNELLES", cursor)
    rgpd_txt = ("Conform\u00e9ment au R\u00e8glement G\u00e9n\u00e9ral sur la Protection des Donn\u00e9es (RGPD) "
                "et \u00e0 la loi Informatique et Libert\u00e9s, les donn\u00e9es personnelles collect\u00e9es dans "
                "le cadre du pr\u00e9sent mandat sont n\u00e9cessaires \u00e0 son ex\u00e9cution. Elles ne seront "
                "communiqu\u00e9es qu\u2019aux seuls professionnels intervenant dans la r\u00e9alisation de la "
                "vente. Le MANDANT dispose d\u2019un droit d\u2019acc\u00e8s, de rectification et de suppression "
                "de ses donn\u00e9es en \u00e9crivant \u00e0 Barbier Immobilier, 2 place Albert Einstein, 56000 Vannes.")
    cursor, _, _ = _mandat_paragraph(c, rgpd_txt, cursor, size=7.5, leading=9.5)
    cursor -= 4 * mm

    # ── ELECTION DE DOMICILE ──
    cursor = _mandat_section(c, "\u00c9LECTION DE DOMICILE", cursor)
    domicile_txt = ("Les parties soussign\u00e9es font \u00e9lection de domicile chacune \u00e0 leur adresse "
                    "respective stipul\u00e9e en t\u00eate du pr\u00e9sent mandat.")
    cursor, _, _ = _mandat_paragraph(c, domicile_txt, cursor, size=8, leading=10.5)
    cursor -= 8 * mm

    # ── SIGNATURES ──
    cursor = _mandat_section(c, "SIGNATURES", cursor)

    sig_w = CW / 2 - 5 * mm
    sig_h = 35 * mm
    left_x = ML
    right_x = ML + CW / 2 + 5 * mm

    # Left: Mandant
    _rrect(c, left_x, cursor - sig_h, sig_w, sig_h, r=3, stroke=GRAY_BDR)
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(TEAL_DARK)
    c.drawString(left_x + 4 * mm, cursor - 5 * mm, "Le Mandant")
    c.setFont("Helvetica", 7)
    c.setFillColor(GRAY_MID)
    c.drawString(left_x + 4 * mm, cursor - 10 * mm, str(mandant_name or ""))
    c.drawString(left_x + 4 * mm, cursor - sig_h + 4 * mm,
                 "Fait \u00e0 ________________  le ________________")

    # Right: Mandataire
    _rrect(c, right_x, cursor - sig_h, sig_w, sig_h, r=3, stroke=GRAY_BDR)
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(TEAL_DARK)
    c.drawString(right_x + 4 * mm, cursor - 5 * mm, "Le Mandataire")
    c.setFont("Helvetica", 7)
    c.setFillColor(GRAY_MID)
    c.drawString(right_x + 4 * mm, cursor - 10 * mm, "Barbier Immobilier")
    c.drawString(right_x + 4 * mm, cursor - 15 * mm, str(nego))
    c.drawString(right_x + 4 * mm, cursor - sig_h + 4 * mm,
                 "Fait \u00e0 Vannes  le ________________")

    _mandat_paraphes(c, 6, 7)

    # ══════════════════════════════════════════════════════════════════════════
    # PAGE 8 — REGISTRE DES MANDATS (Page 7 sur 7)
    # ══════════════════════════════════════════════════════════════════════════
    c.showPage()
    _header(c, sub=f"N\u00b0 {num}", prefix=f"MANDAT {type_label} DE VENTE")
    cursor = PAGE_H - HEADER_H - SP_AFTER_HEADER

    cursor = _mandat_section(c, "INSCRIPTION AU REGISTRE DES MANDATS", cursor)
    registre_txt = ("Le pr\u00e9sent mandat sera inscrit au registre des mandats du MANDATAIRE "
                    "sous le num\u00e9ro figurant en t\u00eate du pr\u00e9sent acte, conform\u00e9ment \u00e0 "
                    "l\u2019article 6 de la loi n\u00b0 70-9 du 2 janvier 1970 et aux articles 72 et 73 "
                    "du d\u00e9cret n\u00b0 72-678 du 20 juillet 1972.")
    cursor, _, _ = _mandat_paragraph(c, registre_txt, cursor, size=8, leading=10.5)
    cursor -= 6 * mm

    # ── LOIS APPLICABLES ──
    cursor = _mandat_section(c, "LOIS APPLICABLES", cursor)
    lois_txt = ("Le pr\u00e9sent mandat est r\u00e9gi par les lois n\u00b0 70-9 du 2 janvier 1970 "
                "(loi Hoguet) et son d\u00e9cret d\u2019application n\u00b0 72-678 du 20 juillet 1972, "
                "la loi n\u00b0 2014-366 du 24 mars 2014 (loi ALUR) et la loi n\u00b0 89-462 du "
                "6 juillet 1989.")
    cursor, _, _ = _mandat_paragraph(c, lois_txt, cursor, size=8, leading=10.5)
    cursor -= 6 * mm

    # Mention manuscrite
    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(TEAL_DARK)
    c.drawString(ML, cursor, "Mention manuscrite obligatoire :")
    cursor -= 6 * mm
    c.setFont("Helvetica", 8)
    c.setFillColor(GRAY_MID)
    mention = ("\u00ab Lu et approuv\u00e9. Le mandant reconna\u00eet avoir re\u00e7u un exemplaire "
               "du pr\u00e9sent mandat au moment de sa signature. \u00bb")
    cursor, _, _ = _mandat_paragraph(c, f"<i>{mention}</i>", cursor, size=8, leading=10.5)
    cursor -= 10 * mm

    # Zone d'ecriture manuscrite
    box_h = 30 * mm
    _rrect(c, ML, cursor - box_h, CW, box_h, r=3, stroke=GRAY_BDR)
    c.setFont("Helvetica", 7)
    c.setFillColor(GRAY_MID)
    c.drawString(ML + 4 * mm, cursor - 5 * mm, "Mention manuscrite du Mandant :")

    _mandat_paraphes(c, 7, 7)
    c.showPage()
    c.save()
    buf.seek(0)
    return buf


@app.route("/mandat", methods=["POST"])
def mandat():
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "Body JSON manquant"}), 400
    num = body.get("num_mandat", "mandat")
    type_m = body.get("type_mandat", "Simple")
    try:
        pdf_buf = generate_mandat_pdf(body)
        fname = f"Mandat_{type_m}_{num}.pdf"
        return send_file(pdf_buf, mimetype="application/pdf",
                         as_attachment=True, download_name=fname)
    except Exception as e:
        app.logger.error("Mandat error: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# AVIS DE VALEUR PDF (2 pages — document confidentiel)
# ---------------------------------------------------------------------------
def _avis_header(c, ref, ville, nego):
    """Custom header for avis de valeur: logo icon left + title right."""
    c.saveState()
    # Logo — small icon only, avoids text overlap
    try:
        lh = 10 * mm
        lw = lh * (488 / 662)  # ~7.4mm wide
        c.drawImage(_ir(LOGO_B64), ML, PAGE_H - 5 * mm - lh, width=lw, height=lh, mask="auto")
    except Exception:
        pass
    # Title right-aligned
    c.setFont("Helvetica-Bold", 16)
    c.setFillColor(TEAL_DARK)
    c.drawRightString(PAGE_W - MR, PAGE_H - 11 * mm, "AVIS DE VALEUR PROFESSIONNEL")
    c.setFont("Helvetica", 8)
    c.setFillColor(GRAY_MID)
    subtitle = "R\u00e9f. " + str(ref) + " \u00b7 " + str(ville) + " \u00b7 " + str(nego)
    c.drawRightString(PAGE_W - MR, PAGE_H - 17 * mm, subtitle)
    # Separator line at fixed 20mm from top
    c.setStrokeColor(GRAY_BDR)
    c.setLineWidth(0.5)
    c.line(ML, PAGE_H - 20 * mm, PAGE_W - MR, PAGE_H - 20 * mm)
    c.restoreState()


def _avis_footer(c, ref, page_n, total):
    """Confidential footer for avis de valeur."""
    c.saveState()
    c.setFont("Helvetica", 6.5)
    c.setFillColor(GRAY_MID)
    c.drawString(ML, 4 * mm,
                 "DOCUMENT CONFIDENTIEL \u00b7 Barbier Immobilier \u00b7 " + str(ref))
    c.drawRightString(PAGE_W - MR, 4 * mm,
                      "Page " + str(page_n) + " / " + str(total))
    c.setStrokeColor(GRAY_BDR)
    c.setLineWidth(0.3)
    c.line(ML, 10 * mm, PAGE_W - MR, 10 * mm)
    c.restoreState()


def _avis_id_cell(c, label, value, x, y, w):
    """Draw one identification cell (label + value) inside a bordered box."""
    c.setStrokeColor(GRAY_BDR)
    c.setLineWidth(0.3)
    c.rect(x, y, w, 14 * mm, fill=0, stroke=1)
    c.setFont("Helvetica", 6.5)
    c.setFillColor(GRAY_MID)
    c.drawString(x + 3 * mm, y + 10 * mm, str(label))
    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(GRAY_DARK)
    val = str(value) if value else "\u2014"
    if len(val) > 30:
        val = val[:28] + "\u2026"
    c.drawString(x + 3 * mm, y + 3.5 * mm, val)


def _avis_price_box(c, label, value, x, y, w, h, highlight=False):
    """Draw a price box for valeur basse/retenue/haute."""
    if highlight:
        _rrect(c, x, y, w, h, r=4, fill=TEAL_DARK)
        # "RECOMMANDE" badge
        badge_w = 32 * mm
        badge_h = 5.5 * mm
        bx = x + (w - badge_w) / 2
        by = y + h - badge_h - 2 * mm
        _rrect(c, bx, by, badge_w, badge_h, r=2.5, fill=ORANGE)
        c.setFont("Helvetica-Bold", 6.5)
        c.setFillColor(WHITE)
        c.drawCentredString(bx + badge_w / 2, by + 1.5 * mm, "\u2605 RECOMMAND\u00c9")
        # Label
        c.setFont("Helvetica", 7)
        c.setFillColor(colors.HexColor("#B0D4E0"))
        c.drawCentredString(x + w / 2, y + h - 14 * mm, label)
        # Price
        c.setFont("Helvetica-Bold", 18)
        c.setFillColor(WHITE)
        c.drawCentredString(x + w / 2, y + h / 2 - 10 * mm, _pfmt(value))
    else:
        _rrect(c, x, y, w, h, r=4, stroke=GRAY_BDR)
        c.setFont("Helvetica", 7)
        c.setFillColor(GRAY_MID)
        c.drawCentredString(x + w / 2, y + h - 8 * mm, label)
        c.setFont("Helvetica-Bold", 16)
        c.setFillColor(GRAY_DARK)
        c.drawCentredString(x + w / 2, y + h / 2 - 6 * mm, _pfmt(value))


def generate_avis_valeur_pdf(d):
    """Generate Avis de Valeur Professionnel PDF (2-3 pages).
    v5.24 — 10pt text, fix annonce gap, more space before analyse
    """
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=A4)

    ref = d.get("reference", "")
    ville = d.get("ville", "Vannes")
    nego = d.get("negociateur", "Barbier Immobilier")
    type_bien = d.get("type_bien", "")
    adresse = d.get("adresse", "")
    surface = d.get("surface") or 0
    surface_t = d.get("surface_terrain") or 0
    ref_cad = d.get("ref_cadastrale", "")
    etat = d.get("etat_bien", "")
    prix_min = d.get("prix_estime_min") or 0
    prix_ret = d.get("prix_retenu") or d.get("prix") or 0
    prix_max = d.get("prix_estime_max") or 0
    avis_texte = d.get("avis_valeur", "")
    annonce = d.get("description", "")

    # ── Layout constants ────────────────────────────────────────────────
    CONTENT_START = PAGE_H - 28 * mm
    BLOC_GAP = 8 * mm
    FOOTER_ZONE = 18 * mm

    # ── Text styles ─────────────────────────────────────────────────────
    # Analyse (page 1, espace limité) — compact 8pt
    STYLE_ANALYSE = ParagraphStyle("avAna", fontName="Helvetica", fontSize=8,
                                   leading=10.5, textColor=GRAY_DARK)
    STYLE_ANALYSE_TITLE = ParagraphStyle("avAnaT", fontName="Helvetica-Bold", fontSize=8.5,
                                         leading=11, textColor=TEAL_DARK, spaceBefore=2,
                                         spaceAfter=0.5)
    # Annonce (page 2, plein espace) — 9pt lisible
    STYLE_BODY = ParagraphStyle("avBody", fontName="Helvetica", fontSize=9,
                                leading=12, textColor=GRAY_DARK)
    STYLE_TITLE = ParagraphStyle("avTitle", fontName="Helvetica-Bold", fontSize=9.5,
                                 leading=12.5, textColor=TEAL_DARK, spaceBefore=3,
                                 spaceAfter=1)

    # Check for cadastre photos
    photos = d.get("photos") or []
    cadastre_imgs = []
    for p in photos:
        if _is_plan(p):
            img = _fetch_photo(p)
            if img:
                cadastre_imgs.append(img)
    has_cadastre = len(cadastre_imgs) > 0
    total_pages = 2 + (1 if has_cadastre else 0)

    # ═══════════════════════════════════════════════════════════════════════
    # PAGE 1: Identification + Carte/Prix + Analyse de marché
    # ═══════════════════════════════════════════════════════════════════════
    _avis_header(c, ref, ville, nego)
    _avis_footer(c, ref, 1, total_pages)
    cursor = CONTENT_START

    # ── 01 — IDENTIFICATION DU BIEN ─────────────────────────────────────
    _sec(c, "01 \u2014 IDENTIFICATION DU BIEN", ML, cursor)
    cursor -= SEC_H + 3 * mm

    cw3 = CW / 3
    cell_h = 13 * mm
    _avis_id_cell(c, "Type de bien", type_bien, ML, cursor - cell_h, cw3)
    _avis_id_cell(c, "Surface habitable", (str(surface) + " m\u00b2") if surface else None, ML + cw3, cursor - cell_h, cw3)
    _avis_id_cell(c, "Surface terrain", (str(surface_t) + " m\u00b2") if surface_t else None, ML + 2 * cw3, cursor - cell_h, cw3)
    cursor -= cell_h
    _avis_id_cell(c, "Adresse compl\u00e8te", adresse, ML, cursor - cell_h, cw3)
    _avis_id_cell(c, "R\u00e9f. cadastrale", ref_cad, ML + cw3, cursor - cell_h, cw3)
    _avis_id_cell(c, "\u00c9tat g\u00e9n\u00e9ral", etat, ML + 2 * cw3, cursor - cell_h, cw3)
    cursor -= cell_h + BLOC_GAP

    # ── 02 — LOCALISATION & ESTIMATION DE VALEUR ────────────────────────
    _sec(c, "02 \u2014 LOCALISATION & ESTIMATION DE VALEUR", ML, cursor)
    cursor -= SEC_H + 3 * mm

    map_w = CW * 0.58
    map_h = 68 * mm
    box_col_x = ML + map_w + 5 * mm
    box_col_w = CW - map_w - 5 * mm

    map_img = None
    try:
        geo = _geocode(adresse, ville)
        if geo and geo[0]:
            poi = _get_poi_osm(*geo)
            gimg, glat, glon = _google_static_map(adresse, ville, poi)
            if gimg:
                map_img = gimg
            else:
                oimg, olat, olon = _osm_map(adresse, ville)
                if oimg:
                    map_img = oimg
    except Exception:
        pass

    if map_img:
        img_buf = io.BytesIO()
        map_img.save(img_buf, format="JPEG", quality=90)
        img_buf.seek(0)
        c.drawImage(ImageReader(img_buf), ML, cursor - map_h, width=map_w, height=map_h,
                     preserveAspectRatio=True, anchor="nw")
    else:
        _rrect(c, ML, cursor - map_h, map_w, map_h, r=4, fill=GRAY_LIGHT, stroke=GRAY_BDR)
        c.setFont("Helvetica", 8); c.setFillColor(GRAY_MID)
        c.drawCentredString(ML + map_w / 2, cursor - map_h / 2, "Carte non disponible")

    # Price boxes
    box_h = 21 * mm
    box_gap = 3 * mm
    _avis_price_box(c, "VALEUR BASSE", prix_min,
                    box_col_x, cursor - box_h, box_col_w, box_h, highlight=False)
    _avis_price_box(c, "VALEUR RETENUE", prix_ret,
                    box_col_x, cursor - 2 * box_h - box_gap, box_col_w, box_h, highlight=True)
    _avis_price_box(c, "VALEUR HAUTE", prix_max,
                    box_col_x, cursor - 3 * box_h - 2 * box_gap, box_col_w, box_h, highlight=False)

    # More space between price boxes and analyse title
    cursor -= map_h + 10 * mm

    # ── 03 — ANALYSE DE MARCHÉ ──────────────────────────────────────────
    _sec(c, "03 \u2014 ANALYSE DE MARCH\u00c9", ML, cursor)
    cursor -= SEC_H + 3 * mm

    if avis_texte:
        avis_clean = _clean(avis_texte)
        lines = avis_clean.split("\n")
        filtered = []
        for line in lines:
            ls = line.strip()
            if not ls:
                continue
            ll = ls.lower()
            if ll.startswith("avis de valeur") or ll.startswith("analyse de march"):
                continue
            if "barbier immobilier" in ll and "expert immobilier" in ll:
                continue
            filtered.append(ls)

        title_keys = ["synth\u00e8se", "m\u00e9thodologie", "\u00e9valuation",
                      "recommandation", "contexte", "analyse du march",
                      "points forts", "m\u00e9thode", "conclusion",
                      "points de vigilance"]

        paras = []
        for line in filtered:
            is_title = any(line.lower().startswith(k) for k in title_keys)
            # Convert markdown **bold** → <b>bold</b> for reportlab
            line_html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line)
            # Convert markdown bullet lines (- text, • text, ✓ text, △ text)
            for bullet in ("- ", "• ", "✓ ", "△ "):
                if line_html.startswith(bullet):
                    line_html = "•\u00a0\u00a0" + line_html[len(bullet):]
                    break
            p = Paragraph(line_html, STYLE_ANALYSE_TITLE if is_title else STYLE_ANALYSE)
            _, ph = p.wrap(CW - 10 * mm, 400 * mm)
            paras.append((p, ph))

        # Box: text fills from top, tight padding
        text_total = sum(ph for _, ph in paras) + len(paras) * 0.3 * mm
        box_inner = text_total + 3 * mm + 6 * mm
        max_box = cursor - FOOTER_ZONE
        box_h_a = min(box_inner, max_box)
        box_y = cursor - box_h_a

        _rrect(c, ML, box_y, CW, box_h_a, r=4, stroke=GRAY_BDR)

        ty = cursor - 3 * mm
        stop_y = box_y + 6 * mm
        for p, ph in paras:
            if ty - ph < stop_y:
                break
            p.drawOn(c, ML + 5 * mm, ty - ph)
            ty -= ph + 0.3 * mm

        c.setFont("Helvetica-Bold", 5.5); c.setFillColor(TEAL_DARK)
        c.drawString(ML + 5 * mm, box_y + 2 * mm,
                     "Barbier Immobilier \u2014 Expert immobilier commercial Morbihan \u2014 02.97.47.11.11")

    c.showPage()

    # ═══════════════════════════════════════════════════════════════════════
    # PAGE 2: Annonce commerciale + Signatures & Validation
    # ═══════════════════════════════════════════════════════════════════════
    _avis_header(c, ref, ville, nego)
    _avis_footer(c, ref, 2, total_pages)

    # Bottom: disclaimer
    DISC_H = 14 * mm
    disc_y = FOOTER_ZONE
    _rrect(c, ML, disc_y, CW, DISC_H, r=3, fill=GRAY_LIGHT)
    disclaimer = (
        "Document \u00e9tabli par Barbier Immobilier, agent immobilier titulaire de la carte "
        "professionnelle Transactions sur immeubles et fonds de commerce. Ce document est "
        "\u00e9tabli \u00e0 titre indicatif et ne constitue pas une expertise au sens de la norme MRICS. "
        "Les valeurs sont susceptibles d\u2019\u00e9voluer en fonction des conditions du march\u00e9."
    )
    style_disc = ParagraphStyle("disc", fontName="Helvetica", fontSize=6, leading=7.5,
                                textColor=GRAY_MID)
    pd = Paragraph(disclaimer, style_disc)
    pd.wrap(CW - 10 * mm, DISC_H - 3 * mm)
    pd.drawOn(c, ML + 5 * mm, disc_y + 2.5 * mm)

    # Signatures
    SIG_GAP = 6 * mm
    SIG_H = 24 * mm
    sig_box_bottom = disc_y + DISC_H + SIG_GAP
    sig_top = sig_box_bottom + SIG_H
    sig_bar_y = sig_top + 3 * mm
    _sec(c, "05 \u2014 SIGNATURES & VALIDATION", ML, sig_bar_y)

    sig_w = CW / 2 - 4 * mm
    _rrect(c, ML, sig_box_bottom, sig_w, SIG_H, r=3, stroke=GRAY_BDR)
    c.setFont("Helvetica", 6.5); c.setFillColor(GRAY_MID)
    c.drawString(ML + 4 * mm, sig_top - 5 * mm, "N\u00c9GOCIATEUR MANDATAIRE")
    c.setFont("Helvetica-Bold", 9); c.setFillColor(GRAY_DARK)
    c.drawString(ML + 4 * mm, sig_top - 11 * mm, str(nego))
    c.setFont("Helvetica", 7); c.setFillColor(GRAY_MID)
    c.drawString(ML + 4 * mm, sig_top - 17 * mm, "Signature :")
    c.drawString(ML + 4 * mm, sig_top - 22 * mm, "Date :")

    rx = ML + sig_w + 8 * mm
    _rrect(c, rx, sig_box_bottom, sig_w, SIG_H, r=3, stroke=GRAY_BDR)
    c.setFont("Helvetica", 6.5); c.setFillColor(GRAY_MID)
    c.drawString(rx + 4 * mm, sig_top - 5 * mm, "DIRECTEUR \u2014 BARBIER IMMOBILIER")
    c.setFont("Helvetica-Bold", 9); c.setFillColor(GRAY_DARK)
    c.drawString(rx + 4 * mm, sig_top - 11 * mm, "Laurent Baradu")
    c.setFont("Helvetica", 7); c.setFillColor(GRAY_MID)
    c.drawString(rx + 4 * mm, sig_top - 17 * mm, "Signature :")
    c.drawString(rx + 4 * mm, sig_top - 22 * mm, "Date :")

    # ── 04 — ANNONCE COMMERCIALE ────────────────────────────────────────
    content_bottom = sig_bar_y + SEC_H + BLOC_GAP
    _sec(c, "04 \u2014 ANNONCE COMMERCIALE", ML, CONTENT_START)
    cursor = CONTENT_START - SEC_H - 3 * mm

    if annonce:
        annonce_clean = _clean(annonce)
        ann_lines = annonce_clean.split("\n")

        # Filter and clean — strip leading/trailing blanks
        ann_filtered = []
        for aline in ann_lines:
            al = aline.strip().lower()
            if al.startswith("annonce immobili") or al.startswith("annonce commerci"):
                continue
            ann_filtered.append(aline)
        # Strip leading/trailing empty lines
        while ann_filtered and not ann_filtered[0].strip():
            ann_filtered.pop(0)
        while ann_filtered and not ann_filtered[-1].strip():
            ann_filtered.pop()

        # Bold key lines for attractive formatting
        ann_bold_keys = ["\u00e0 vendre", "prix de vente", "description du bien",
                         "atouts", "surface :", "adresse :", "activit",
                         "n\u00e9gociateur", "taxe fonci", "loyer",
                         "ne manquez pas", "renouvellement", "dur\u00e9e :",
                         "paiement", "destination"]

        # Build paragraphs one by one (like analyse) for precise control
        ann_paras = []
        for aline in ann_filtered:
            al = aline.strip()
            if not al:
                continue  # skip blank lines, just add a small gap
            ll = al.lower()
            is_bold = any(ll.startswith(k) for k in ann_bold_keys) or al.endswith(":")
            style = STYLE_TITLE if is_bold else STYLE_BODY
            p = Paragraph(al, style)
            _, ph = p.wrap(CW - 10 * mm, 400 * mm)
            ann_paras.append((p, ph))

        # Box from cursor to content_bottom
        box_h_ann = cursor - content_bottom
        _rrect(c, ML, content_bottom, CW, box_h_ann, r=4, stroke=GRAY_BDR)

        # Draw paragraphs top-down
        ty = cursor - 4 * mm
        stop_y = content_bottom + 3 * mm
        for p, ph in ann_paras:
            if ty - ph < stop_y:
                break
            p.drawOn(c, ML + 5 * mm, ty - ph)
            ty -= ph + 0.3 * mm
    else:
        no_ann_h = 30 * mm
        _rrect(c, ML, cursor - no_ann_h, CW, no_ann_h, r=4, fill=GRAY_LIGHT, stroke=GRAY_BDR)
        c.setFont("Helvetica", 8); c.setFillColor(GRAY_MID)
        c.drawCentredString(ML + CW / 2, cursor - no_ann_h / 2, "Annonce commerciale non disponible")

    c.showPage()

    # ═══════════════════════════════════════════════════════════════════════
    # PAGE 3 (optional): Plan cadastral
    # ═══════════════════════════════════════════════════════════════════════
    if has_cadastre:
        _avis_header(c, ref, ville, nego)
        _avis_footer(c, ref, 3, total_pages)

        cursor = CONTENT_START
        _sec(c, "06 \u2014 PLAN CADASTRAL", ML, cursor)
        cursor -= SEC_H + 4 * mm

        info_h = 16 * mm if (ref_cad or surface_t) else 0
        zone_bot = FOOTER_ZONE + info_h + 6 * mm
        available_h = cursor - zone_bot
        gap_y = 4 * mm

        n = min(len(cadastre_imgs), 2)
        ph_each = (available_h - (n - 1) * gap_y) / n

        for i in range(n):
            py = cursor - (i + 1) * ph_each - i * gap_y
            img = cadastre_imgs[i]
            try:
                iw, ih = img.getSize()
                ir_ratio = iw / ih if ih > 0 else 1
                target_r = CW / ph_each
                if ir_ratio > target_r:
                    dw = CW; dh = CW / ir_ratio
                else:
                    dh = ph_each; dw = ph_each * ir_ratio
                dx = ML + (CW - dw) / 2
                dy = py + (ph_each - dh) / 2
                c.setFillColor(WHITE)
                c.setStrokeColor(colors.HexColor("#CCCCCC"))
                c.setLineWidth(0.5)
                c.roundRect(ML, py, CW, ph_each, 3 * mm, fill=1, stroke=1)
                c.drawImage(img, dx, dy, width=dw, height=dh, mask="auto")
            except Exception as e:
                app.logger.error("Avis cadastre draw: %s", e)
                c.setFillColor(GRAY_LIGHT)
                c.roundRect(ML, py, CW, ph_each, 3 * mm, fill=1, stroke=0)

        if ref_cad or surface_t:
            info_box_y = FOOTER_ZONE + 8 * mm
            c.setFillColor(colors.HexColor("#EBF0F8"))
            c.roundRect(ML, info_box_y, CW, 12 * mm, 1.5 * mm, fill=1, stroke=0)
            c.setFillColor(TEAL_DARK)
            c.setFont("Helvetica-Bold", 8)
            infos = []
            if ref_cad:
                infos.append("R\u00e9f. cadastrale : " + str(ref_cad))
            if surface_t:
                infos.append("Surface terrain : " + str(surface_t) + " m\u00b2")
            c.drawString(ML + 5 * mm, info_box_y + 3.5 * mm, "  \u00b7  ".join(infos))

        c.showPage()

    c.save()
    buf.seek(0)
    return buf



@app.route("/avis-valeur", methods=["POST"])
def avis_valeur():
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "Body JSON manquant"}), 400
    ref = body.get("reference", "bien")
    try:
        pdf_buf = generate_avis_valeur_pdf(body)
        fname = f"Avis_valeur_{ref}.pdf"
        return send_file(pdf_buf, mimetype="application/pdf",
                         as_attachment=True, download_name=fname)
    except Exception as e:
        app.logger.error("Avis valeur error: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# URBANISME (Cadastre + PLU + Servitudes)
# ---------------------------------------------------------------------------
def _geocode_urba(adresse, code_postal, ville):
    """Geocode for urbanisme. Returns (lon, lat, code_insee) or None."""
    q = f"{adresse} {ville}".strip()
    if not q:
        return None
    params = {"q": q, "limit": 1}
    if code_postal:
        params["postcode"] = str(code_postal)
    try:
        r = requests.get("https://api-adresse.data.gouv.fr/search/", params=params, timeout=10)
        r.raise_for_status()
        features = r.json().get("features", [])
        if not features:
            return None
        f = features[0]
        lon, lat = f["geometry"]["coordinates"]
        code_insee = f["properties"].get("citycode", "")
        return lon, lat, code_insee
    except Exception as e:
        app.logger.error("Geocode urba error: %s", e)
        return None


def _parse_ref_cadastrale(ref):
    """Parse '000 AB 1234' or 'AB 1234' into (com_abs, section, numero)."""
    if not ref:
        return None, None, None
    parts = ref.strip().split()
    if len(parts) == 3:
        return parts[0], parts[1].upper(), parts[2]
    elif len(parts) == 2:
        return "000", parts[0].upper(), parts[1]
    elif len(parts) == 1:
        # Try to parse "AB1234"
        m = re.match(r"([A-Z]{1,2})(\d+)", ref.strip().upper())
        if m:
            return "000", m.group(1), m.group(2)
    return None, None, None


def _get_parcelle_geometry(code_insee, section, numero):
    """Fetch parcel GeoJSON geometry from API Carto cadastre."""
    try:
        params = {"code_insee": code_insee, "section": section, "numero": numero}
        r = requests.get("https://apicarto.ign.fr/api/cadastre/parcelle",
                         params=params, timeout=15)
        r.raise_for_status()
        features = r.json().get("features", [])
        if features:
            return features[0]["geometry"]
    except Exception as e:
        app.logger.error("Cadastre parcelle error: %s", e)
    return None


def _point_to_micro_polygon(geom):
    """Convert a Point geometry to a tiny polygon (~20m buffer) for API Carto GPU
    which does not support Point geometries."""
    if geom.get("type") == "Point":
        lon, lat = geom["coordinates"]
        d = 0.001  # ~111m — minimum for API Carto GPU to return results
        return {
            "type": "Polygon",
            "coordinates": [[[lon - d, lat - d], [lon + d, lat - d],
                             [lon + d, lat + d], [lon - d, lat + d],
                             [lon - d, lat - d]]]
        }
    return geom


def _get_plu_zone(geom):
    """Query API Carto GPU for PLU zone info. geom is a GeoJSON geometry dict."""
    try:
        import json as _j
        query_geom = _point_to_micro_polygon(geom)
        geom_str = _j.dumps(query_geom)
        r = requests.get("https://apicarto.ign.fr/api/gpu/zone-urba",
                         params={"geom": geom_str}, timeout=20)
        r.raise_for_status()
        features = r.json().get("features", [])
        if not features:
            return None
        # Pick the most specific zone (smallest area or first non-Uj)
        best = features[0]
        for f in features:
            p = f.get("properties", {})
            if p.get("typezone") in ("U", "AU", "A", "N") and p.get("libelle", "").lower() != "uj":
                best = f
                break
        props = best["properties"]
        return {
            "zone_plu": props.get("libelle", ""),
            "libelong": props.get("libelong", ""),
            "typezone": props.get("typezone", ""),
            "destdomi": props.get("destdomi", ""),
            "url_reglement": props.get("urlfic", ""),
        }
    except Exception as e:
        app.logger.error("GPU zone-urba error: %s", e)
        return None


SUP_LABELS = {
    "AC1": "Monuments historiques",
    "AC2": "Sites inscrits et class\u00e9s",
    "AC4": "Zone de protection du patrimoine",
    "PM1": "Plan de pr\u00e9vention des risques naturels",
    "PM3": "Plan de pr\u00e9vention des risques technologiques",
    "PT2": "Servitudes transmissions radio\u00e9lectriques",
    "T1": "Servitudes voies ferr\u00e9es",
    "EL7": "Servitudes d\u2019alignement",
    "I4": "Canalisations de gaz",
}


def _get_servitudes(geom):
    """Query API Carto GPU for servitudes d'utilité publique."""
    import json as _j
    query_geom = _point_to_micro_polygon(geom)
    geom_str = _j.dumps(query_geom)
    servitudes = set()
    for suffix in ["assiette-sup-s", "assiette-sup-l", "assiette-sup-p"]:
        try:
            r = requests.get(f"https://apicarto.ign.fr/api/gpu/{suffix}",
                             params={"geom": geom_str}, timeout=15)
            if r.ok:
                for f in r.json().get("features", []):
                    cat = f.get("properties", {}).get("categorie", "")
                    if cat:
                        label = SUP_LABELS.get(cat, cat)
                        servitudes.add(f"{cat} - {label}")
        except Exception as e:
            app.logger.warning("SUP %s error: %s", suffix, e)
    return sorted(servitudes)


def _gpt_resume_plu(zone, typezone, destdomi, libelong, ville, type_bien):
    """Use GPT to generate a human-readable PLU summary."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return libelong or f"Zone {zone}"
    prompt = (
        f"Tu es urbaniste expert. R\u00e9sume en 3-4 phrases claires ce que signifie "
        f"la zone PLU \u00ab {zone} \u00bb ({libelong}) pour un {type_bien} \u00e0 {ville}.\n"
        f"Type de zone: {typezone}. Destination dominante: {destdomi}.\n"
        f"Explique: ce qui est autoris\u00e9/interdit, les contraintes cl\u00e9s. "
        f"Ton professionnel, pas de liste \u00e0 puces."
    )
    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 400, "temperature": 0.5},
            timeout=25)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        app.logger.error("GPT resume PLU: %s", e)
        return libelong or f"Zone {zone}"


# ---------------------------------------------------------------------------
# COMPARABLES — Recherche DVF intelligente (API Cerema + reverse geocode)
# ---------------------------------------------------------------------------
# Mapping type de bien → codtypbien DVF Cerema
_TYPE_DVF_MAP = {
    "local commercial":    "14",
    "bureau":              "14",
    "bureaux":             "14",
    "commerce":            "14",
    "entrepot":            "14",
    "entrepôt":            "14",
    "atelier":             "14",
    "activité":            "14",
    "local industriel":    "14",
    "local professionnel": "14",
    "appartement":         "121",
    "studio":              "121",
    "maison":              "111",
    "terrain":             "21",
}


def _dvf_code_for_type(type_bien):
    """Return Cerema codtypbien from a free-text property type."""
    t = (type_bien or "").strip().lower()
    for key, code in _TYPE_DVF_MAP.items():
        if key in t:
            return code
    return "14"  # default to Activité (commercial)


def _reverse_geocode_parcelle(idpar):
    """Get address from a cadastre parcel ID like '56260000DH0211'."""
    try:
        code_insee = idpar[:5]
        prefix = idpar[5:8]
        section = idpar[8:10]
        numero = idpar[10:]
        r = requests.get(
            "https://apicarto.ign.fr/api/cadastre/parcelle",
            params={"code_insee": code_insee, "section": section, "numero": numero},
            timeout=10)
        r.raise_for_status()
        feats = r.json().get("features", [])
        if feats:
            props = feats[0].get("properties", {})
            # Try to get centroid for reverse geocoding
            geom = feats[0].get("geometry", {})
            coords = None
            if geom.get("type") == "Polygon":
                pts = geom["coordinates"][0]
                lon = sum(p[0] for p in pts) / len(pts)
                lat = sum(p[1] for p in pts) / len(pts)
                coords = (lon, lat)
            elif geom.get("type") == "MultiPolygon":
                pts = geom["coordinates"][0][0]
                lon = sum(p[0] for p in pts) / len(pts)
                lat = sum(p[1] for p in pts) / len(pts)
                coords = (lon, lat)
            if coords:
                rg = requests.get(
                    "https://api-adresse.data.gouv.fr/reverse/",
                    params={"lon": coords[0], "lat": coords[1]},
                    timeout=8)
                rg.raise_for_status()
                rf = rg.json().get("features", [])
                if rf:
                    p = rf[0].get("properties", {})
                    return p.get("name", ""), p.get("city", ""), coords
    except Exception:
        pass
    return "", "", None


@app.route("/comparables", methods=["POST"])
def comparables():
    """Recherche de biens comparables via l'API DVF Cerema.

    Body JSON:
      - code_insee (str, optional): code INSEE commune — if missing, geocoded from ville
      - ville (str): commune
      - code_postal (str): code postal
      - type_bien (str): type de bien (bureau, local commercial, appartement...)
      - surface (int): surface du bien en m²
      - rayon_communes (list[str], optional): codes INSEE voisins à inclure
      - annee_min (int, optional): année minimum (default: 3 ans en arrière)
      - limit (int, optional): max comparables renvoyés (default: 6)
    """
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"ok": False, "error": "Body JSON manquant"}), 400

    ville = body.get("ville", "Vannes")
    cp = body.get("code_postal", "56000")
    adresse = body.get("adresse", "")
    type_bien = body.get("type_bien", "Local commercial")
    surface = int(body.get("surface") or 0)
    annee_min = int(body.get("annee_min") or 2021)
    limit = min(int(body.get("limit") or 6), 10)
    # Rayon km autour de l'adresse pour filtrer les comparables
    # (0 ou absent = pas de filtre, toute la commune comme avant)
    try:
        rayon_km = float(body.get("rayon_km") or 0)
    except (ValueError, TypeError):
        rayon_km = 0

    # Resolve code_insee + lat/lon du bien (pour filtre rayon)
    code_insee = body.get("code_insee", "")
    ref_lat = None
    ref_lon = None
    geo = _geocode_urba(adresse, cp, ville)
    if geo:
        ref_lon, ref_lat, insee_g = geo
        if not code_insee:
            code_insee = insee_g
    if not code_insee:
        return jsonify({"ok": False, "error": "Impossible de résoudre le code INSEE"}), 400
    if rayon_km > 0 and (ref_lat is None or ref_lon is None):
        app.logger.warning("Comparables: rayon_km=%s demandé mais géocodage a échoué — rayon ignoré", rayon_km)
        rayon_km = 0

    # Surface filter: ±50% of target surface, min 40m²
    if surface > 0:
        sbatmin = max(40, int(surface * 0.5))
        sbatmax = int(surface * 1.5)
    else:
        sbatmin = 40
        sbatmax = 5000

    codtypbien = _dvf_code_for_type(type_bien)

    # Query Cerema DVF API
    communes = body.get("rayon_communes") or [code_insee]
    if code_insee not in communes:
        communes.append(code_insee)

    all_mutations = []
    for cinsee in communes[:5]:  # max 5 communes
        try:
            params = {
                "code_insee": cinsee,
                "nature_mutation": "Vente",
                "codtypbien": codtypbien,
                "sbatmin": sbatmin,
                "sbatmax": sbatmax,
                "anneemut_min": annee_min,
                "page_size": 50,
                "ordering": "-datemut",
            }
            r = requests.get(
                "https://apidf-preprod.cerema.fr/dvf_opendata/mutations/",
                params=params, timeout=20)
            r.raise_for_status()
            data = r.json()
            all_mutations.extend(data.get("results", []))
        except Exception as e:
            app.logger.error("DVF Cerema %s: %s", cinsee, e)

    if not all_mutations:
        return jsonify({"ok": True, "comparables": [],
                        "message": "Aucune transaction comparable trouvée"}), 200

    # Score and rank by relevance (surface proximity + recency + distance si rayon_km)
    scored = []
    for m in all_mutations:
        s_bati = float(m.get("sbati") or 0)
        if s_bati <= 0:
            continue
        prix = float(m.get("valeurfonc") or 0)
        if prix <= 0:
            continue
        annee = m.get("anneemut", 2020)

        # Distance (si rayon_km actif) — via lat/lon du point de mutation Cerema
        dist_km = None
        if rayon_km > 0 and ref_lat is not None:
            mlat = m.get("latitude") or m.get("lat")
            mlon = m.get("longitude") or m.get("lon")
            if mlat is not None and mlon is not None:
                dist_km = _haversine_km(ref_lat, ref_lon, mlat, mlon)

        # Surface proximity score
        if surface > 0:
            ratio = min(s_bati, surface) / max(s_bati, surface)
        else:
            ratio = 0.5
        # Recency bonus
        recency = (annee - annee_min + 1) / 6
        score = ratio * 0.7 + min(recency, 1.0) * 0.3
        scored.append((score, m, s_bati, prix, dist_km))

    # Filtre rayon : applique le seuil si dist_km connue
    # Élargit automatiquement à rayon_km * 2 si < 3 résultats passent le filtre
    elargissement = False
    applied_rayon = rayon_km
    if rayon_km > 0:
        inside = [s for s in scored if s[4] is not None and s[4] <= rayon_km]
        if len(inside) < 3:
            applied_rayon = rayon_km * 2
            inside = [s for s in scored if s[4] is not None and s[4] <= applied_rayon]
            elargissement = True
            app.logger.info("Comparables: rayon %.1f km < 3 résultats, élargi à %.1f km", rayon_km, applied_rayon)
        if inside:
            scored = inside
        else:
            app.logger.warning("Comparables: aucune mutation avec lat/lon dans le rayon %.1f km, désactivation du filtre", rayon_km)
            applied_rayon = 0

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:limit]

    # Enrich with addresses via reverse geocode (from parcel IDs)
    results = []
    for score, m, s_bati, prix, dist_km in top:
        adresse_c = ""
        ville_comp = ""
        parcels = m.get("l_idparmut") or m.get("l_idpar") or []
        if parcels:
            adresse_c, ville_comp, _ = _reverse_geocode_parcelle(parcels[0])

        prix_m2 = int(prix / s_bati) if s_bati > 0 else 0
        results.append({
            "adresse": adresse_c or "Adresse non disponible",
            "ville": ville_comp or ville,
            "prix": int(prix),
            "surface": int(s_bati),
            "prix_m2": prix_m2,
            "annee": m.get("anneemut", ""),
            "type_bien": m.get("libtypbien", type_bien),
            "source": "DVF",
            "score": round(score, 2),
            "distance_km": round(dist_km, 2) if dist_km is not None else None,
        })

    # Compute fourchette from comparables
    prix_m2_list = [c["prix_m2"] for c in results if c["prix_m2"] > 0]
    if prix_m2_list and surface > 0:
        avg_m2 = sum(prix_m2_list) / len(prix_m2_list)
        min_m2 = min(prix_m2_list)
        max_m2 = max(prix_m2_list)
        fourchette = {
            "prix_estime_min": int(min_m2 * surface),
            "prix_estime_max": int(max_m2 * surface),
            "prix_retenu": int(avg_m2 * surface),
            "prix_m2_moyen": int(avg_m2),
            "prix_m2_min": int(min_m2),
            "prix_m2_max": int(max_m2),
        }
    else:
        fourchette = {}

    app.logger.info(
        "Comparables for %s %s: %d found, %d returned, surface=%d, type=%s",
        ville, code_insee, len(all_mutations), len(results), surface, codtypbien)

    return jsonify({
        "ok": True,
        "comparables": results,
        "fourchette": fourchette,
        "meta": {
            "code_insee": code_insee,
            "type_dvf": codtypbien,
            "surface_range": f"{sbatmin}-{sbatmax} m²",
            "annee_min": annee_min,
            "total_mutations": len(all_mutations),
            "rayon_km": applied_rayon,
            "elargissement": elargissement,
        }
    })


@app.route("/urbanisme", methods=["POST"])
def urbanisme():
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"ok": False, "error": "Body JSON manquant"}), 400

    ref_cad = body.get("ref_cadastrale", "")
    adresse = body.get("adresse", "")
    ville = body.get("ville", "Vannes")
    cp = body.get("code_postal", "56000")
    type_bien = body.get("type_bien", "Local commercial")

    # Step 1: Geocode
    geo = _geocode_urba(adresse, cp, ville)
    if not geo:
        return jsonify({"ok": False, "error": "Impossible de g\u00e9olocaliser l\u2019adresse"}), 400
    lon, lat, code_insee = geo
    app.logger.info("Urbanisme: geocoded %s %s -> INSEE %s (%s, %s)", adresse, ville, code_insee, lon, lat)

    # Step 2: Get parcel geometry (from cadastre ref or point/reverse lookup)
    geom = None
    found_ref_cadastrale = ref_cad  # will be enriched if found via reverse
    if ref_cad:
        _, section, numero = _parse_ref_cadastrale(ref_cad)
        if section and numero:
            geom = _get_parcelle_geometry(code_insee, section, numero)

    # Fallback: reverse-lookup parcelle from GPS coordinates
    # IMPORTANT: API Carto IGN requires geom GeoJSON, NOT lon/lat params
    # (lon/lat without geom returns random parcels matching section/numero from all France)
    if not geom:
        try:
            import json as _json
            point_geom = _json.dumps({"type": "Point", "coordinates": [lon, lat]})
            r_cad = requests.get(
                "https://apicarto.ign.fr/api/cadastre/parcelle",
                params={"geom": point_geom, "code_insee": code_insee},
                timeout=15)
            r_cad.raise_for_status()
            feats = r_cad.json().get("features", [])
            # Filter to parcels actually in the target commune
            feats = [f for f in feats
                     if f.get("properties", {}).get("code_insee", "") == code_insee]
            if feats:
                props = feats[0].get("properties", {})
                geom = feats[0]["geometry"]
                sec = props.get("section", "")
                num = props.get("numero", "")
                com = props.get("com_abs", "000")
                if sec and num:
                    found_ref_cadastrale = f"{com} {sec} {num}".strip()
                    app.logger.info("Urbanisme: reverse-lookup found parcelle %s", found_ref_cadastrale)
        except Exception as e:
            app.logger.error("Cadastre reverse-lookup: %s", e)

    if not geom:
        geom = {"type": "Point", "coordinates": [lon, lat]}
        app.logger.info("Urbanisme: using point geometry (no parcel found)")

    # Step 3: PLU zone — retry 2× avec backoff 1s
    import time as _time
    plu = None
    last_plu_error = None
    for attempt in range(2):
        try:
            plu = _get_plu_zone(geom)
            if plu:
                break
        except Exception as e:
            last_plu_error = str(e)
            app.logger.warning("PLU attempt %d failed: %s", attempt + 1, e)
        if attempt == 0:
            _time.sleep(1.0)

    zone_plu = ""
    resume_plu = ""
    url_reglement = ""
    if plu:
        zone_plu = plu.get("zone_plu", "")
        url_reglement = plu.get("url_reglement", "")
        resume_plu = _gpt_resume_plu(
            zone_plu, plu.get("typezone", ""), plu.get("destdomi", ""),
            plu.get("libelong", ""), ville, type_bien)

    # Step 4: Servitudes — retry 2× aussi
    servitudes = []
    for attempt in range(2):
        try:
            servitudes = _get_servitudes(geom) or []
            break
        except Exception as e:
            app.logger.warning("Servitudes attempt %d failed: %s", attempt + 1, e)
            if attempt == 0:
                _time.sleep(1.0)

    # Si aucune donnée : retour 200 avec message d'erreur lisible
    # (au lieu de 404 opaque) pour que le cockpit affiche un champ de saisie manuelle
    if not zone_plu and not servitudes:
        msg = (last_plu_error or
               "L'API IGN (apicarto) n'a retourné aucune zone PLU ni servitude pour cette adresse. "
               "Possibles causes : parcelle mal identifiée, PLU communal non encore numérisé, "
               "ou panne temporaire IGN. Vous pouvez saisir les infos manuellement.")
        app.logger.info("Urbanisme: aucune donnée IGN pour %s, %s — fallback manuel", adresse, ville)
        return jsonify({
            "ok": True,
            "plu": None,
            "zone_plu": "",
            "resume_plu": "",
            "url_reglement": "",
            "servitudes": [],
            "code_insee": code_insee,
            "ref_cadastrale": found_ref_cadastrale,
            "error_message": msg,
        })

    result = {
        "ok": True,
        "zone_plu": zone_plu,
        "resume_plu": resume_plu,
        "url_reglement": url_reglement,
        "servitudes": servitudes,
        "code_insee": code_insee,
        "ref_cadastrale": found_ref_cadastrale,
    }
    app.logger.info("Urbanisme result for %s: zone=%s, servitudes=%d", adresse, zone_plu, len(servitudes))
    return jsonify(result)

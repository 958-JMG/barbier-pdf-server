#!/usr/bin/env python3
"""
Barbier Immobilier — PDF Generator v3.0
Railway Flask app — routes /generate-pdf-by-ref et /generate-pdf
Version fusionnée : générateur PDF intégré
"""

import io, os, math, base64, re
import requests
from flask import Flask, request, jsonify, send_file, Response

# ── Assets embarqués (base64) ─────────────────────────
import base64 as _b64
from assets import LOGO_B64, PICTO_SURFACE_B64, PICTO_TYPE_B64, PICTO_LIEU_B64, PICTO_VILLE_B64
from io import BytesIO as _BytesIO

def _img_reader(b64_str):
    from reportlab.lib.utils import ImageReader
    return ImageReader(_BytesIO(_b64.b64decode(b64_str)))


from PIL import Image, ImageDraw
import PIL.ImageOps as _PIL_OPS
import numpy as _np

def _invert_picto_b64(b64_str):
    """Picto noir sur fond noir → blanc sur fond transparent pour affichage sur cercle coloré."""
    import io as _io2, base64 as _b64_inv
    try:
        raw = _b64_inv.b64decode(b64_str)
        img = Image.open(_io2.BytesIO(raw)).convert('RGBA')
        arr = _np.array(img)
        # Détecter tracé : pixels sombres (luminosité < 128) avec alpha > 128
        lum = arr[:,:,0].astype(int) + arr[:,:,1].astype(int) + arr[:,:,2].astype(int)
        new = _np.zeros_like(arr)
        new[:,:,0] = 255; new[:,:,1] = 255; new[:,:,2] = 255
        new[:,:,3] = _np.where((lum < 384) & (arr[:,:,3] > 64), 255, 0).astype(_np.uint8)
        result = Image.fromarray(new, 'RGBA')
        buf = _io2.BytesIO()
        result.save(buf, format='PNG')
        return _b64_inv.b64encode(buf.getvalue()).decode()
    except Exception:
        return b64_str
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle


def seatable_get_row(reference):
    """Récupère une ligne SeaTable par référence."""
    r = requests.get(
        "https://cloud.seatable.io/api/v2.1/dtable/app-access-token/",
        headers={"Authorization": f"Token {SEATABLE_TOKEN}"}, timeout=10
    )
    r.raise_for_status()
    tok = r.json()
    AT   = tok["access_token"]
    UUID = tok["dtable_uuid"]

    sql = f"SELECT * FROM `01_Biens` WHERE `Reference` = '{reference}' LIMIT 1"
    resp = requests.post(
        f"https://cloud.seatable.io/api-gateway/api/v2/dtables/{UUID}/sql",
        headers={"Authorization": f"Token {AT}", "Content-Type": "application/json"},
        json={"sql": sql, "convert_keys": True}, timeout=10
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])
    if not results:
        raise ValueError(f"Référence {reference} non trouvée dans SeaTable")
    return results[0]


def seatable_update_after_generation(reference):
    """Met à jour statut et reset checkbox après génération PDF."""
    try:
        tok = requests.get(
            "https://cloud.seatable.io/api/v2.1/dtable/app-access-token/",
            headers={"Authorization": f"Token {SEATABLE_TOKEN}"}, timeout=10
        ).json()
        AT   = tok["access_token"]
        UUID = tok["dtable_uuid"]

        results = requests.post(
            f"https://cloud.seatable.io/api-gateway/api/v2/dtables/{UUID}/sql",
            headers={"Authorization": f"Token {AT}", "Content-Type": "application/json"},
            json={"sql": f"SELECT _id FROM `01_Biens` WHERE `Reference` = '{reference}' LIMIT 1", "convert_keys": False},
            timeout=10
        ).json().get("results", [])
        if not results:
            return
        row_id = results[0]["_id"]

        requests.put(
            f"https://cloud.seatable.io/api-gateway/api/v2/dtables/{UUID}/rows/",
            headers={"Authorization": f"Token {AT}", "Content-Type": "application/json"},
            json={"table_name": "01_Biens", "updates": [{
                "row_id": row_id,
                "row": {
                    "Statut avis valeur": "PDF généré"
                }
            }]},
            timeout=10
        )
    except Exception as e:
        print(f"[UPDATE] erreur: {e}", flush=True)


def seatable_update_statut(reference, statut):
    """Met à jour le statut avis valeur dans SeaTable."""
    r = requests.get(
        "https://cloud.seatable.io/api/v2.1/dtable/app-access-token/",
        headers={"Authorization": f"Token {SEATABLE_TOKEN}"}, timeout=10
    )
    r.raise_for_status()
    tok = r.json()
    AT   = tok["access_token"]
    UUID = tok["dtable_uuid"]

    # Récupérer le _id de la ligne
    sql = f"SELECT _id FROM `01_Biens` WHERE `Reference` = '{reference}' LIMIT 1"
    resp = requests.post(
        f"https://cloud.seatable.io/api-gateway/api/v2/dtables/{UUID}/sql",
        headers={"Authorization": f"Token {AT}", "Content-Type": "application/json"},
        json={"sql": sql, "convert_keys": False}, timeout=10
    )
    results = resp.json().get("results", [])
    if not results:
        return
    row_id = results[0]["_id"]

    requests.put(
        f"https://cloud.seatable.io/api-gateway/api/v2/dtables/{UUID}/rows/",
        headers={"Authorization": f"Token {AT}", "Content-Type": "application/json"},
        json={"table_name": "01_Biens", "updates": [{"row_id": row_id, "row": {"Statut avis valeur": statut}}]},
        timeout=10
    )


app = Flask(__name__)

# ── CREDENTIALS ─────────────────────────────────────────────────────────────
SEATABLE_TOKEN = os.environ.get("SEATABLE_TOKEN", "4fcb9688f14c8c6b076a5612c0dbadc0d7e7cf41")

# ── CHARTE GRAPHIQUE ────────────────────────────────────────────────────────
TEAL         = colors.HexColor("#16708B")   # extrait du LOGO_B64
TEAL_DARK    = colors.HexColor("#0D5570")   # variante foncée
TEAL_LIGHT   = colors.HexColor("#E8F5F8")
ORANGE       = colors.HexColor("#F0795B")   # extrait du LOGO_B64
ORANGE_LIGHT = colors.HexColor("#FDF0EC")
GRAY_DARK    = colors.HexColor("#1F2937")
GRAY_MID     = colors.HexColor("#6B7280")
GRAY_LIGHT   = colors.HexColor("#F3F4F6")
GRAY_BORDER  = colors.HexColor("#D1D5DB")
WHITE        = colors.white

PAGE_W, PAGE_H = A4
ML = 20*mm; MR = 20*mm; MT = 22*mm; MB = 16*mm
CW = PAGE_W - ML - MR

def fmt(val):
    if val is None: return "—"
    try: return f"{float(val):,.0f}".replace(",", " ") + " €"
    except: return str(val)

# ── UTILITAIRES ─────────────────────────────────────────────────────────────

def rrect(c, x, y, w, h, r=4, fill=None, stroke=None, sw=0.6):
    c.saveState()
    if fill:   c.setFillColor(fill)
    if stroke: c.setStrokeColor(stroke); c.setLineWidth(sw)
    p = c.beginPath()
    p.moveTo(x+r, y)
    p.lineTo(x+w-r, y)
    p.arcTo(x+w-2*r, y, x+w, y+2*r, -90, 90)
    p.lineTo(x+w, y+h-r)
    p.arcTo(x+w-2*r, y+h-2*r, x+w, y+h, 0, 90)
    p.lineTo(x+r, y+h)
    p.arcTo(x, y+h-2*r, x+2*r, y+h, 90, 90)
    p.lineTo(x, y+r)
    p.arcTo(x, y, x+2*r, y+2*r, 180, 90)
    p.close()
    c.drawPath(p, fill=1 if fill else 0, stroke=1 if stroke else 0)
    c.restoreState()

def sec_title(c, x, y, txt, font_size=8):
    """Titre de section : trait orange + texte teal. Retourne Y du bas."""
    bar_h = 13
    c.saveState()
    c.setFillColor(ORANGE)
    c.rect(x, y - bar_h + 3, 3, bar_h, fill=1, stroke=0)
    c.setFillColor(TEAL_DARK)
    c.setFont("Helvetica-Bold", font_size)
    c.drawString(x + 8, y - bar_h + 5, txt.upper())
    c.restoreState()
    return y - bar_h - 6   # curseur après le titre

def hline(c, x, y, w, color=GRAY_BORDER, lw=0.5):
    c.saveState()
    c.setStrokeColor(color)
    c.setLineWidth(lw)
    c.line(x, y, x+w, y)
    c.restoreState()

def wrap_text(c, text, x, y, max_w, max_h, font="Helvetica", size=8, color=GRAY_DARK, leading=12):
    """Dessine du texte avec retour à la ligne. Retourne Y final."""
    style = ParagraphStyle("t", fontName=font, fontSize=size,
                           leading=leading, textColor=color, alignment=4)
    p = Paragraph(text, style)
    p.wrap(max_w, max_h)
    p.drawOn(c, x, y - max_h + (max_h - p.height))
    return y - p.height


# ── CARTE OSM ───────────────────────────────────────────────────────────────


def get_ref_cadastrale(adresse, ville):
    """Récupère la référence cadastrale via BAN + data.geopf.fr (géoplateforme IGN)"""
    try:
        import urllib.parse as _up
        q = _up.quote(f"{adresse}, {ville}, France")
        # 1. Géocode BAN
        geo_r = requests.get(
            f"https://api-adresse.data.gouv.fr/search/?q={q}&limit=1",
            headers={"User-Agent": "BarbierImmo/1.0"}, timeout=8
        )
        features = geo_r.json().get("features", [])
        if not features:
            return "—"
        coords = features[0]["geometry"]["coordinates"]
        lon, lat = coords[0], coords[1]
        # 2. Reverse geocodage parcellaire — géoplateforme IGN
        cad_r = requests.get(
            f"https://data.geopf.fr/geocodage/reverse?lon={lon}&lat={lat}&index=parcel&limit=1",
            headers={"User-Agent": "BarbierImmo/1.0"}, timeout=10
        )
        feats = cad_r.json().get("features", [])
        if not feats:
            return "—"
        p = feats[0]["properties"]
        idu = p.get("id", "")
        if idu:
            # Format lisible : dep + commune + section + numéro
            # ex: "56053000AN0003" → "56053 AN 0003"
            section = p.get("section", "")
            numero = p.get("number", "")
            dep = p.get("departmentcode", "")
            com = p.get("municipalitycode", "")
            return f"{dep}{com} {section} {numero}".strip() if section else idu
        return "—"
    except Exception:
        return "—"
def _geocode(address):
    """Géocode via BAN (data.gouv.fr) — fiable pour adresses françaises."""
    try:
        import urllib.parse
        q = urllib.parse.quote(address)
        r = requests.get(
            f"https://api-adresse.data.gouv.fr/search/?q={q}&limit=1",
            headers={"User-Agent": "BarbierImmobilier/1.0"}, timeout=10)
        feat = r.json().get("features", [])
        if feat:
            lon, lat = feat[0]["geometry"]["coordinates"]
            return float(lat), float(lon)
    except: pass
    return None, None

def get_osm_map(address, out_w=840, out_h=340, zoom=16):
    """Carte centrée exactement sur le point géocodé — BAN en priorité, Nominatim en fallback."""
    try:
        headers = {"User-Agent": "BarbierImmobilier/1.0"}
        # Essai 1 : BAN (API adresse gouv.fr — très fiable pour la France)
        lat, lon = _geocode(address)
        # Essai 2 : Nominatim fallback
        if lat is None:
            r = requests.get("https://nominatim.openstreetmap.org/search",
                             params={"q": address, "format": "json", "limit": 1},
                             headers=headers, timeout=15)
            res = r.json()
            if not res: return None
            lat = float(res[0]["lat"])
            lon = float(res[0]["lon"])
        if lat is None: return None

        T = 256  # tile size

        def tile_float(lat, lon, z):
            lr = math.radians(lat)
            n = 2**z
            fx = (lon + 180) / 360 * n
            fy = (1 - math.log(math.tan(lr) + 1/math.cos(lr)) / math.pi) / 2 * n
            return fx, fy

        fx, fy = tile_float(lat, lon, zoom)
        tx, ty = int(fx), int(fy)
        sub_x = (fx - tx) * T
        sub_y = (fy - ty) * T

        # Grille 5×3 tuiles autour du point (suffisant pour tout crop)
        gc, gr = 5, 4
        ox, oy = tx - 2, ty - 1
        canvas_img = Image.new("RGB", (gc*T, gr*T), (220, 220, 220))
        for dc in range(gc):
            for dr in range(gr):
                url = f"https://tile.openstreetmap.org/{zoom}/{ox+dc}/{oy+dr}.png"
                tr = requests.get(url, headers=headers, timeout=8)
                if tr.status_code == 200:
                    canvas_img.paste(Image.open(io.BytesIO(tr.content)).convert("RGB"),
                                     (dc*T, dr*T))

        # Position absolue du marqueur
        mx = (tx - ox) * T + sub_x
        my = (ty - oy) * T + sub_y

        # Crop centré
        l = max(0, int(mx - out_w/2))
        t = max(0, int(my - out_h/2))
        r2 = l + out_w
        b  = t + out_h
        if r2 > gc*T: l = gc*T - out_w; r2 = gc*T
        if b  > gr*T: t = gr*T - out_h; b  = gr*T
        l = max(0, l); t = max(0, t)

        cropped = canvas_img.crop((l, t, r2, b))
        mkx = int(mx - l)
        mky = int(my - t)

        from PIL import ImageDraw
        d = ImageDraw.Draw(cropped)
        R = 15
        d.ellipse([mkx-R+3, mky-R+3, mkx+R+3, mky+R+3], fill=(0,0,0,50))
        d.ellipse([mkx-R, mky-R, mkx+R, mky+R], fill=(232,99,42), outline=(255,255,255), width=4)
        d.ellipse([mkx-5, mky-5, mkx+5, mky+5], fill=(255,255,255))

        buf = io.BytesIO()
        cropped.save(buf, "PNG")
        buf.seek(0)
        return buf
    except Exception as e:
        print(f"OSM error: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  PAGE 1 — En-tête + Identification + Carte + Valeurs
# ══════════════════════════════════════════════════════════════════════════════

def page1(c, d, logo_buf=None):
    y = PAGE_H - MT   # curseur descend depuis le haut

    # ── Trait teal vertical gauche ──────────────────────────────────────────
    c.saveState()
    c.setFillColor(TEAL)
    c.rect(0, 0, 5, PAGE_H, fill=1, stroke=0)
    c.restoreState()

    # ── HEADER ──────────────────────────────────────────────────────────────
    header_h = 36

    # Logo réel (488×662 → ratio 0.738, on cible h=36pt → w≈26pt)
    logo_h = 36
    logo_w = 36 * (488/662)
    try:
        c.drawImage(_img_reader(LOGO_B64), ML, y - logo_h, width=logo_w, height=logo_h,
                    mask='auto', preserveAspectRatio=True)
    except:
        pass

    # Titre droite
    c.saveState()
    c.setFillColor(TEAL)
    c.setFont("Helvetica-Bold", 15)
    c.drawRightString(PAGE_W - MR, y - 13, "AVIS DE VALEUR PROFESSIONNEL")
    c.setFillColor(GRAY_MID)
    c.setFont("Helvetica", 8)
    c.drawRightString(PAGE_W - MR, y - 25, f"Réf. {d['reference']}  ·  {d['ville']}  ·  {d['negociateur']}")
    c.restoreState()

    y -= header_h + 4
    hline(c, ML, y, CW, TEAL, 1.5)
    y -= 12

    # ── SECTION 01 — IDENTIFICATION ─────────────────────────────────────────
    y = sec_title(c, ML, y, "01 — Identification du bien")

    cell_h = 26
    cell_gap = 3
    col_w = (CW - 2*cell_gap) / 3

    fields = [
        ("Type de bien",       d["type_bien"]),
        ("Surface habitable",  f"{d['surface']} m\u00b2"),
        ("Surface terrain",    f"{d['surface_terrain']} m\u00b2" if d["surface_terrain"] else "—"),
        ("Adresse complète",   d["adresse"]),
        ("Réf. cadastrale",    d["ref_cadastrale"]),
        ("État général",       d["etat_bien"]),
    ]

    for i, (lbl, val) in enumerate(fields):
        col = i % 3
        row = i // 3
        cx = ML + col * (col_w + cell_gap)
        cy = y - row * (cell_h + cell_gap)

        rrect(c, cx, cy - cell_h, col_w, cell_h, r=3, fill=GRAY_LIGHT)
        c.saveState()
        c.setFillColor(GRAY_MID);  c.setFont("Helvetica", 6.5)
        c.drawString(cx+6, cy - 9, lbl)
        c.setFillColor(GRAY_DARK); c.setFont("Helvetica-Bold", 8.5)
        c.drawString(cx+6, cy - 20, val)
        c.restoreState()

    y -= 2*(cell_h + cell_gap) + 12

    # ── SECTION PHOTO DU BIEN (si fournie) ─────────────────────────────────
    photo_b64 = d.get("photo_bien")
    if photo_b64:
        try:
            photo_buf = io.BytesIO(base64.b64decode(photo_b64))
            # Calculer hauteur selon ratio réel de l'image — max 95mm
            from PIL import Image as _PILImage
            _pil = _PILImage.open(io.BytesIO(base64.b64decode(photo_b64)))
            _w, _h = _pil.size
            _ratio = _h / _w if _w > 0 else 0.75
            photo_h = min(CW * _ratio, 95*mm)
            y = sec_title(c, ML, y, "02 — Photo du bien")
            c.drawImage(rl_canvas.ImageReader(photo_buf), ML, y - photo_h,
                        width=CW, height=photo_h, preserveAspectRatio=False)
            y -= photo_h + 14
        except Exception as e:
            print(f"Photo error: {e}")
    # ── LOCALISATION + ESTIMATION CÔTE À CÔTE ──────────────────────────────
    section_num_loc = "03" if d.get("photo_bien") else "02"
    y = sec_title(c, ML, y, f"{section_num_loc} — Localisation & Estimation de valeur")

    bloc_h = 80*mm   # hauteur commune des deux colonnes
    gap    = 8       # espace entre colonne gauche et droite
    map_w  = CW * 0.56
    est_w  = CW - map_w - gap

    # ── Colonne gauche : carte OSM ───────────────────────────────────────────
    _addr_geo = " ".join(filter(None, [d.get("adresse",""), d.get("code_postal","") or "56000", d.get("ville","") or "Vannes", "France"]))
    map_buf = get_osm_map(_addr_geo, out_w=640, out_h=420, zoom=18)

    if map_buf:
        c.drawImage(rl_canvas.ImageReader(map_buf), ML, y - bloc_h,
                    width=map_w, height=bloc_h, preserveAspectRatio=False)
    else:
        rrect(c, ML, y - bloc_h, map_w, bloc_h, fill=GRAY_LIGHT, stroke=GRAY_BORDER)
        c.setFillColor(GRAY_MID); c.setFont("Helvetica", 8)
        c.drawCentredString(ML + map_w/2, y - bloc_h/2, "Carte indisponible")

    # Légende sous la carte
    c.saveState()
    c.setFillColor(TEAL); c.setFont("Helvetica-Bold", 7)
    c.drawString(ML, y - bloc_h - 9, f"\u25a0  {d.get('adresse','')}")
    c.restoreState()

    # ── Colonne droite : 3 cartes estimation ────────────────────────────────
    ex = ML + map_w + gap   # x de départ colonne droite

    cards = [
        ("VALEUR BASSE",   d["prix_min"],    False),
        ("VALEUR RETENUE", d["prix_retenu"],  True),
        ("VALEUR HAUTE",   d["prix_max"],    False),
    ]

    card_gap = 5
    card_h   = (bloc_h - 2*card_gap) / 3   # 3 cartes empilées

    for i, (lbl, prix, rec) in enumerate(cards):
        cy = y - (i * (card_h + card_gap)) - card_h

        if rec:
            rrect(c, ex, cy, est_w, card_h, r=5, fill=TEAL, stroke=TEAL_DARK, sw=1.5)
            tc, pc = WHITE, WHITE
        else:
            rrect(c, ex, cy, est_w, card_h, r=5, fill=WHITE, stroke=GRAY_BORDER, sw=0.8)
            tc, pc = GRAY_MID, GRAY_DARK

        # Badge RECOMMANDÉ
        if rec:
            bw, bh = 78, 13
            bx = ex + (est_w - bw)/2
            by = cy + card_h - bh - 4
            rrect(c, bx, by, bw, bh, r=6, fill=ORANGE)
            c.saveState()
            c.setFillColor(WHITE); c.setFont("Helvetica-Bold", 6)
            c.drawCentredString(ex + est_w/2, by + 3.5, "\u2605  RECOMMAND\u00c9")
            c.restoreState()

        # Label
        label_y = cy + card_h - (rec and 30 or 13)
        c.saveState()
        c.setFillColor(tc); c.setFont("Helvetica", 6.5)
        c.drawCentredString(ex + est_w/2, label_y, lbl)
        c.restoreState()

        # Prix
        prix_y = cy + card_h/2 - (rec and 17 or 15)
        c.saveState()
        c.setFillColor(pc); c.setFont("Helvetica-Bold", rec and 13 or 11)
        c.drawCentredString(ex + est_w/2, prix_y, fmt(prix))
        c.restoreState()

    y -= bloc_h + 18


# ══════════════════════════════════════════════════════════════════════════════
#  PAGE 2 — Analyse + Synthèse + Signatures (tout tient sur la page)
# ══════════════════════════════════════════════════════════════════════════════

def page2(c, d, logo_buf=None):
    y = PAGE_H - MT

    c.saveState()
    c.setFillColor(TEAL)
    c.rect(0, 0, 5, PAGE_H, fill=1, stroke=0)
    c.restoreState()

    # Section 04 — Analyse de marché (rendu structuré par sections)
    y = sec_title(c, ML, y, "04 — Analyse de marché & Avis professionnel")

    import re as _re

    avis_raw = (d.get("Avis de valeur") or "Avis de valeur à compléter.").strip()

    # Parser flexible : accepte ---TAG--- ET "TAG\n" (sans tirets)
    SECTION_MAP = {
        "SYNTHÈSE": "Synthèse",
        "SYNTHESE": "Synthèse",
        "MÉTHODOLOGIE": "Méthodologie",
        "METHODOLOGIE": "Méthodologie",
        "ÉVALUATION DÉTAILLÉE": "Évaluation détaillée",
        "EVALUATION DÉTAILLÉE": "Évaluation détaillée",
        "EVALUATION DETAILLEE": "Évaluation détaillée",
        "RECOMMANDATIONS": "Recommandations",
        # VALEURS : affiché dans la synthèse des valeurs, pas ici
    }
    SKIP_TAGS = {"VALEURS", "VALEUR"}

    sections = []
    # Essai 1 : format avec ---TAG---
    parts = _re.split(r'---([^-\n]+)---', avis_raw)
    if len(parts) >= 3:
        for i in range(1, len(parts) - 1, 2):
            tag = parts[i].strip().upper()
            body = parts[i+1].strip()
            if tag in SKIP_TAGS:
                continue
            label = SECTION_MAP.get(tag)
            if label and body:
                sections.append((label, body))

    # Essai 2 si pas de tags --- : format "NOM_SECTION\ntexte"
    if not sections:
        # Le pattern inclut AUSSI les SKIP_TAGS pour bien les couper
        all_keys = list(SECTION_MAP.keys()) + list(SKIP_TAGS)
        pattern = '|'.join(_re.escape(k) for k in all_keys)
        parts2 = _re.split(r'^(' + pattern + r')\s*$', avis_raw, flags=_re.MULTILINE | _re.IGNORECASE)
        if len(parts2) >= 3:
            for i in range(1, len(parts2) - 1, 2):
                tag = parts2[i].strip().upper()
                body = parts2[i+1].strip()
                if tag in SKIP_TAGS:
                    continue
                label = SECTION_MAP.get(tag)
                if label and body:
                    sections.append((label, body))

    # Fallback final
    if not sections:
        avis_no_val = _re.split(r'^VALEURS?\s*$', avis_raw, flags=_re.MULTILINE)[0].strip()
        sections = [("Analyse de marché", avis_no_val or avis_raw)]

    style_titre_s = ParagraphStyle("st", fontName="Helvetica-Bold", fontSize=8,
                                   leading=11, textColor=TEAL, spaceAfter=2)
    style_body_s  = ParagraphStyle("sb", fontName="Helvetica", fontSize=7.5,
                                   leading=11, textColor=GRAY_DARK)

    # Calculer hauteur totale
    total_h = 0
    rendered = []
    for label, body in sections:
        pt = Paragraph(label.upper(), style_titre_s)
        _, ht = pt.wrap(CW - 16, 9999)
        pb = Paragraph(body.replace("\n", "<br/>"), style_body_s)
        _, hb = pb.wrap(CW - 16, 9999)
        total_h += ht + hb + 8
        rendered.append((pt, ht, pb, hb))
    
    avis_box_h = total_h + 20
    rrect(c, ML, y - avis_box_h, CW, avis_box_h, r=4, fill=TEAL_LIGHT, stroke=TEAL, sw=0.5)

    cy = y - 12
    for (pt, ht, pb, hb) in rendered:
        cy -= ht
        pt.drawOn(c, ML + 8, cy)
        cy -= hb + 4
        pb.drawOn(c, ML + 8, cy)
        cy -= 4

    y -= avis_box_h + 14

    # Section annonce portail
    annonce_txt = (d.get("Version portail") or d.get("version_portail") or "").strip()
    if annonce_txt:
        y = sec_title(c, ML, y, "Annonce commerciale")
        style_ann = ParagraphStyle("ann", fontName="Helvetica", fontSize=7.5, leading=11,
                                   textColor=GRAY_DARK, leftIndent=8, rightIndent=8)
        pa = Paragraph(annonce_txt, style_ann)
        ann_w = CW - 16
        pa.wrap(ann_w, 200)
        ann_h = pa.height + 20
        rrect(c, ML, y - ann_h, CW, ann_h, r=4, fill=GRAY_LIGHT, stroke=GRAY_BORDER, sw=0.5)
        pa.drawOn(c, ML + 8, y - ann_h + 8)
        y -= ann_h + 14

    # Section 05 — Signatures
    y = sec_title(c, ML, y, "05 — Signatures & Validation")

    sig_h = 75
    sig_w = (CW - 14) / 2

    negociateur = d.get("Negociateur") or "Négociateur"
    for i, (name, role) in enumerate([
        (negociateur, "Négociateur mandataire"),
        ("Laurent Baradu", "Directeur — Barbier Immobilier"),
    ]):
        sx = ML + i * (sig_w + 14)
        rrect(c, sx, y - sig_h, sig_w, sig_h, r=4, fill=WHITE, stroke=GRAY_BORDER, sw=0.8)
        c.saveState()
        c.setFillColor(GRAY_MID); c.setFont("Helvetica", 6.5)
        c.drawString(sx + 8, y - 13, role.upper())
        c.setFillColor(GRAY_DARK); c.setFont("Helvetica-Bold", 9.5)
        c.drawString(sx + 8, y - 25, name)
        hline(c, sx + 8, y - 42, sig_w - 16, GRAY_BORDER, 0.5)
        c.setFillColor(GRAY_MID); c.setFont("Helvetica", 7)
        c.drawString(sx + 8, y - 53, "Signature :")
        c.drawString(sx + 8, y - 64, "Date :")
        c.restoreState()

    y -= sig_h + 12

    # Mentions légales
    mentions = (
        "Document établi par Barbier Immobilier, agent immobilier titulaire de la carte professionnelle "
        "Transactions sur immeubles et fonds de commerce. Ce document est établi à titre indicatif et "
        "ne constitue pas une expertise au sens de la norme MRICS. Les valeurs sont susceptibles "
        "d'évoluer en fonction des conditions du marché."
    )
    rrect(c, ML, y - 28, CW, 28, r=3, fill=GRAY_LIGHT)
    style_l = ParagraphStyle("l", fontName="Helvetica", fontSize=5.8, leading=8.5, textColor=GRAY_MID)
    pl = Paragraph(mentions, style_l)
    pl.wrap(CW - 16, 26)
    pl.drawOn(c, ML + 8, y - 25)


def footer(c, page_n, reference):
    y = MB - 4
    hline(c, ML, y + 6, CW, GRAY_BORDER, 0.5)
    c.saveState()
    c.setFillColor(GRAY_MID); c.setFont("Helvetica", 6.5)
    c.drawString(ML, y - 1, f"DOCUMENT CONFIDENTIEL  ·  Barbier Immobilier  ·  {reference}")
    c.drawRightString(PAGE_W - MR, y - 1, f"Page {page_n} / 2")
    c.restoreState()


def generate_pdf(data):
    """Génère le PDF avis de valeur et retourne un buffer BytesIO."""
    # Charger le logo
    logo_buf = None
    if LOGO_B64:
        try:
            logo_buf = io.BytesIO(base64.b64decode(LOGO_B64))
        except:
            pass

    # Calculer fourchettes DVF si absentes (prix_min/max/retenu = 0)
    prix_min_raw = data.get("Prix estime min") or 0
    prix_max_raw = data.get("Prix estime max") or 0
    prix_ret_raw = data.get("Prix retenu") or 0
    if not (prix_min_raw and prix_max_raw and prix_ret_raw):
        try:
            surf_v = float(data.get("Surface") or 0)
            ville_v = data.get("Ville","Vannes")
            cp_v = str(data.get("Code postal","56000"))
            type_v = data.get("Type de bien","")
            prix_v = float(data.get("Prix de vente") or 0)
            loyer_m = float(data.get("Loyer mensuel") or 0)
            if surf_v > 0:
                _, dvf_pm2, _ = _run_dvf(ville_v, cp_v, surf_v, type_v, limit=6)
                if dvf_pm2 > 0:
                    if loyer_m:
                        loyer_m2 = (loyer_m * 12) / surf_v
                        pm2_ref = (loyer_m2 + dvf_pm2) / 2
                        data["Prix estime min"] = int(pm2_ref * 0.88 * surf_v)
                        data["Prix estime max"] = int(pm2_ref * 1.12 * surf_v)
                        data["Prix retenu"]     = int(pm2_ref * surf_v)
                    elif prix_v:
                        pm2_vente = prix_v / surf_v
                        pm2_ref = (pm2_vente + dvf_pm2) / 2
                        data["Prix estime min"] = int(pm2_ref * 0.90 * surf_v)
                        data["Prix estime max"] = int(pm2_ref * 1.10 * surf_v)
                        data["Prix retenu"]     = int(pm2_ref * surf_v)
        except Exception:
            pass

    # Mapper les clés SeaTable (espaces) vers snake_case attendu par page1
    data = {
        "reference":       data.get("Reference", ""),
        "type_bien":       data.get("Type de bien", "—"),
        "surface":         data.get("Surface") or "—",
        "surface_terrain": data.get("Surface terrain") or "—",
"adresse":         data.get("Adresse") or "—",
"ville":           data.get("Ville") or "",
"code_postal":     data.get("Code postal") or "",
"ref_cadastrale":  data.get("Référence cadastrale") or get_ref_cadastrale(
            data.get("Adresse",""), data.get("Ville","")
        ),
"etat_bien":       data.get("Etat du bien") or "—",
"negociateur":     data.get("Negociateur") or "—",
"nom_client":      data.get("Nom client") or "—",
"photo_bien":      data.get("Photo bien") or data.get("photo_bien") or None,
"prix_min":        data.get("Prix estime min") or 0,
"prix_max":        data.get("Prix estime max") or 0,
"prix_retenu":     data.get("Prix retenu") or 0,
"prix_sans_decote":data.get("Prix sans décote") or 0,
"prix_avec_decote":data.get("Prix avec décote") or 0,
        "avis_valeur":     data.get("Avis de valeur", ""),
"version_portail": data.get("Version portail") or data.get("version_portail") or "",
        # Garder aussi les clés originales pour page2
        "Avis de valeur":  data.get("Avis de valeur", ""),
        "Version portail": data.get("Version portail") or data.get("version_portail") or "",
        "Prix estime min": data.get("Prix estime min"),
        "Prix estime max": data.get("Prix estime max"),
        "Prix retenu":     data.get("Prix retenu"),
        "Prix sans décote":data.get("Prix sans décote"),
        "Prix avec décote":data.get("Prix avec décote"),
        "Negociateur":     data.get("Negociateur", "—"),
        "Reference":       data.get("Reference", ""),
    }

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"Avis de Valeur — {data.get('Reference', '')} — Barbier Immobilier")
    c.setAuthor("Barbier Immobilier · 9•58 Consulting")

    page1(c, data, logo_buf)
    footer(c, 1, data.get("Reference", ""))
    c.showPage()

    page2(c, data, logo_buf)
    footer(c, 2, data.get("Reference", ""))
    c.showPage()

    c.save()
    buf.seek(0)
    return buf


# ── ROUTES FLASK ─────────────────────────────────────────────────────────────

@app.route("/")
def health():
    return jsonify({"service": "Barbier PDF Generator", "status": "ok", "version": "4.29"})


@app.route("/generate-pdf-by-ref", methods=["GET", "POST"])
def generate_by_ref():
    """
    GET/POST ?reference=BAR-00316&email=xxx@yyy.fr
    Récupère les données SeaTable, génère le PDF, l'envoie par email si fourni,
    et retourne le PDF en réponse.
    """
    reference = request.args.get("reference")
    email     = request.args.get("email")
    if not reference and request.is_json:
        body = request.get_json(silent=True) or {}
        reference = body.get("reference")
        email     = body.get("email")

    if not reference:
        return jsonify({"error": "Paramètre 'reference' manquant"}), 400

    try:
        # 1. Récupérer les données SeaTable
        row = seatable_get_row(reference)
        app.logger.info(f"SeaTable OK: {reference} — {row.get('Type de bien')}")

        # 1b. Appliquer les overrides (adresse, ville passés en paramètre)
        overrides = {}
        if request.is_json:
            overrides = request.get_json(silent=True) or {}
        for param in ['Adresse', 'Ville', 'Code postal', 'Surface terrain', 'Référence cadastrale']:
            val = request.args.get(param) or overrides.get(param)
            if val:
                row[param] = val
        # Overrides numériques (prix GPT)
        for param, key in [('prix_min', 'Prix estime min'), ('prix_max', 'Prix estime max'), ('prix_retenu', 'Prix retenu')]:
            val = request.args.get(param) or overrides.get(param)
            if val:
                try:
                    row[key] = float(str(val).replace(' ', '').replace(' ', '').replace(' ', ''))
                except Exception:
                    pass
        # Override avis de valeur (texte GPT)
        avis_override = request.args.get('avis_valeur') or overrides.get('avis_valeur')
        if avis_override:
            row['Avis de valeur'] = avis_override

        # 2. Générer le PDF
        pdf_buf = generate_pdf(row)
        app.logger.info(f"PDF généré: {reference}")

        # 3. Mettre à jour le statut SeaTable
        try:
            seatable_update_after_generation(reference)
        except Exception as e:
            app.logger.warning(f"Statut SeaTable non mis à jour: {e}")

        # 4. Retourner le PDF (n8n récupère le binaire et envoie l'email)
        pdf_buf.seek(0)
        filename = f"Avis_de_Valeur_{reference}.pdf"
        return send_file(
            pdf_buf,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename
        )

    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        app.logger.error(f"Erreur génération {reference}: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/generate-pdf", methods=["POST"])
def generate_direct():
    """
    POST avec body JSON contenant les données du bien directement.
    Compatible avec WF8b (webhook).
    """
    data = request.json
    if not data:
        return jsonify({"error": "Body JSON manquant"}), 400

    try:
        pdf_buf = generate_pdf(data)
        reference = data.get("reference", data.get("Reference", "inconnu"))
        filename = f"Avis_de_Valeur_{reference}.pdf"
        return send_file(
            pdf_buf,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        app.logger.error(f"Erreur generate-pdf: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500



# ─────────────────────────────────────────────────────────────────
# DOSSIER DE VENTE
# ─────────────────────────────────────────────────────────────────



# ═══════════════════════════════════════════════════════
# DOSSIER DE VENTE — v4 (assets base64, prompts riches)
# ═══════════════════════════════════════════════════════

def _st_token():
    r2 = requests.get("https://cloud.seatable.io/api/v2.1/dtable/app-access-token/",
        headers={"Authorization": f"Token {SEATABLE_TOKEN}"}, timeout=10)
    tok = r2.json()
    return tok["access_token"], tok["dtable_uuid"]


import json as _json, urllib.request as _ur, urllib.parse as _up, math as _math

def _fetch_photo_image(photo_url, access_token=None):
    """Charge une photo et retourne un ImageReader ReportLab.
    Supporte : data URLs base64, URLs HTTP(S), URLs Airtable."""
    if not photo_url:
        return None
    try:
        from reportlab.lib.utils import ImageReader as _IR
        from io import BytesIO as _BIO

        # data URL base64 (ex: data:image/jpeg;base64,...)
        if photo_url.startswith("data:"):
            _, b64data = photo_url.split(",", 1)
            import base64 as _b64_local
            raw = _b64_local.b64decode(b64data)
            return _IR(_BIO(raw))

        # URL HTTP standard (Airtable attachments, etc.)
        resp = requests.get(photo_url, timeout=15,
                            headers={"User-Agent": "BarbierImmo/1.0"})
        if resp.status_code == 200:
            ct = resp.headers.get("Content-Type", "")
            if "image" in ct or resp.content[:4] in (b"\xff\xd8\xff\xe0", b"\x89PNG", b"\xff\xd8\xff\xe1"):
                return _IR(_BIO(resp.content))
    except Exception:
        pass
    return None

from reportlab.lib.pagesizes import A4 as _A4
from reportlab.pdfgen import canvas as _canvas
from reportlab.lib import colors as _colors
from reportlab.platypus import Paragraph as _Para
from reportlab.lib.styles import ParagraphStyle as _PS
from reportlab.lib.units import mm as _mm
from PIL import Image as _PILImage

_W, _H = _A4
_BLEU   = _colors.HexColor("#16708B")   # extrait du LOGO_B64
_ORANGE = _colors.HexColor("#F0795B")   # extrait du LOGO_B64
_BLEU_F = _colors.HexColor("#0D5570")   # variante foncée du teal logo
_GRIS   = _colors.HexColor("#F3F4F6")
_GTEXTE = _colors.HexColor("#333333")
_BLANC  = _colors.white

def _ir(b64):
    from reportlab.lib.utils import ImageReader
    return ImageReader(_BytesIO(_b64.b64decode(b64)))

# ── Helpers ──────────────────────────────────────────────
def _safe(v, fb="—"): return fb if (v is None or v == "" or v == 0) else str(v)

def _pfmt(v):
    if not v: return "—"
    try: return f"{int(float(str(v).replace(' ',''))) :,}".replace(",", " ") + " €"
    except: return str(v)

def _pm2(p, s):
    try: return f"{int(float(str(p).replace(' ',''))/float(str(s).replace(' ',''))) :,}".replace(",", " ") + " €/m²"
    except: return "—"

def _footer(c, n):
    c.setFillColor(_BLEU); c.rect(0, 0, _W, 9*_mm, fill=1, stroke=0)
    c.setFillColor(_BLANC); c.setFont("Helvetica", 6.5)
    c.drawString(14*_mm, 3.5*_mm, "Barbier Immobilier — 2 place Albert Einstein, 56000 Vannes — 02.97.47.11.11 — barbierimmobilier.com")
    c.drawRightString(_W-14*_mm, 3.5*_mm, f"{n} / 6")

def _header(c, sub=""):
    c.setFillColor(_BLEU); c.rect(0, _H-11*_mm, _W, 11*_mm, fill=1, stroke=0)
    c.setFillColor(_BLANC); c.setFont("Helvetica-Bold", 8.5)
    c.drawString(14*_mm, _H-7.5*_mm, f"DOSSIER DE PRÉSENTATION  ›  {sub.upper()}")
    _logo_small(c)

def _sec(c, text, x, y):
    # Fond léger toute largeur
    c.setFillColor(_colors.HexColor("#EBF0F8"))
    c.rect(x, y+2.5*_mm, _W-28*_mm, 8*_mm, fill=1, stroke=0)
    # Barre orange gauche
    c.setFillColor(_ORANGE); c.rect(x, y+2.5*_mm, 3.5*_mm, 8*_mm, fill=1, stroke=0)
    c.setFillColor(_BLEU_F); c.setFont("Helvetica-Bold", 12)
    c.drawString(x+8*_mm, y+5*_mm, text)

def _logo(c, x, y, w=34*_mm):
    pad = 3*_mm
    logo = _ir(LOGO_B64)
    ratio = 662/488; h = w*ratio
    c.setFillColor(_BLANC)
    c.roundRect(x-pad, y-pad, w+2*pad, h+2*pad, 3*_mm, fill=1, stroke=0)
    c.drawImage(logo, x, y, width=w, height=h, mask='auto')

def _logo_small(c):
    """Logo dans le coin supérieur droit du header pages 2-6."""
    try:
        # Logo réel : 488px large × 662px haut → ratio h/w = 662/488
        # On fixe w et on calcule h pour respecter le ratio
        bar_h = 11*_mm
        w = 18*_mm          # largeur fixe — lisible sans déborder
        h = w * (662/488)   # hauteur calculée depuis le ratio réel
        # Si h dépasse la barre, on réduit
        if h > bar_h * 0.90:
            h = bar_h * 0.90
            w = h * (488/662)
        bar_top = _H - bar_h
        x = _W - w - 4*_mm
        y = bar_top + (bar_h - h) / 2
        logo = _ir(LOGO_B64)
        c.drawImage(logo, x, y, width=w, height=h, mask='auto')
    except Exception:
        pass

def _pill_picto(c, x, y, picto_b64, label, value, w=57*_mm, h=16*_mm):
    # Fond avec légère bordure
    c.setFillColor(_GRIS); c.setStrokeColor(_colors.HexColor("#D1D8E8")); c.setLineWidth(0.5)
    c.roundRect(x, y, w, h, 2*_mm, fill=1, stroke=1)
    r = 5.5*_mm; cx = x+r+2*_mm; cy = y+h/2
    c.setFillColor(_BLEU); c.circle(cx, cy, r, fill=1, stroke=0)
    try:
        ico = _ir(picto_b64)
        s = r*1.3
        c.drawImage(ico, cx-s/2, cy-s/2, width=s, height=s, mask='auto')
    except:
        c.setFillColor(_BLANC); c.setFont("Helvetica-Bold", 8)
        c.drawCentredString(cx, cy-3*_mm, "•")
    c.setFillColor(_colors.HexColor("#777777")); c.setFont("Helvetica", 6.5)
    c.drawString(x+r*2+5*_mm, y+h-4.5*_mm, label.upper())
    c.setFillColor(_BLEU_F); c.setFont("Helvetica-Bold", 9.5)
    # Auto-fit
    for fsz in [9, 8, 7]:
        c.setFont("Helvetica-Bold", fsz)
        if c.stringWidth(str(value), "Helvetica-Bold", fsz) < w-r*2-8*_mm: break
    c.drawString(x+r*2+5*_mm, y+3.5*_mm, str(value))

# ── Carte OSM ──────────────────────────────────────────────

def _get_parcelle_coords(code_insee, section, numero):
    """
    Récupère les coordonnées GPS du centroïde d'une parcelle depuis cadastre.data.gouv.fr
    Retourne (lon, lat) ou None.
    """
    import gzip as _gz
    try:
        url = f"https://cadastre.data.gouv.fr/bundler/cadastre-etalab/communes/{code_insee}/geojson/parcelles"
        req = _ur.Request(url, headers={"User-Agent": "BarbierImmo/1.0", "Accept-Encoding": "gzip"})
        with _ur.urlopen(req, timeout=20) as r:
            raw = r.read()
        try:
            data = _json.loads(_gz.decompress(raw))
        except Exception:
            data = _json.loads(raw)
        parcelle_id = f"{code_insee}000{section}{numero.zfill(4)}"
        feature = next((f for f in data.get("features", [])
                        if f["properties"].get("id") == parcelle_id), None)
        if not feature:
            # Fallback : chercher par section + numero
            feature = next((f for f in data.get("features", [])
                            if f["properties"].get("section") == section
                            and str(f["properties"].get("numero", "")) == str(int(numero))), None)
        if feature:
            coords = feature["geometry"]["coordinates"][0]
            if isinstance(coords[0][0], list):
                coords = coords[0]
            lon = sum(c[0] for c in coords) / len(coords)
            lat = sum(c[1] for c in coords) / len(coords)
            return lon, lat
    except Exception:
        pass
    return None


def _fetch_cadastre_image(ref_cadastrale, adresse="", ville=""):
    """
    Récupère une image du plan cadastral via IGN WMTS tiles.
    ref_cadastrale : ex "56034 AM 0355" ou "56034AM0355"
    Retourne une PIL.Image ou None.
    """
    import math as _m2, re as _re2
    try:
        # Parser la référence cadastrale
        ref_clean = ref_cadastrale.replace(" ","").upper()
        m = _re2.match(r"(\d{5})([A-Z]{2})(\d{3,4})", ref_clean)
        if not m:
            return None
        code_insee, section, numero = m.group(1), m.group(2), m.group(3).zfill(4)

        # 1. Coordonnées de la parcelle via cadastre.data.gouv.fr (fiable)
        coords_result = _get_parcelle_coords(code_insee, section, numero)
        if coords_result:
            lon, lat = coords_result
        else:
            # Fallback : géocodage de l'adresse
            import urllib.parse as _up_cad
            q = _up_cad.quote_plus(f"{adresse}, {ville}, France")
            geo_url = f"https://data.geopf.fr/geocodage/search?q={q}&limit=1"
            req_geo = _ur.Request(geo_url, headers={"User-Agent": "BarbierImmo/1.0"})
            with _ur.urlopen(req_geo, timeout=8) as r_geo:
                geo_data = _json.load(r_geo)
            features_geo = geo_data.get("features", [])
            if not features_geo:
                return None
            c = features_geo[0]["geometry"]["coordinates"]
            lon, lat = c[0], c[1]

        # 2. Tiles cadastraux : fond Plan IGN + parcelles en superposition
        zoom = 19
        n = 2**zoom
        cx = int((lon+180)/360*n)
        cy = int((1 - _m2.log(_m2.tan(_m2.radians(lat))+1/_m2.cos(_m2.radians(lat)))/_m2.pi)/2*n)
        tiles_grid = 3; tw, th = 256, 256

        def _fetch_tiles(layer, fmt="image/png", convert="RGBA"):
            rows_t = []
            for row in range(tiles_grid):
                ri = []
                for col in range(tiles_grid):
                    tx = cx - tiles_grid//2 + col
                    ty = cy - tiles_grid//2 + row
                    url_t = (
                        f"https://data.geopf.fr/wmts?SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile"
                        f"&LAYER={layer}&STYLE=normal"
                        f"&FORMAT={fmt}&TILEMATRIXSET=PM"
                        f"&TILEMATRIX={zoom}&TILEROW={ty}&TILECOL={tx}"
                    )
                    req_t = _ur.Request(url_t, headers={"User-Agent": "BarbierImmo/1.0"})
                    with _ur.urlopen(req_t, timeout=10) as rt:
                        tile = _PILImage.open(_BytesIO(rt.read())).convert(convert)
                    ri.append(tile)
                rows_t.append(ri)
            canvas_t = _PILImage.new(convert, (tw*tiles_grid, th*tiles_grid),
                                     (255,255,255,255) if convert=="RGBA" else (255,255,255))
            for r2 in range(tiles_grid):
                for c2 in range(tiles_grid):
                    canvas_t.paste(rows_t[r2][c2], (c2*tw, r2*th))
            return canvas_t

        # Fond : Plan IGN (fond propre blanc/gris, rues, bâtiments)
        try:
            base_img = _fetch_tiles("GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2", convert="RGB")
        except Exception:
            base_img = _PILImage.new("RGB", (tw*tiles_grid, th*tiles_grid), (245,245,242))

        # Superposition : parcelles cadastrales en transparent
        try:
            cad_overlay = _fetch_tiles("CADASTRALPARCELS.PARCELLAIRE_EXPRESS", convert="RGBA")
            # Les parcelles IGN RGBA ont fond transparent et contours colorés
            # On convertit en fond blanc pour coller proprement
            result = base_img.copy().convert("RGBA")
            result.paste(cad_overlay, mask=cad_overlay.split()[3])
            result = result.convert("RGB")
        except Exception:
            result = base_img

        # Marqueur orange au centre (position de la parcelle)
        from PIL import ImageDraw as _ID
        draw = _ID.Draw(result)
        cx_img = tw*tiles_grid//2; cy_img = th*tiles_grid//2
        r_m = 8
        draw.ellipse([cx_img-r_m, cy_img-r_m, cx_img+r_m, cy_img+r_m],
                     fill=(232,71,42), outline=(255,255,255), width=2)

        # Crop central 70%
        w, h = result.size
        mx = int(w*0.15); my = int(h*0.15)
        result = result.crop((mx, my, w-mx, h-my))
        return result

    except Exception:
        return None


def _osm_map(adresse, ville, zoom=16, tiles=3):
    import urllib.parse as up2
    q = up2.quote_plus(f"{adresse}, {ville}, France")
    url = f"https://nominatim.openstreetmap.org/search?q={q}&format=json&limit=1"
    req = _ur.Request(url, headers={"User-Agent": "BarbierImmo/1.0"})
    try:
        with _ur.urlopen(req, timeout=8) as res: data = _json.load(res)
        lat, lon = float(data[0]["lat"]), float(data[0]["lon"])
    except:
        lat, lon = 47.6580, -2.7600
    n = 2**zoom
    cx = int((lon+180)/360*n)
    cy = int((1-_math.log(_math.tan(_math.radians(lat))+1/_math.cos(_math.radians(lat)))/_math.pi)/2*n)
    half = tiles//2
    rows = []
    for row in range(tiles):
        ri = []
        for col in range(tiles):
            tx, ty = cx-half+col, cy-half+row
            u = f"https://tile.openstreetmap.org/{zoom}/{tx}/{ty}.png"
            rq = _ur.Request(u, headers={"User-Agent": "BarbierImmo/1.0"})
            with _ur.urlopen(rq, timeout=10) as res2:
                tile = _PILImage.open(_BytesIO(res2.read())).convert("RGB")
            ri.append(tile)
        rows.append(ri)
    tw, th = rows[0][0].width, rows[0][0].height
    result = _PILImage.new("RGB", (tw*tiles, th*tiles))
    for row in range(tiles):
        for col in range(tiles):
            result.paste(rows[row][col], (col*tw, row*th))
    return result, lat, lon

# ── GPT texte quartier ──────────────────────────────────────
def _gpt_quartier(adresse, ville, type_bien, surface):
    import os
    api_key = os.environ.get("OPENAI_API_KEY","")
    if not api_key: return ""
    ville_str = ville or "Vannes"
    adresse_str = adresse or ville_str
    prompt = (
        f"Tu es un expert en immobilier commercial dans le Golfe du Morbihan (Bretagne Sud).\n"
        f"Rédige un texte de présentation de la ville et du secteur, destiné à un futur locataire ou acquéreur.\n\n"
        f"Secteur : {adresse_str}, {ville_str} (Morbihan, 56)\n"
        f"Type de bien : {type_bien or 'Local commercial'} — {surface or '?'} m²\n\n"
        f"Le texte doit comporter 5 à 6 phrases riches (160-220 mots), en texte continu, sans titre ni liste.\n"
        f"Aborde obligatoirement :\n"
        f"1. L'attractivité économique de {ville_str} (bassin d'emploi, tourisme, démographie, dynamisme)\n"
        f"2. Le secteur spécifique : {adresse_str} — son positionnement, sa fréquentation, ses atouts\n"
        f"3. L'accessibilité : axes routiers, parkings, transports en commun\n"
        f"4. L'environnement commercial à proximité : enseignes, services, flux de clientèle\n"
        f"5. Pourquoi ce secteur est stratégique pour implanter une activité\n\n"
        f"Ton : éditorial, valorisant, vendeur. Pas de formule vague. Donner des éléments concrets sur {ville_str}."
    )
    payload = _json.dumps({
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 500,
        "temperature": 0.65
    }).encode()
    req = _ur.Request("https://api.openai.com/v1/chat/completions",
        data=payload, method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    with _ur.urlopen(req, timeout=30) as res:
        return _json.load(res)["choices"][0]["message"]["content"].strip()

# ══════════════════════════════════════════════════════════════
# PAGES
# ══════════════════════════════════════════════════════════════
def _page1(c, d):
    c.setFillColor(_BLEU); c.rect(0, _H*0.50, _W, _H*0.50, fill=1, stroke=0)
    # Logo dessiné en overlay APRÈS la photo (voir fin de fonction)
    # Titre
    c.setFillColor(_BLANC); c.setFont("Helvetica-Bold", 30)
    c.drawString(14*_mm, _H-42*_mm, _safe(d.get("type_bien"), "Bien immobilier"))
    c.setFont("Helvetica", 15)
    c.drawString(14*_mm, _H-53*_mm, _safe(d.get("adresse")))
    c.drawString(14*_mm, _H-62*_mm, f"{_safe(d.get('code_postal'))} {_safe(d.get('ville'))}")
    c.setFillColor(_ORANGE); c.rect(14*_mm, _H-65.5*_mm, 50*_mm, 2.5*_mm, fill=1, stroke=0)
    # Prix ou Loyer — détecter si c'est une location
    prix     = d.get("prix") or d.get("prix_retenu") or 0
    loyer_m  = d.get("loyer_mensuel") or 0
    loyer_a  = d.get("loyer_annuel") or 0
    surf     = d.get("surface")
    # Location : loyer_mensuel présent OU statut_mandat = Location
    statut_mandat = str(d.get("statut_mandat") or "").lower()
    is_location = bool(loyer_m) or "location" in statut_mandat
    if is_location and not loyer_m:
        # Location sans loyer saisi — utiliser loyer_estime_median si disponible
        loyer_estime = d.get("loyer_estime_median") or d.get("loyer_mensuel_estime") or 0
        val_affiche_fallback = not bool(loyer_estime)
    else:
        loyer_estime = 0
        val_affiche_fallback = False
    if is_location and not prix:
        prix = 0
    if is_location:
        val_affiche  = loyer_m if loyer_m else (loyer_estime if loyer_estime else None)
        label_prix   = "LOYER MENSUEL HT" if loyer_m else ("LOYER ESTIMÉ HT" if loyer_estime else "LOYER MENSUEL HT")
        suffix_val   = " HT/mois"
        show_pm2     = False
    else:
        val_affiche  = prix
        label_prix   = "PRIX DE PRÉSENTATION"
        suffix_val   = ""
        show_pm2     = bool(prix and surf)

    c.setFillColor(_BLANC); c.setFont("Helvetica", 9)
    c.drawString(14*_mm, _H-74*_mm, label_prix)
    c.setFont("Helvetica-Bold", 34)
    prix_str = _pfmt(val_affiche) if val_affiche else "—"
    if suffix_val:
        # Afficher valeur + suffix sur même ligne
        c.setFont("Helvetica-Bold", 28)
        c.drawString(14*_mm, _H-91*_mm, prix_str)
        c.setFont("Helvetica", 13); c.setFillColor(_colors.HexColor("#FFFFFFCC"))
        vw = c.stringWidth(prix_str, "Helvetica-Bold", 28)
        c.drawString(14*_mm + vw + 3*_mm, _H-91*_mm, suffix_val)
        c.setFillColor(_BLANC)
    else:
        c.setFont("Helvetica-Bold", 34)
        c.drawString(14*_mm, _H-91*_mm, prix_str)
    if show_pm2:
        c.setFont("Helvetica", 10); c.setFillColor(_colors.HexColor("#FFFFFFBB"))
        if is_location and surf:
            # Loyer annuel / surface
            try:
                loyer_an = float(str(val_affiche).replace(" ","")) * 12
                pm2_an   = loyer_an / float(str(surf).replace(" ",""))
                c.drawString(14*_mm, _H-98*_mm, f"soit {int(pm2_an):,} € HT/m²/an".replace(",", " "))
            except Exception:
                c.drawString(14*_mm, _H-98*_mm, f"soit {_pm2(val_affiche, surf)}")
        else:
            c.drawString(14*_mm, _H-98*_mm, f"soit {_pm2(val_affiche, surf)}")
    # Blocs caractéristiques blancs
    carac = [("SURFACE", f"{_safe(surf)} m²"), ("TYPE", _safe(d.get("type_bien","—")))]
    if d.get("surface_terrain"): carac.append(("TERRAIN", f"{_safe(d.get('surface_terrain'))} m²"))
    if d.get("activite"):        carac.append(("ACTIVITÉ", _safe(d.get("activite"))))
    carac = carac[:4]
    bw = (_W-28*_mm)/len(carac)-2*_mm; bh = 22*_mm; by = _H*0.49+1*_mm
    for i, (lbl, val) in enumerate(carac):
        bx = 14*_mm+i*(bw+2*_mm)
        c.setFillColor(_colors.HexColor("#00000022"))
        c.roundRect(bx+0.5*_mm, by-0.5*_mm, bw, bh, 2*_mm, fill=1, stroke=0)
        c.setFillColor(_BLANC); c.roundRect(bx, by, bw, bh, 2*_mm, fill=1, stroke=0)
        c.setFillColor(_ORANGE); c.rect(bx+2*_mm, by+bh-2*_mm, bw-4*_mm, 2*_mm, fill=1, stroke=0)
        c.setFillColor(_colors.HexColor("#888888")); c.setFont("Helvetica", 7)
        c.drawCentredString(bx+bw/2, by+bh-7*_mm, lbl)
        c.setFillColor(_BLEU_F)
        for fsz in [12,10,8,7,6]:
            c.setFont("Helvetica-Bold", fsz)
            if c.stringWidth(val, "Helvetica-Bold", fsz) < bw-4*_mm: break
        c.drawCentredString(bx+bw/2, by+5*_mm, val)
    # Zone blanche + photo principale
    c.setFillColor(_BLANC); c.rect(0, 0, _W, _H*0.50, fill=1, stroke=0)
    ph = _H*0.50-22*_mm
    px0, py0, pw0 = 14*_mm, 20*_mm, _W-28*_mm
    photos = d.get("photos") or []
    img0 = _fetch_photo_image(photos[0]) if photos else None
    if img0:
        try:
            from reportlab.lib.utils import ImageReader as _IR
            pil_img = img0._image if hasattr(img0, '_image') else None
            iw = img0.getSize()[0]; ih = img0.getSize()[1]
            scale = min(pw0/iw, ph/ih)
            dw, dh = iw*scale, ih*scale
            dx = px0 + (pw0-dw)/2; dy = py0 + (ph-dh)/2
            c.saveState()
            p = c._doc.pdfDocument if hasattr(c,'_doc') else None
            c.roundRect(px0, py0, pw0, ph, 3*_mm, fill=0, stroke=0)
            c.clipPath(c.beginPath(), stroke=0, fill=0)
            c.drawImage(img0, dx, dy, dw, dh, mask="auto")
            c.restoreState()
        except Exception:
            c.setFillColor(_GRIS); c.setStrokeColor(_colors.HexColor("#DDDDDD")); c.setLineWidth(1)
            c.roundRect(px0, py0, pw0, ph, 3*_mm, fill=1, stroke=1)
            try: c.drawImage(img0, px0, py0, pw0, ph, preserveAspectRatio=True, anchor='c', mask="auto")
            except: pass
    else:
        c.setFillColor(_GRIS); c.setStrokeColor(_colors.HexColor("#DDDDDD")); c.setLineWidth(1)
        c.roundRect(px0, py0, pw0, ph, 3*_mm, fill=1, stroke=1)
        c.setFillColor(_colors.HexColor("#BBBBBB")); c.setFont("Helvetica", 10)
        c.drawCentredString(_W/2, py0+ph/2+3*_mm, "[ Photo principale du bien ]")
        c.setFont("Helvetica", 8); c.setFillColor(_colors.HexColor("#AAAAAA"))
        c.drawCentredString(_W/2, py0+ph/2-6*_mm, "Ajoutez une photo depuis le cockpit")
    # Logo compact coin supérieur droit du bandeau bleu — pas de fond blanc
    logo_w = 28*_mm; ratio = 662/488; logo_h = logo_w * ratio
    logo_x = _W - logo_w - 8*_mm
    logo_y = _H - logo_h - 5*_mm   # 5mm du haut de page
    # Petit fond blanc arrondi juste derrière le logo
    pad = 2.5*_mm
    c.setFillColor(_BLANC)
    c.roundRect(logo_x - pad, logo_y - pad, logo_w + pad*2, logo_h + pad*2, 3*_mm, fill=1, stroke=0)
    logo_img = _ir(LOGO_B64)
    c.drawImage(logo_img, logo_x, logo_y, width=logo_w, height=logo_h, mask='auto')
    c.setFillColor(_GTEXTE); c.setFont("Helvetica", 7.5)
    c.drawString(14*_mm, 13*_mm, f"Dossier préparé par  {_safe(d.get('negociateur'),'Barbier Immobilier')}  ·  Réf. {_safe(d.get('reference'))}")
    _footer(c, 1)

def _page2(c, d):
    _header(c, f"{_safe(d.get('type_bien'))} — {_safe(d.get('adresse'))}, {_safe(d.get('ville'))}")
    _sec(c, "Présentation du bien", 14*_mm, _H-32*_mm)
    desc = _safe(d.get("description"), "Description non disponible.")
    p = _Para(desc, _PS("b", fontName="Helvetica", fontSize=9.5, textColor=_GTEXTE, leading=15, alignment=4))
    _, ph = p.wrap(_W-28*_mm, 9999); p.drawOn(c, 14*_mm, _H-38*_mm-ph)
    bot = _H-38*_mm-ph-14*_mm
    _sec(c, "Caractéristiques", 14*_mm, bot-2*_mm)
    pills = [
        (PICTO_SURFACE_B64, "Surface habitable", f"{_safe(d.get('surface'))} m²"),
        (PICTO_TYPE_B64,    "Type de bien",      _safe(d.get("type_bien"))),
        (PICTO_LIEU_B64,    "Adresse",           _safe(d.get("adresse"))),
        (PICTO_VILLE_B64,   "Ville",             _safe(d.get("ville"))),
    ]
    if d.get("surface_terrain"): pills.append((PICTO_SURFACE_B64,"Surface terrain",f"{_safe(d.get('surface_terrain'))} m²"))
    if d.get("annee_construct"): pills.append((PICTO_TYPE_B64,"Année construction",_safe(d.get("annee_construct"))))
    if d.get("ca_ht"):           pills.append((PICTO_SURFACE_B64,"CA HT annuel",_pfmt(d.get("ca_ht"))))
    if d.get("loyer_annuel"):    pills.append((PICTO_SURFACE_B64,"Loyer annuel",_pfmt(d.get("loyer_annuel"))))
    if d.get("activite"):        pills.append((PICTO_TYPE_B64,"Activité",_safe(d.get("activite"))))
    pw, ph2, pgx, pgy = 57*_mm, 16*_mm, 3*_mm, 3*_mm; cols = 3
    sy = bot-20*_mm
    for i, (b64, lbl, val) in enumerate(pills):
        col = i%cols; row2 = i//cols
        _pill_picto(c, 14*_mm+col*(pw+pgx), sy-row2*(ph2+pgy), b64, lbl, val, pw, ph2)
    pb = sy-((len(pills)-1)//cols)*(ph2+pgy)-ph2-14*_mm
    _sec(c, "Photos du bien", 14*_mm, pb)
    pw3 = (_W-28*_mm-6*_mm)/3; ph3 = 36*_mm
    photos = d.get("photos") or []
    # photos[0] = photo principale déjà affichée page 1 → on commence à l'index 1
    photos_p2 = photos[1:] if len(photos) > 1 else []
    for i in range(3):
        px = 14*_mm+i*(pw3+3*_mm); py = pb-12*_mm-ph3
        img = _fetch_photo_image(photos_p2[i]) if i < len(photos_p2) else None
        if img:
            try:
                c.saveState()
                path_clip = c.beginPath()
                path_clip.roundRect(px, py, pw3, ph3, 2*_mm)
                c.clipPath(path_clip, stroke=0, fill=0)
                c.drawImage(img, px, py, pw3, ph3, preserveAspectRatio=True, anchor='c', mask="auto")
                c.restoreState()
            except Exception:
                c.setFillColor(_GRIS); c.setStrokeColor(_colors.HexColor("#DDDDDD"))
                c.roundRect(px, py, pw3, ph3, 2*_mm, fill=1, stroke=1)
                try: c.drawImage(img, px, py, pw3, ph3, preserveAspectRatio=True, anchor='c', mask="auto")
                except: pass
        else:
            c.setFillColor(_GRIS); c.setStrokeColor(_colors.HexColor("#DDDDDD"))
            c.roundRect(px, py, pw3, ph3, 2*_mm, fill=1, stroke=1)
            c.setFillColor(_colors.HexColor("#BBBBBB")); c.setFont("Helvetica", 8)
            c.drawCentredString(px+pw3/2, py+ph3/2, f"Photo {i+2}")
    _footer(c, 2)

def _get_poi_blocks_osm(lat_c, lon_c, radius=500):
    """Interroge Overpass pour les POI à proximité. Retourne liste de (categorie, nom_poi, couleur_hex)."""
    import urllib.request as _ur3, json as _j3, urllib.parse as _up3
    # Catégories pro pertinentes avec couleur associée
    categories = [
        ("amenity", "parking",                    "Parking",       "#1B3A5C"),
        ("public_transport", "stop_position",     "Transport",     "#0D5570"),
        ("amenity", "restaurant|cafe|bar",        "Restauration",  "#E8472A"),
        ("amenity", "bank|post_office",           "Banque / Poste","#1B5C3A"),
        ("amenity", "school|university|college",  "Formation",     "#5C3A1B"),
        ("shop",    "supermarket|convenience|mall","Commerce",     "#3A1B5C"),
        ("amenity", "hospital|clinic|pharmacy",   "Sante",         "#5C1B3A"),
        ("amenity", "fuel",                       "Station-service","#3A5C1B"),
        ("amenity", "hotel|lodging",              "Hotellerie",    "#1B3A5C"),
        ("leisure", "sports_centre|fitness_centre","Sport",        "#1B5C5C"),
    ]
    results = []
    try:
        for key, values, label, color in categories:
            val_filter = "|".join(f'"{v}"' for v in values.split("|"))
            query = f'[out:json][timeout:6];(node["{key}"~{val_filter}](around:{radius},{lat_c},{lon_c});way["{key}"~{val_filter}](around:{radius},{lat_c},{lon_c}););out 3;'
            enc = _up3.quote(query)
            req = _ur3.Request(
                f"https://overpass-api.de/api/interpreter?data={enc}",
                headers={"User-Agent": "BarbierImmo/1.0"}
            )
            with _ur3.urlopen(req, timeout=7) as res:
                data = _j3.load(res)
            elements = data.get("elements", [])
            noms = []
            for el in elements:
                tags = el.get("tags", {})
                nom = tags.get("name") or tags.get("brand") or ""
                if nom and nom not in noms:
                    noms.append(nom)
                if len(noms) >= 2:
                    break
            if noms:
                val_affichee = noms[0] if len(noms[0]) <= 24 else noms[0][:22] + "…"
                results.append((label, val_affichee, color))
            if len(results) >= 6:
                break
    except Exception:
        pass
    # Ne retourner QUE ce qui est réellement trouvé par Overpass — pas d'invention
    return results[:6]


# ── PICTOS ENVIRONNEMENT (base64) ──────────────────────────────────────────────
PICTO_BANQUE_B64 = "/9j/4AAQSkZJRgABAQAAAQABAAD/4gHYSUNDX1BST0ZJTEUAAQEAAAHIAAAAAAQwAABtbnRyUkdCIFhZWiAH4AABAAEAAAAAAABhY3NwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAA9tYAAQAAAADTLQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAlkZXNjAAAA8AAAACRyWFlaAAABFAAAABRnWFlaAAABKAAAABRiWFlaAAABPAAAABR3dHB0AAABUAAAABRyVFJDAAABZAAAAChnVFJDAAABZAAAAChiVFJDAAABZAAAAChjcHJ0AAABjAAAADxtbHVjAAAAAAAAAAEAAAAMZW5VUwAAAAgAAAAcAHMAUgBHAEJYWVogAAAAAAAAb6IAADj1AAADkFhZWiAAAAAAAABimQAAt4UAABjaWFlaIAAAAAAAACSgAAAPhAAAts9YWVogAAAAAAAA9tYAAQAAAADTLXBhcmEAAAAAAAQAAAACZmYAAPKnAAANWQAAE9AAAApbAAAAAAAAAABtbHVjAAAAAAAAAAEAAAAMZW5VUwAAACAAAAAcAEcAbwBvAGcAbABlACAASQBuAGMALgAgADIAMAAxADb/2wBDAAUDBAQEAwUEBAQFBQUGBwwIBwcHBw8LCwkMEQ8SEhEPERETFhwXExQaFRERGCEYGh0dHx8fExciJCIeJBweHx7/2wBDAQUFBQcGBw4ICA4eFBEUHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh7/wAARCAIAAgADASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAj/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFAEBAAAAAAAAAAAAAAAAAAAAAP/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhEDEQA/AIyAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAB//9k="
PICTO_PARKING_B64 = "/9j/4AAQSkZJRgABAQAAAQABAAD/4gHYSUNDX1BST0ZJTEUAAQEAAAHIAAAAAAQwAABtbnRyUkdCIFhZWiAH4AABAAEAAAAAAABhY3NwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAA9tYAAQAAAADTLQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAlkZXNjAAAA8AAAACRyWFlaAAABFAAAABRnWFlaAAABKAAAABRiWFlaAAABPAAAABR3dHB0AAABUAAAABRyVFJDAAABZAAAAChnVFJDAAABZAAAAChiVFJDAAABZAAAAChjcHJ0AAABjAAAADxtbHVjAAAAAAAAAAEAAAAMZW5VUwAAAAgAAAAcAHMAUgBHAEJYWVogAAAAAAAAb6IAADj1AAADkFhZWiAAAAAAAABimQAAt4UAABjaWFlaIAAAAAAAACSgAAAPhAAAts9YWVogAAAAAAAA9tYAAQAAAADTLXBhcmEAAAAAAAQAAAACZmYAAPKnAAANWQAAE9AAAApbAAAAAAAAAABtbHVjAAAAAAAAAAEAAAAMZW5VUwAAACAAAAAcAEcAbwBvAGcAbABlACAASQBuAGMALgAgADIAMAAxADb/2wBDAAUDBAQEAwUEBAQFBQUGBwwIBwcHBw8LCwkMEQ8SEhEPERETFhwXExQaFRERGCEYGh0dHx8fExciJCIeJBweHx7/2wBDAQUFBQcGBw4ICA4eFBEUHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh7/wAARCAIAAgADASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAj/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFAEBAAAAAAAAAAAAAAAAAAAAAP/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhEDEQA/AIyAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAB//9k="
PICTO_RESTAURATION_B64 = "/9j/4AAQSkZJRgABAQAAAQABAAD/4gHYSUNDX1BST0ZJTEUAAQEAAAHIAAAAAAQwAABtbnRyUkdCIFhZWiAH4AABAAEAAAAAAABhY3NwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAA9tYAAQAAAADTLQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAlkZXNjAAAA8AAAACRyWFlaAAABFAAAABRnWFlaAAABKAAAABRiWFlaAAABPAAAABR3dHB0AAABUAAAABRyVFJDAAABZAAAAChnVFJDAAABZAAAAChiVFJDAAABZAAAAChjcHJ0AAABjAAAADxtbHVjAAAAAAAAAAEAAAAMZW5VUwAAAAgAAAAcAHMAUgBHAEJYWVogAAAAAAAAb6IAADj1AAADkFhZWiAAAAAAAABimQAAt4UAABjaWFlaIAAAAAAAACSgAAAPhAAAts9YWVogAAAAAAAA9tYAAQAAAADTLXBhcmEAAAAAAAQAAAACZmYAAPKnAAANWQAAE9AAAApbAAAAAAAAAABtbHVjAAAAAAAAAAEAAAAMZW5VUwAAACAAAAAcAEcAbwBvAGcAbABlACAASQBuAGMALgAgADIAMAAxADb/2wBDAAUDBAQEAwUEBAQFBQUGBwwIBwcHBw8LCwkMEQ8SEhEPERETFhwXExQaFRERGCEYGh0dHx8fExciJCIeJBweHx7/2wBDAQUFBQcGBw4ICA4eFBEUHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh7/wAARCAIAAgADASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAj/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFAEBAAAAAAAAAAAAAAAAAAAAAP/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhEDEQA/AIyAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAB//9k="
PICTO_TRANSPORT_B64 = "/9j/4AAQSkZJRgABAQAAAQABAAD/4gHYSUNDX1BST0ZJTEUAAQEAAAHIAAAAAAQwAABtbnRyUkdCIFhZWiAH4AABAAEAAAAAAABhY3NwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAA9tYAAQAAAADTLQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAlkZXNjAAAA8AAAACRyWFlaAAABFAAAABRnWFlaAAABKAAAABRiWFlaAAABPAAAABR3dHB0AAABUAAAABRyVFJDAAABZAAAAChnVFJDAAABZAAAAChiVFJDAAABZAAAAChjcHJ0AAABjAAAADxtbHVjAAAAAAAAAAEAAAAMZW5VUwAAAAgAAAAcAHMAUgBHAEJYWVogAAAAAAAAb6IAADj1AAADkFhZWiAAAAAAAABimQAAt4UAABjaWFlaIAAAAAAAACSgAAAPhAAAts9YWVogAAAAAAAA9tYAAQAAAADTLXBhcmEAAAAAAAQAAAACZmYAAPKnAAANWQAAE9AAAApbAAAAAAAAAABtbHVjAAAAAAAAAAEAAAAMZW5VUwAAACAAAAAcAEcAbwBvAGcAbABlACAASQBuAGMALgAgADIAMAAxADb/2wBDAAUDBAQEAwUEBAQFBQUGBwwIBwcHBw8LCwkMEQ8SEhEPERETFhwXExQaFRERGCEYGh0dHx8fExciJCIeJBweHx7/2wBDAQUFBQcGBw4ICA4eFBEUHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh7/wAARCAIAAgADASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAj/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFAEBAAAAAAAAAAAAAAAAAAAAAP/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhEDEQA/AIyAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAB//9k="
PICTO_HOTELLERIE_B64 = "/9j/4AAQSkZJRgABAQAAAQABAAD/4gHYSUNDX1BST0ZJTEUAAQEAAAHIAAAAAAQwAABtbnRyUkdCIFhZWiAH4AABAAEAAAAAAABhY3NwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAA9tYAAQAAAADTLQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAlkZXNjAAAA8AAAACRyWFlaAAABFAAAABRnWFlaAAABKAAAABRiWFlaAAABPAAAABR3dHB0AAABUAAAABRyVFJDAAABZAAAAChnVFJDAAABZAAAAChiVFJDAAABZAAAAChjcHJ0AAABjAAAADxtbHVjAAAAAAAAAAEAAAAMZW5VUwAAAAgAAAAcAHMAUgBHAEJYWVogAAAAAAAAb6IAADj1AAADkFhZWiAAAAAAAABimQAAt4UAABjaWFlaIAAAAAAAACSgAAAPhAAAts9YWVogAAAAAAAA9tYAAQAAAADTLXBhcmEAAAAAAAQAAAACZmYAAPKnAAANWQAAE9AAAApbAAAAAAAAAABtbHVjAAAAAAAAAAEAAAAMZW5VUwAAACAAAAAcAEcAbwBvAGcAbABlACAASQBuAGMALgAgADIAMAAxADb/2wBDAAUDBAQEAwUEBAQFBQUGBwwIBwcHBw8LCwkMEQ8SEhEPERETFhwXExQaFRERGCEYGh0dHx8fExciJCIeJBweHx7/2wBDAQUFBQcGBw4ICA4eFBEUHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh7/wAARCAIAAgADASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAj/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFAEBAAAAAAAAAAAAAAAAAAAAAP/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhEDEQA/AIyAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAB//9k="
PICTO_COMMERCE_B64 = "/9j/4AAQSkZJRgABAQAAAQABAAD/4gHYSUNDX1BST0ZJTEUAAQEAAAHIAAAAAAQwAABtbnRyUkdCIFhZWiAH4AABAAEAAAAAAABhY3NwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAA9tYAAQAAAADTLQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAlkZXNjAAAA8AAAACRyWFlaAAABFAAAABRnWFlaAAABKAAAABRiWFlaAAABPAAAABR3dHB0AAABUAAAABRyVFJDAAABZAAAAChnVFJDAAABZAAAAChiVFJDAAABZAAAAChjcHJ0AAABjAAAADxtbHVjAAAAAAAAAAEAAAAMZW5VUwAAAAgAAAAcAHMAUgBHAEJYWVogAAAAAAAAb6IAADj1AAADkFhZWiAAAAAAAABimQAAt4UAABjaWFlaIAAAAAAAACSgAAAPhAAAts9YWVogAAAAAAAA9tYAAQAAAADTLXBhcmEAAAAAAAQAAAACZmYAAPKnAAANWQAAE9AAAApbAAAAAAAAAABtbHVjAAAAAAAAAAEAAAAMZW5VUwAAACAAAAAcAEcAbwBvAGcAbABlACAASQBuAGMALgAgADIAMAAxADb/2wBDAAUDBAQEAwUEBAQFBQUGBwwIBwcHBw8LCwkMEQ8SEhEPERETFhwXExQaFRERGCEYGh0dHx8fExciJCIeJBweHx7/2wBDAQUFBQcGBw4ICA4eFBEUHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh7/wAARCAIAAgADASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAj/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFAEBAAAAAAAAAAAAAAAAAAAAAP/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhEDEQA/AIyAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAB//9k="
PICTO_FORMATION_B64 = "/9j/4AAQSkZJRgABAQAAAQABAAD/4gHYSUNDX1BST0ZJTEUAAQEAAAHIAAAAAAQwAABtbnRyUkdCIFhZWiAH4AABAAEAAAAAAABhY3NwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAA9tYAAQAAAADTLQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAlkZXNjAAAA8AAAACRyWFlaAAABFAAAABRnWFlaAAABKAAAABRiWFlaAAABPAAAABR3dHB0AAABUAAAABRyVFJDAAABZAAAAChnVFJDAAABZAAAAChiVFJDAAABZAAAAChjcHJ0AAABjAAAADxtbHVjAAAAAAAAAAEAAAAMZW5VUwAAAAgAAAAcAHMAUgBHAEJYWVogAAAAAAAAb6IAADj1AAADkFhZWiAAAAAAAABimQAAt4UAABjaWFlaIAAAAAAAACSgAAAPhAAAts9YWVogAAAAAAAA9tYAAQAAAADTLXBhcmEAAAAAAAQAAAACZmYAAPKnAAANWQAAE9AAAApbAAAAAAAAAABtbHVjAAAAAAAAAAEAAAAMZW5VUwAAACAAAAAcAEcAbwBvAGcAbABlACAASQBuAGMALgAgADIAMAAxADb/2wBDAAUDBAQEAwUEBAQFBQUGBwwIBwcHBw8LCwkMEQ8SEhEPERETFhwXExQaFRERGCEYGh0dHx8fExciJCIeJBweHx7/2wBDAQUFBQcGBw4ICA4eFBEUHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh7/wAARCAIAAgADASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAj/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFAEBAAAAAAAAAAAAAAAAAAAAAP/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhEDEQA/AIyAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAB//9k="
PICTO_DYNAMIQUE_B64 = "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"

# ── Versions blanches sur transparent pour affichage sur fond coloré ──
_PICTO_PARKING_W      = _invert_picto_b64(PICTO_PARKING_B64)
_PICTO_TRANSPORT_W    = _invert_picto_b64(PICTO_TRANSPORT_B64)
_PICTO_RESTAURATION_W = _invert_picto_b64(PICTO_RESTAURATION_B64)
_PICTO_COMMERCE_W     = _invert_picto_b64(PICTO_COMMERCE_B64)
_PICTO_BANQUE_W       = _invert_picto_b64(PICTO_BANQUE_B64)
_PICTO_HOTELLERIE_W   = _invert_picto_b64(PICTO_HOTELLERIE_B64)
_PICTO_FORMATION_W    = _invert_picto_b64(PICTO_FORMATION_B64)
_PICTO_DYNAMIQUE_W    = _invert_picto_b64(PICTO_DYNAMIQUE_B64)


def _draw_poi_icon(c, cat, cx, cy, r, col):
    """Dessine un symbole lisible dans le cercle bleu selon la catégorie POI."""
    cat_up = cat.upper()
    c.setFillColor(_BLANC)
    # Mapping catégorie → lettre ou symbole court — toujours lisible en PDF
    if "PARKING" in cat_up:
        c.setFont("Helvetica-Bold", r * 1.5)
        c.drawCentredString(cx, cy - r * 0.5, "P")
    elif "TRANSPORT" in cat_up or "BUS" in cat_up or "GARE" in cat_up:
        c.setFont("Helvetica-Bold", r * 1.3)
        c.drawCentredString(cx, cy - r * 0.45, "T")
    elif "RESTAURATION" in cat_up or "CAFE" in cat_up or "RESTAURANT" in cat_up:
        c.setFont("Helvetica-Bold", r * 1.3)
        c.drawCentredString(cx, cy - r * 0.45, "R")
    elif "COMMERCE" in cat_up or "MAGASIN" in cat_up:
        c.setFont("Helvetica-Bold", r * 1.3)
        c.drawCentredString(cx, cy - r * 0.45, "C")
    elif "BANQUE" in cat_up or "SERVICE" in cat_up:
        c.setFont("Helvetica-Bold", r * 1.3)
        c.drawCentredString(cx, cy - r * 0.45, "B")
    elif "SANTE" in cat_up or "SANTÉ" in cat_up or "PHARMAC" in cat_up or "MEDICAL" in cat_up:
        c.setFont("Helvetica-Bold", r * 1.5)
        c.drawCentredString(cx, cy - r * 0.5, "+")
    else:
        c.setFont("Helvetica-Bold", r * 1.3)
        c.drawCentredString(cx, cy - r * 0.45, "·")
def _draw_poi_card(c, bx, by, bw, bh, label, valeur, color_hex):
    """Bloc POI — style identique aux pills caracteristiques page 2 via _pill_picto."""
    # Utilise _pill_picto avec le picto correspondant a la categorie
    import unicodedata as _ud
    def _safe_str(s):
        try:
            str(s).encode('latin-1'); return str(s)
        except:
            return _ud.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode('ascii')
    cat = _safe_str(label).upper()
    PICTO_MAP = {
        "PARKING":      _PICTO_PARKING_W,
        "TRANSPORT":    _PICTO_TRANSPORT_W,
        "RESTAURATION": _PICTO_RESTAURATION_W,
        "COMMERCE":     _PICTO_COMMERCE_W,
        "BANQUE":       _PICTO_BANQUE_W,
        "HOTELLERIE":   _PICTO_HOTELLERIE_W,
        "FORMATION":    _PICTO_FORMATION_W,
        "DYNAMIQUE":    _PICTO_DYNAMIQUE_W,
        "SANTE":        _PICTO_BANQUE_W,
    }
    picto = next((v for k, v in PICTO_MAP.items() if k in cat), PICTO_LIEU_B64)
    _pill_picto(c, bx, by, picto, _safe_str(label), _safe_str(valeur), w=bw, h=bh)


def _page3(c, d):
    _header(c, "Quartier & environnement")
    _sec(c, "Le quartier", 14*_mm, _H-32*_mm)
    texte = d.get("texte_quartier") or (
        f"Situe a {_safe(d.get('ville','Vannes'))}, ce bien beneficie d'une localisation strategique "
        "dans un secteur economiquement actif du Morbihan. L'accessibilite est optimale grace a la "
        "proximite de la rocade et des axes principaux. Le secteur compte de nombreux commerces, "
        "services et equipements a proximite immediate, offrant un environnement favorable a "
        "l'exploitation d'une activite commerciale ou professionnelle."
    )
    p = _Para(texte, _PS("b", fontName="Helvetica", fontSize=9.5, textColor=_GTEXTE, leading=15))
    _, ph = p.wrap(_W-28*_mm, 9999)
    # Limiter dynamiquement si trop haut (garder au moins 80mm pour la carte)
    max_text_h = _H - 38*_mm - 80*_mm
    if ph > max_text_h and max_text_h > 0:
        # Recalculer avec taille réduite
        for fsz in [9, 8, 7.5, 7]:
            p2 = _Para(texte, _PS("b2", fontName="Helvetica", fontSize=fsz, textColor=_GTEXTE, leading=fsz*1.5))
            _, ph2 = p2.wrap(_W-28*_mm, 9999)
            if ph2 <= max_text_h:
                p = p2; ph = ph2
                break
        else:
            # Même à 7pt ça déborde : tronquer le texte par les phrases
            _sentences = texte.replace(". ", ".|").split("|")
            _kept = []
            for _fsz_final in [7.5, 7]:
                _kept = []
                for _s in _sentences:
                    _candidate = " ".join(_kept + [_s])
                    _pt = _Para(_candidate, _PS("bt", fontName="Helvetica", fontSize=_fsz_final, textColor=_GTEXTE, leading=_fsz_final*1.5))
                    _, _ph = _pt.wrap(_W-28*_mm, 9999)
                    if _ph <= max_text_h:
                        _kept.append(_s)
                    else:
                        break
                if _kept:
                    _texte_final = " ".join(_kept)
                    p = _Para(_texte_final, _PS("bf", fontName="Helvetica", fontSize=_fsz_final, textColor=_GTEXTE, leading=_fsz_final*1.5))
                    _, ph = p.wrap(_W-28*_mm, 9999)
                    break
    p.drawOn(c, 14*_mm, _H-38*_mm-ph)
    qbot = _H-38*_mm-ph-10*_mm

    _sec(c, "Localisation", 14*_mm, qbot-2*_mm)
    mh = 72*_mm; mx = 14*_mm; mw = _W-28*_mm; my = qbot-14*_mm-mh

    # ── Carte OSM ──────────────────────────────────────────────────────────
    lat = lon = None
    try:
        osm, lat, lon = _osm_map(_safe(d.get("adresse"),""), _safe(d.get("ville"),"Vannes"))
        iw, ih = osm.size; tr = mw/mh
        if iw/ih > tr:
            nw = int(ih*tr); osm = osm.crop(((iw-nw)//2,0,(iw-nw)//2+nw,ih))
        else:
            nh = int(iw/tr); osm = osm.crop((0,(ih-nh)//2,iw,(ih-nh)//2+nh))
        buf2 = _BytesIO(); osm.save(buf2, format="PNG"); buf2.seek(0)
        from reportlab.lib.utils import ImageReader as _IR2
        c.drawImage(_IR2(buf2), mx, my, width=mw, height=mh)
        # Marqueur orange centré
        px2 = mx+mw/2; py2 = my+mh/2
        c.setFillColor(_ORANGE); c.circle(px2, py2, 4.5*_mm, fill=1, stroke=0)
        c.setFillColor(_BLANC); c.setFont("Helvetica-Bold", 10)
        c.drawCentredString(px2, py2-3.5*_mm, "+")
        # Bulle adresse
        adr = f"{_safe(d.get('adresse'))}, {_safe(d.get('ville'))}"
        bwb = min(c.stringWidth(adr,"Helvetica-Bold",7)+16, mw-20)
        c.setFillColor(_BLANC); c.setStrokeColor(_colors.HexColor("#AAAAAA")); c.setLineWidth(0.5)
        c.roundRect(px2-bwb/2, py2+7*_mm, bwb, 9*_mm, 1.5*_mm, fill=1, stroke=1)
        c.setFillColor(_BLEU_F); c.setFont("Helvetica-Bold", 7)
        c.drawCentredString(px2, py2+11*_mm, adr)
        # Bordure carte
        c.setStrokeColor(_colors.HexColor("#CCCCCC")); c.setLineWidth(0.8)
        c.roundRect(mx, my, mw, mh, 3*_mm, fill=0, stroke=1)
        # Copyright OSM
        c.setFillColor(_colors.HexColor("#FFFFFF88")); c.rect(mx, my, mw, 5*_mm, fill=1, stroke=0)
        c.setFillColor(_colors.HexColor("#666666")); c.setFont("Helvetica", 5.5)
        c.drawRightString(mx+mw-2*_mm, my+1.5*_mm, "© OpenStreetMap contributors")
    except Exception as e:
        c.setFillColor(_colors.HexColor("#E8F0F4")); c.roundRect(mx,my,mw,mh,3*_mm,fill=1,stroke=0)
        c.setFillColor(_colors.HexColor("#AAAAAA")); c.setFont("Helvetica",8)
        c.drawCentredString(_W/2, my+mh/2, "Carte indisponible")

    # ── POI réels via Overpass (utilise lat/lon de la carte) ───────────────
    POI_CATS_PRO = {"parking", "transport", "restauration", "commerce", "banque", "sante"}
    poi_blocks = []
    if lat and lon:
        try:
            raw_blocks = _get_poi_blocks_osm(lat, lon, radius=500)
            poi_blocks = [b for b in raw_blocks if b[0].lower() in POI_CATS_PRO]
        except Exception:
            pass

    # Si Overpass insuffisant, enrichir avec GPT (POI certains uniquement)
    if len(poi_blocks) < 3:
        try:
            import os as _os_poi, json as _j_poi, urllib.request as _ur_poi
            api_key = _os_poi.environ.get("OPENAI_API_KEY", "")
            if api_key:
                adresse_poi = d.get("adresse","")
                ville_poi = d.get("ville","")
                type_bien_poi = d.get("type_bien","local commercial")
                prompt_poi = (
                    "Tu es expert en immobilier commercial dans le Morbihan."
                    f" Pour : {type_bien_poi} au {adresse_poi}, {ville_poi},"
                    " liste les points d'interet REELS certains dans un rayon de 500m."
                    " Reponds UNIQUEMENT en JSON (sans backticks ni markdown) :"
                    ' [{"categorie":"Parking","nom":"Nom exact ou description"}]'
                    " Categories : Parking, Transport, Restauration, Commerce, Banque, Sante."
                    " N'inclus QUE ce dont tu es certain. Si incertain = ne pas inclure."
                    " Maximum 6 elements."
                )
                gpt_payload = _j_poi.dumps({
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt_poi}],
                    "max_tokens": 400, "temperature": 0.1
                }).encode()
                req_poi = _ur_poi.Request(
                    "https://api.openai.com/v1/chat/completions",
                    data=gpt_payload, method="POST",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                )
                with _ur_poi.urlopen(req_poi, timeout=20) as rp:
                    resp_poi = _j_poi.load(rp)
                raw_poi = resp_poi["choices"][0]["message"]["content"].strip()
                raw_poi = raw_poi.strip("`").strip()
                if raw_poi.startswith("json"):
                    raw_poi = raw_poi[4:].strip()
                pois_gpt = _j_poi.loads(raw_poi)
                cat_colors = {
                    "Parking":"#1B3A5C","Transport":"#0D5570","Restauration":"#E8472A",
                    "Commerce":"#3A1B5C","Formation":"#5C3A1B","Banque":"#1B5C3A",
                    "Sante":"#5C1B3A","Dynamisme":"#1B5C5C"
                }
                existing_cats = {r[0] for r in poi_blocks}
                for poi_item in pois_gpt:
                    cat = poi_item.get("categorie","")
                    nom = poi_item.get("nom","")
                    if cat and nom and cat not in existing_cats and len(poi_blocks) < 6:
                        poi_blocks.append((cat, nom[:28], cat_colors.get(cat,"#1B3A5C")))
                        existing_cats.add(cat)
        except Exception:
            pass  # GPT indisponible ou JSON invalide : on garde ce qu'Overpass a trouvé

    # ── Zone 1 : POI quartier (Overpass — ce qui existe autour) ────────────
    # Filtrer catégories non pertinentes pour l'immobilier pro
    _CATS_PRO = {"parking", "transport", "restauration", "commerce", "banque", "sante", "santé"}
    poi_blocks = [b for b in poi_blocks if b[0].lower() in _CATS_PRO]

    _sec(c, "Environnement du quartier", 14*_mm, my - 14*_mm)
    pt_y = my - 24*_mm
    ncols = 3; card_w = (_W-28*_mm - (ncols-1)*4*_mm)/ncols; card_h = 16*_mm
    for i, item in enumerate(poi_blocks[:6]):
        lbl, val, col_hex = item if len(item) == 3 else (item[0], item[1], "#1B3A5C")
        col_idx = i % ncols; row_idx = i // ncols
        bx = 14*_mm + col_idx*(card_w+4*_mm)
        by = pt_y - row_idx*(card_h+3*_mm) - card_h
        _draw_poi_card(c, bx, by, card_w, card_h, lbl, val, col_hex)

    # ── Zone 2 : Caractéristiques du bien (données Airtable uniquement) ────
    # N'affiche QUE ce qui est explicitement renseigné dans les données
    carac_bien = []

    parking_val = d.get("parking") or ""
    if parking_val and str(parking_val).strip() not in ("", "0", "False", "Non", "nan"):
        carac_bien.append(("Parking", str(parking_val), "#1B3A5C"))

    pmr = d.get("pmr") or d.get("PMR") or ""
    if str(pmr).strip() in ("Oui", "oui", "True", "1", "true"):
        carac_bien.append(("Accessibilite PMR", "Acces PMR", "#0D5570"))

    dpe = d.get("dpe_classe") or d.get("DPE") or ""
    if str(dpe).strip() not in ("", "nan", "None"):
        carac_bien.append(("DPE", f"Classe {dpe}", "#5C3A1B"))

    bail = d.get("type_bail") or d.get("bail") or ""
    if str(bail).strip() not in ("", "nan", "None"):
        carac_bien.append(("Type de bail", str(bail), "#3A1B5C"))

    etat = d.get("etat_bien") or d.get("etat") or ""
    if str(etat).strip() not in ("", "nan", "None", "—"):
        carac_bien.append(("Etat", str(etat), "#1B5C3A"))

    taxe = d.get("taxe_fonciere") or d.get("taxe") or 0
    if taxe and float(str(taxe).replace(" ","")) > 0:
        try:
            taxe_fmt = f"{int(float(str(taxe).replace(' ',''))) :,}".replace(","," ") + " EUR/an"
        except Exception:
            taxe_fmt = str(taxe)
        carac_bien.append(("Taxe fonciere", taxe_fmt, "#5C1B3A"))

    if carac_bien:
        carac_y = pt_y - 2*(card_h+3*_mm) - 10*_mm
        _sec(c, "Caracteristiques du bien", 14*_mm, carac_y + 4*_mm)
        carac_y2 = carac_y - 4*_mm
        for i, (lbl, val, col_hex) in enumerate(carac_bien[:6]):
            col_idx = i % ncols; row_idx = i // ncols
            bx = 14*_mm + col_idx*(card_w+4*_mm)
            by = carac_y2 - row_idx*(card_h+3*_mm) - card_h
            _draw_poi_card(c, bx, by, card_w, card_h, lbl, val, col_hex)

    # ── Plan cadastral + Zone PLU ────────────────────────────────────────────
    ref_cad  = d.get("ref_cadastrale","")
    zone_plu = d.get("zone_plu","") or d.get("Zone PLU","") or ""
    res_plu  = d.get("resume_plu","") or d.get("Résumé PLU","") or ""
    url_regl = d.get("url_reglement","") or d.get("URL Règlement PLU","") or ""

    # Calculer la position de départ (sous les 2 rangées de POI + section carac)
    nb_carac_rows = (len(carac_bien) + 2) // 3 if carac_bien else 0
    cad_start_y = pt_y - 2*(card_h+3*_mm) - (nb_carac_rows*(card_h+3*_mm) if carac_bien else 0) - 16*_mm

    if ref_cad and len(ref_cad) >= 6:
        _sec(c, "Urbanisme & Cadastre", 14*_mm, cad_start_y + 6*_mm)
        cad_top = cad_start_y - 4*_mm

        # ── Bloc Zone PLU (si disponible) ─────────────────────────────────────
        plu_drawn_h = 0
        if zone_plu or res_plu:
            plu_h = 0
            plu_content = []
            if zone_plu:
                plu_content.append(("zone", zone_plu))
            if res_plu:
                plu_content.append(("resume", res_plu))

            # Calculer hauteur du bloc résumé PLU
            if res_plu:
                plu_para = _Para(res_plu, _PS("plu", fontName="Helvetica", fontSize=8, textColor=_GTEXTE, leading=12))
                _, para_h = plu_para.wrap(_W-36*_mm, 9999)
                plu_h = max(22*_mm, para_h + 18*_mm)
            else:
                plu_h = 16*_mm

            plu_y = cad_top - plu_h
            # Fond bleu clair
            c.setFillColor(_colors.HexColor("#E8F4F8"))
            c.roundRect(14*_mm, plu_y, _W-28*_mm, plu_h, 2*_mm, fill=1, stroke=0)
            # Pastille zone
            c.setFillColor(_BLEU_F)
            c.roundRect(14*_mm, plu_y + plu_h - 10*_mm, 30*_mm, 9*_mm, 1*_mm, fill=1, stroke=0)
            c.setFillColor(_BLANC); c.setFont("Helvetica-Bold", 7)
            c.drawCentredString(29*_mm, plu_y + plu_h - 6.5*_mm, f"Zone {zone_plu}")
            # Label
            c.setFillColor(_BLEU_F); c.setFont("Helvetica-Bold", 7.5)
            c.drawString(48*_mm, plu_y + plu_h - 6.5*_mm, "ZONE PLU")
            # Résumé
            if res_plu:
                plu_para.drawOn(c, 18*_mm, plu_y + 6*_mm)
            # Lien règlement
            if url_regl:
                c.setFillColor(_colors.HexColor("#888888")); c.setFont("Helvetica-Oblique", 6)
                c.drawString(18*_mm, plu_y + 2*_mm, f"Règlement : {url_regl[:70]}")

            cad_top = plu_y - 6*_mm
            plu_drawn_h = plu_h + 6*_mm

        # ── Image cadastrale ───────────────────────────────────────────────────
        try:
            cad_img = _fetch_cadastre_image(ref_cad, d.get("adresse",""), d.get("ville",""))
            if cad_img:
                cad_h2 = 45*_mm
                cad_y2 = cad_top - cad_h2
                buf_cad = _BytesIO(); cad_img.save(buf_cad, "PNG"); buf_cad.seek(0)
                from reportlab.lib.utils import ImageReader as _IRC2
                c.saveState()
                p_cad = c.beginPath(); p_cad.roundRect(14*_mm, cad_y2, _W-28*_mm, cad_h2, 2*_mm)
                c.clipPath(p_cad, stroke=0, fill=0)
                c.drawImage(_IRC2(buf_cad), 14*_mm, cad_y2, _W-28*_mm, cad_h2,
                            preserveAspectRatio=False, mask="auto")
                c.restoreState()
                c.setStrokeColor(_colors.HexColor("#CCCCCC")); c.setLineWidth(0.5)
                c.roundRect(14*_mm, cad_y2, _W-28*_mm, cad_h2, 2*_mm, fill=0, stroke=1)
                c.setFillColor(_colors.HexColor("#999999")); c.setFont("Helvetica", 5.5)
                c.drawRightString(_W-14*_mm, cad_y2+1.5*_mm, "© IGN / data.geopf.fr — Plan cadastral")
        except Exception:
            pass

    _footer(c, 3)

def _page4(c, comparables, d):
    # Détecter si c'est une location
    _is_loc_p4 = bool(d.get("loyer_mensuel")) or "location" in str(d.get("statut_mandat","")).lower()

    if _is_loc_p4:
        # ── PAGE 4 LOCATION : Positionnement loyer de marché ──────────────────
        _header(c, "Positionnement loyer")
        _sec(c, "Loyers de marché de référence", 14*_mm, _H-32*_mm)

        # Données transmises par le cockpit depuis 02_Loyers_Marche
        _pm2_min = d.get("loyer_pm2_min") or 0
        _pm2_max = d.get("loyer_pm2_max") or 0
        _pm2_med = d.get("loyer_pm2_median") or 0
        _loyer_m = float(str(d.get("loyer_mensuel") or 0).replace(" ",""))
        _surf     = float(str(d.get("surface") or 0).replace(" ",""))
        _dvf_src  = d.get("dvf_source") or "Référentiel marché Barbier"
        _notes    = d.get("loyer_notes") or ""
        _ville_m  = d.get("loyer_ville_match") or d.get("ville") or ""

        # Bloc intro
        _loyer_m2_actuel = (_loyer_m * 12 / _surf) if _surf > 0 and _loyer_m > 0 else 0
        intro_txt = (
            f"Positionnement du loyer proposé ({int(_loyer_m):,} € HT/mois) au regard des références de marché "
            f"pour ce type de bien à {_ville_m}.".replace(",", " ")
            if _loyer_m else
            f"Références de marché pour ce type de bien à {_ville_m}."
        )
        intro = _Para(intro_txt, _PS("sm", fontName="Helvetica", fontSize=9, textColor=_GTEXTE, leading=13))
        _, ih = intro.wrap(_W-28*_mm, 9999)
        intro.drawOn(c, 14*_mm, _H-40*_mm-ih)

        ct = _H-42*_mm-ih-6*_mm

        if _pm2_med > 0:
            # 3 blocs : fourchette basse / médiane / haute
            bw = (_W-28*_mm-8*_mm)/3; bh = 38*_mm; gap = 4*_mm
            cols_data = [
                ("FOURCHETTE BASSE", f"{_pm2_min} €/m²/an", f"{int(_pm2_min*_surf/12):,} €/mois".replace(",", " ") if _surf else ""),
                ("MÉDIANE MARCHÉ", f"{_pm2_med} €/m²/an", f"{int(_pm2_med*_surf/12):,} €/mois".replace(",", " ") if _surf else ""),
                ("FOURCHETTE HAUTE", f"{_pm2_max} €/m²/an", f"{int(_pm2_max*_surf/12):,} €/mois".replace(",", " ") if _surf else ""),
            ]
            for i, (lbl, val_m2, val_mois) in enumerate(cols_data):
                bx = 14*_mm + i*(bw+gap)
                by = ct - bh
                is_med = (i == 1)
                bg = _BLEU if is_med else _colors.HexColor("#E8F0F8")
                c.setFillColor(bg); c.roundRect(bx, by, bw, bh, 3*_mm, fill=1, stroke=0)
                lbl_col = _BLANC if is_med else _colors.HexColor("#777777")
                val_col = _BLANC if is_med else _BLEU_F
                c.setFillColor(lbl_col); c.setFont("Helvetica", 7)
                c.drawCentredString(bx+bw/2, by+bh-7*_mm, lbl)
                c.setFillColor(val_col); c.setFont("Helvetica-Bold", 13)
                c.drawCentredString(bx+bw/2, by+bh/2+1*_mm, val_m2)
                c.setFillColor(val_col); c.setFont("Helvetica", 9)
                c.drawCentredString(bx+bw/2, by+bh/2-8*_mm, val_mois)

            # Loyer actuel si renseigné
            if _loyer_m > 0 and _loyer_m2_actuel > 0:
                arrow_x = 14*_mm + (_loyer_m2_actuel - _pm2_min) / max(_pm2_max - _pm2_min, 1) * (_W-28*_mm)
                arrow_x = max(16*_mm, min(arrow_x, _W-16*_mm))
                ay = ct - bh - 8*_mm
                c.setFillColor(_ORANGE)
                c.drawCentredString(arrow_x, ay, "▲")
                c.setFont("Helvetica-Bold", 8); c.setFillColor(_BLEU_F)
                c.drawCentredString(arrow_x, ay-5*_mm, f"Loyer proposé : {int(_loyer_m2_actuel)} €/m²/an")

            # Source
            src_y = ct - bh - 16*_mm
            c.setFillColor(_colors.HexColor("#999999")); c.setFont("Helvetica-Oblique", 7)
            c.drawString(14*_mm, src_y, f"Source : {_dvf_src}")
            if _notes:
                c.drawString(14*_mm, src_y-4*_mm, _notes)
        else:
            # Pas de données — afficher message simple
            c.setFillColor(_GRIS); c.roundRect(14*_mm, ct-50*_mm, _W-28*_mm, 50*_mm, 3*_mm, fill=1, stroke=0)
            c.setFillColor(_colors.HexColor("#AAAAAA")); c.setFont("Helvetica-Oblique", 9)
            c.drawCentredString(_W/2, ct-25*_mm, "Référentiel loyer non disponible pour ce secteur")
            c.setFont("Helvetica", 7.5)
            c.drawCentredString(_W/2, ct-33*_mm, "Mettre à jour la table 02_Loyers_Marche dans Airtable")

        _footer(c, 4)
        return  # Sortir — pas de suite vente

    # ── PAGE 4 VENTE : Biens comparables DVF ──────────────────────────────────
    _header(c, "Biens comparables"); _sec(c,"Analyse des biens comparables",14*_mm,_H-32*_mm)
    intro = _Para("Sélection des transactions les plus récentes permettant de positionner ce bien dans son marché local.",
        _PS("sm",fontName="Helvetica",fontSize=9,textColor=_GTEXTE,leading=13))
    _,ih = intro.wrap(_W-28*_mm,9999); intro.drawOn(c,14*_mm,_H-40*_mm-ih)
    ct=_H-42*_mm-ih-6*_mm; ch=50*_mm
    if not comparables:
        c.setFillColor(_GRIS); c.roundRect(14*_mm,ct-ch,_W-28*_mm,ch,3*_mm,fill=1,stroke=0)
        c.setFillColor(_colors.HexColor("#AAAAAA")); c.setFont("Helvetica-Oblique", 9)
        c.drawCentredString(_W/2, ct-ch/2, "Aucun comparable disponible — relancer la recherche dans 01_Biens")
    else:
        # Layout: 2 columns x up to 2 rows (max 4 cards)
        cards = comparables[:4]
        ncols = 2; gap = 4*_mm
        cw = (_W - 28*_mm - gap) / ncols
        row_gap = 4*_mm
        for i, comp in enumerate(cards):
            col = i % ncols; row = i // ncols
            cx2 = 14*_mm + col*(cw+gap)
            cy2 = ct - ch - row*(ch+row_gap)
            pc = comp.get("Prix",0); sc2 = comp.get("Surface",0)
            st = comp.get("Statut","—"); src = str(comp.get("Source","") or "")
            date_raw = str(comp.get("Date","") or ""); yr = date_raw[:4] if date_raw else "—"
            c.setFillColor(_GRIS); c.roundRect(cx2,cy2,cw,ch,3*_mm,fill=1,stroke=0)
            # Badge statut
            badge_col = _BLEU if st=="Vendu" else _ORANGE
            c.setFillColor(badge_col)
            c.roundRect(cx2+cw-26*_mm,cy2+ch-8*_mm,24*_mm,6.5*_mm,1*_mm,fill=1,stroke=0)
            c.setFillColor(_BLANC); c.setFont("Helvetica-Bold",6)
            c.drawCentredString(cx2+cw-14*_mm,cy2+ch-5*_mm,str(st).upper())
            # Numéro
            c.setFillColor(_BLEU); c.circle(cx2+8*_mm,cy2+ch-7.5*_mm,5.5*_mm,fill=1,stroke=0)
            c.setFillColor(_BLANC); c.setFont("Helvetica-Bold",9); c.drawCentredString(cx2+8*_mm,cy2+ch-9.5*_mm,str(i+1))
            # Adresse (ligne 1 : -17mm, ligne 2 ville : -23mm)
            c.setFillColor(_BLEU_F); c.setFont("Helvetica-Bold",8)
            c.drawString(cx2+3*_mm,cy2+ch-18*_mm,str(comp.get("Adresse","—"))[:28])
            c.setFillColor(_GTEXTE); c.setFont("Helvetica",7.5)
            c.drawString(cx2+3*_mm,cy2+ch-24*_mm,str(comp.get("Ville","")))
            # Prix (-32mm) + prix/m² (-38mm) — 6mm d'écart
            c.setFillColor(_ORANGE); c.setFont("Helvetica-Bold",12)
            c.drawString(cx2+3*_mm,cy2+ch-32*_mm,_pfmt(pc))
            c.setFillColor(_GTEXTE); c.setFont("Helvetica",7.5)
            c.drawString(cx2+3*_mm,cy2+ch-38.5*_mm,_pm2(pc,sc2))
            # Ligne séparatrice à -41mm (9mm au-dessus du bas)
            c.setStrokeColor(_colors.HexColor("#DDDDDD")); c.setLineWidth(0.5)
            c.line(cx2+3*_mm,cy2+ch-42*_mm,cx2+cw-3*_mm,cy2+ch-42*_mm)
            # Infos bas : 2 lignes sous la séparatrice
            src_short = src.replace(" (vendu)","").replace("DVF ","DVF ").strip()[:10]
            c.setFillColor(_GTEXTE); c.setFont("Helvetica",6.5)
            c.drawString(cx2+3*_mm,cy2+7*_mm,f"{_safe(sc2)} m²")
            c.drawString(cx2+3*_mm,cy2+2.5*_mm,f"{src_short}  ·  {yr}")
    # Synthèse : décalée selon nb de lignes de cartes
    nrows_cards = (len(comparables[:4]) + 1) // 2 if comparables else 1
    sy = ct - nrows_cards*ch - (nrows_cards-1)*4*_mm - 14*_mm
    _sec(c,"Synthèse marché",14*_mm,sy+2*_mm)
    if comparables:
        try:
            pl=[float(str(x.get("Prix",0)).replace(" ","").replace(" ","")) for x in comparables if x.get("Prix")]
            sl=[float(str(x.get("Surface",0)).replace(" ","")) for x in comparables if x.get("Surface")]
            mp=int(sum(pl)/len(pl)) if pl else 0
            mm2v=int(sum(p/s for p,s in zip(pl,sl))/len(pl)) if (pl and sl) else 0
        except: mp=mm2v=0
        # Année la plus récente
        dates=[str(x.get("Date",""))[:4] for x in comparables if x.get("Date")]
        yr_max=max(dates) if dates else "—"
        vs=[_pfmt(mp),f"{mm2v:,} €/m²".replace(",","") if mm2v else "—",yr_max]
        ls=["Prix moyen constaté","Prix moyen au m²","Année réf. la + récente"]
    else:
        vs=["—","—","—"]; ls=["Prix moyen constaté","Prix moyen au m²","Année réf. la + récente"]
    mw2=(_W-28*_mm-8*_mm)/3
    for i,(l,v) in enumerate(zip(ls,vs)):
        mx2=14*_mm+i*(mw2+4*_mm); my2=sy-18*_mm
        c.setFillColor(_BLEU); c.roundRect(mx2,my2,mw2,16*_mm,2*_mm,fill=1,stroke=0)
        c.setFillColor(_BLANC); c.setFont("Helvetica-Bold",11); c.drawCentredString(mx2+mw2/2,my2+9*_mm,v)
        c.setFont("Helvetica",7); c.drawCentredString(mx2+mw2/2,my2+4*_mm,l)
    c.setFillColor(_colors.HexColor("#999999")); c.setFont("Helvetica-Oblique",7)
    c.drawString(14*_mm,sy-22*_mm,"Sources : DVF (data.gouv.fr) — Mutations de valeurs foncières, données officielles.")
    _footer(c,4)


def _draw_atouts_cards(c, d, x, y, total_w):
    """4 cartes d'atouts générées par GPT — biais cognitifs — full width."""
    import json as _json
    # Récupérer les atouts GPT ou construire fallback
    atouts_raw = d.get("atouts_gpt") or ""
    atouts = []
    if atouts_raw:
        try:
            atouts = _json.loads(atouts_raw)
        except Exception:
            pass
    if not atouts or len(atouts) < 4:
        # Fallback générique basé sur les données du bien
        surf = _safe(d.get("surface"))
        ville = _safe(d.get("ville"))
        type_bien = _safe(d.get("type_bien"), "bien")
        atouts = [
            {"titre": "LOCALISATION PRIME", "texte": f"Au cœur de {ville}, ce {type_bien.lower()} bénéficie d'une visibilité immédiate et d'un accès fluide pour vos clients et collaborateurs."},
            {"titre": "FORMAT OPTIMISÉ", "texte": f"{surf} m² agencés pour maximiser la productivité. Une surface rare sur ce secteur, prisée des professions libérales et PME."},
            {"titre": "ZONE EN ESSOR", "texte": "Secteur à forte dynamique économique. Vos clients vous trouvent facilement, vos équipes s'y installent durablement."},
            {"titre": "DISPONIBILITÉ IMMÉDIATE", "texte": "Bien disponible rapidement. Les opportunités de cette qualité se louent vite — prenez de l'avance sur vos concurrents."},
        ]
    # Mise en page : 2 colonnes × 2 lignes
    gap = 3 * _mm
    card_w = (total_w - gap) / 2
    card_h = 22 * _mm
    row_gap = 3 * _mm
    for i, atout in enumerate(atouts[:4]):
        col = i % 2
        row = i // 2
        cx = x + col * (card_w + gap)
        cy = y - card_h - row * (card_h + row_gap)
        c.setFillColor(_BLEU)
        c.roundRect(cx, cy, card_w, card_h, 2 * _mm, fill=1, stroke=0)
        c.setFillColor(_ORANGE)
        c.roundRect(cx, cy + card_h - 2.5 * _mm, card_w, 2.5 * _mm, 2 * _mm, fill=1, stroke=0)
        titre = atout.get("titre", "").upper()
        c.setFillColor(_ORANGE)
        c.setFont("Helvetica-Bold", 7)
        c.drawString(cx + 3 * _mm, cy + card_h - 7 * _mm, titre[:28])
        texte = atout.get("texte", "")
        para = _Para(texte, _PS("ac", fontName="Helvetica", fontSize=6.8,
                                textColor=_BLANC, leading=10, alignment=4))
        tw = card_w - 6 * _mm
        _, ph = para.wrap(tw, 9999)
        para.drawOn(c, cx + 3 * _mm, cy + card_h - 10 * _mm - ph)

def _page5(c, d):
    statut_m = str(d.get("statut_mandat") or "").lower()
    is_loc = bool(d.get("loyer_mensuel")) or "location" in statut_m
    loyer_m = float(str(d.get("loyer_mensuel") or 0).replace(" ",""))
    surf = d.get("surface")

    if is_loc:
        # ── LOCATION : afficher fourchette loyer annuel au m² ──────────────
        _header(c,"Notre positionnement locatif"); _sec(c,"Loyer de marché",14*_mm,_H-32*_mm)
        surf_f = float(str(surf or 0).replace(" ","")) if surf else 0

        # Priorité : loyer_mensuel saisi → sinon loyer_estime_median (calculé depuis web search / référentiel)
        loyer_m_use = loyer_m if loyer_m else float(str(d.get("loyer_estime_median") or 0))
        loyer_an_actuel = loyer_m_use * 12
        loyer_m2_actuel = loyer_an_actuel / surf_f if surf_f else 0

        # Fourchette : depuis loyer_estime_min/max si disponible, sinon ±10 %
        _est_min = float(str(d.get("loyer_estime_min") or 0))
        _est_max = float(str(d.get("loyer_estime_max") or 0))
        pm = int(_est_min * 12) if _est_min else int(loyer_an_actuel * 0.90)
        pv = int(loyer_an_actuel)
        px = int(_est_max * 12) if _est_max else int(loyer_an_actuel * 1.10)

        def _pfmt_loyer(v):
            if not v: return "—"
            try: return f"{int(v):,}".replace(",", " ") + " €/an"
            except: return str(v)

        by2=_H-82*_mm; sw=(_W-28*_mm)/3
        for i,((t,p,n),col) in enumerate(zip(
            [("Loyer bas de marché",_pfmt_loyer(pm),"Conditions de marché difficiles"),
             ("Loyer retenu",_pfmt_loyer(pv),"Valeur recommandée"),
             ("Loyer haut de marché",_pfmt_loyer(px),"Marché porteur")],
            [_colors.HexColor("#7BAFC4"),_BLEU_F,_BLEU])):
            sx2=14*_mm+i*sw; sh2=34*_mm if i==1 else 27*_mm; sy2=by2-sh2+(6*_mm if i==1 else 0)
            c.setFillColor(col); c.roundRect(sx2,sy2,sw-2*_mm,sh2,2*_mm if i==1 else 1.5*_mm,fill=1,stroke=0)
            c.setFillColor(_BLANC); c.setFont("Helvetica",7); c.drawCentredString(sx2+sw/2,sy2+sh2-8*_mm,t.upper())
            c.setFont("Helvetica-Bold",12 if i==1 else 10); c.drawCentredString(sx2+sw/2,sy2+sh2-20*_mm,p)
            c.setFont("Helvetica",6.5); c.drawCentredString(sx2+sw/2,sy2+5*_mm,n)
        tri_x=14*_mm+sw+sw/2; tri_y=by2-27*_mm-4*_mm
        tp=c.beginPath(); tp.moveTo(tri_x,tri_y); tp.lineTo(tri_x-4*_mm,tri_y-5*_mm); tp.lineTo(tri_x+4*_mm,tri_y-5*_mm); tp.close()
        c.setFillColor(_ORANGE); c.drawPath(tp,fill=1,stroke=0)
        if loyer_m2_actuel and surf_f:
            _lbl_loyer = "Loyer mensuel estimé" if not loyer_m else "Loyer mensuel"
            c.setFillColor(_GTEXTE); c.setFont("Helvetica",8.5)
            c.drawCentredString(_W/2,by2-42*_mm,
                f"{_lbl_loyer} : {int(loyer_m_use):,} € HT/mois  ·  soit {int(loyer_m2_actuel):,} €/m²/an  ·  Surface : {_safe(surf)} m²".replace(","," "))
        ay=by2-54*_mm; _sec(c,"Analyse & positionnement",14*_mm,ay); cw2=(_W-28*_mm-6*_mm)/2
        _draw_atouts_cards(c, d, 14*_mm, ay-3*_mm, (_W-28*_mm))
        c.setFillColor(_colors.HexColor("#E8F0F8")); c.roundRect(14*_mm+cw2+6*_mm,ay-52*_mm,cw2,50*_mm,2*_mm,fill=1,stroke=0)
        c.setFillColor(_BLEU_F); c.setFont("Helvetica-Bold",8.5); c.drawString(18*_mm+cw2+6*_mm,ay-7*_mm,"POSITIONNEMENT LOYER")
        lm2_str = f"{int(loyer_m2_actuel)} EUR/m2/an" if loyer_m2_actuel else "en coherence avec le marche"
        loyer_txt = (
            f"Le loyer affiche est positionne a {lm2_str}, coherent avec le marche "
            "local des locaux commerciaux de ce secteur. "
            "Les DVF recensent uniquement les ventes ; notre positionnement "
            "s appuie sur les baux commerciaux en cours et la demande locative locale."
        )
        loyer_para = _Para(loyer_txt, _PS("lp", fontName="Helvetica", fontSize=8,
                           textColor=_GTEXTE, leading=12, alignment=4))
        _, lph = loyer_para.wrap(cw2 - 10*_mm, 9999)
        loyer_para.drawOn(c, 18*_mm+cw2+6*_mm, ay - 14*_mm - lph)
        _footer(c,5)
        return  # Fin branche location — ne pas exécuter la suite (vente)

    else:
        # ── VENTE : afficher fourchette valeur vénale ────────────────────────
        _header(c,"Notre estimation de valeur"); _sec(c,"Positionnement prix",14*_mm,_H-32*_mm)
        pm=d.get("prix_estime_min") or d.get("prix"); px=d.get("prix_estime_max") or d.get("prix")
        pv=d.get("prix_retenu") or d.get("prix")
        # Si valeur centrale absente mais fourchette disponible → milieu
        if not pv and pm and px:
            try: pv = (int(pm) + int(px)) // 2
            except: pass
        by2=_H-82*_mm; sw=(_W-28*_mm)/3
        for i,((t,p,n),col) in enumerate(zip(
            [("Fourchette basse",_pfmt(pm),"Conditions défavorables"),
             ("Valeur estimée",_pfmt(pv),"Recommandée"),
             ("Fourchette haute",_pfmt(px),"Marché porteur")],
            [_colors.HexColor("#7BAFC4"),_BLEU_F,_BLEU])):
            sx2=14*_mm+i*sw; sh2=34*_mm if i==1 else 27*_mm; sy2=by2-sh2+(6*_mm if i==1 else 0)
            c.setFillColor(col); c.roundRect(sx2,sy2,sw-2*_mm,sh2,2*_mm if i==1 else 1.5*_mm,fill=1,stroke=0)
            c.setFillColor(_BLANC); c.setFont("Helvetica",7); c.drawCentredString(sx2+sw/2,sy2+sh2-8*_mm,t.upper())
            c.setFont("Helvetica-Bold",14 if i==1 else 11); c.drawCentredString(sx2+sw/2,sy2+sh2-20*_mm,p)
            c.setFont("Helvetica",6.5); c.drawCentredString(sx2+sw/2,sy2+5*_mm,n)
        tri_x=14*_mm+sw+sw/2; tri_y=by2-27*_mm-4*_mm
        tp=c.beginPath(); tp.moveTo(tri_x,tri_y); tp.lineTo(tri_x-4*_mm,tri_y-5*_mm); tp.lineTo(tri_x+4*_mm,tri_y-5*_mm); tp.close()
        c.setFillColor(_ORANGE); c.drawPath(tp,fill=1,stroke=0)
        if pv and surf:
            c.setFillColor(_GTEXTE); c.setFont("Helvetica",8.5)
            c.drawCentredString(_W/2,by2-42*_mm,f"Valeur estimée au m² : {_pm2(pv,surf)}  ·  Surface : {_safe(surf)} m²")
    ay=by2-54*_mm; _sec(c,"Analyse & positionnement",14*_mm,ay); cw2=(_W-28*_mm-6*_mm)/2
    # Bloc atouts
    _draw_atouts_cards(c, d, 14*_mm, ay-3*_mm, (_W-28*_mm))
    # Bloc explication DVF vs estimation
    c.setFillColor(_colors.HexColor("#E8F0F8")); c.roundRect(14*_mm+cw2+6*_mm,ay-52*_mm,cw2,50*_mm,2*_mm,fill=1,stroke=0)
    c.setFillColor(_BLEU_F); c.setFont("Helvetica-Bold",8.5); c.drawString(18*_mm+cw2+6*_mm,ay-7*_mm,"POURQUOI CET ECART AVEC LES DVF ?")
    dvf_txt = (
        "Les DVF (donnees officielles) recensent toutes les ventes de locaux "
        "commerciaux dans la commune, quelle que soit leur localisation ou configuration. "
        "Notre estimation integre les specificites de ce bien : visibilite, etat, "
        "emplacement precis et potentiel locatif reel."
    )
    dvf_para = _Para(dvf_txt, _PS("dvf", fontName="Helvetica", fontSize=8,
                     textColor=_GTEXTE, leading=12, alignment=4))
    _, dvf_h = dvf_para.wrap(cw2 - 10*_mm, 9999)
    dvf_para.drawOn(c, 18*_mm+cw2+6*_mm, ay - 14*_mm - dvf_h)
    # Taxe foncière si disponible
    taxe = d.get("taxe_fonciere") or d.get("taxe") or 0
    if taxe:
        try:
            taxe_fmt = f"{int(float(str(taxe).replace(' ',''))) :,}".replace(","," ") + " €/an"
        except Exception:
            taxe_fmt = str(taxe)
        c.setFillColor(_GRIS); c.setStrokeColor(_colors.HexColor("#D1D8E8")); c.setLineWidth(0.5)
        tf_y = ay - 58*_mm
        c.roundRect(14*_mm, tf_y, _W-28*_mm, 12*_mm, 2*_mm, fill=1, stroke=1)
        c.setFillColor(_colors.HexColor("#777777")); c.setFont("Helvetica", 7)
        c.drawString(18*_mm, tf_y+7.5*_mm, "TAXE FONCIÈRE ANNUELLE")
        c.setFillColor(_BLEU_F); c.setFont("Helvetica-Bold", 10)
        c.drawString(18*_mm, tf_y+2.5*_mm, taxe_fmt)

    _footer(c,5)

def _page6(c):
    c.setFillColor(_BLEU); c.rect(0,_H*0.5,_W,_H*0.5,fill=1,stroke=0)
    c.setFillColor(_BLANC); c.rect(0,0,_W,_H*0.5,fill=1,stroke=0)
    _logo(c, _W-54*_mm, _H-56*_mm, w=36*_mm)
    c.setFillColor(_BLANC); c.setFont("Helvetica",11); c.drawString(14*_mm,_H-20*_mm,"VOTRE PARTENAIRE EN IMMOBILIER COMMERCIAL")
    c.setFont("Helvetica-Bold",28); c.drawString(14*_mm,_H-38*_mm,"Barbier Immobilier")
    c.setFont("Helvetica",14); c.setFillColor(_colors.HexColor("#FFFFFFCC")); c.drawString(14*_mm,_H-50*_mm,"Votre projet devient le nôtre")
    c.setFillColor(_ORANGE); c.rect(14*_mm,_H-54*_mm,50*_mm,2.5*_mm,fill=1,stroke=0)
    for i,(num,lbl) in enumerate([("33 ans","d'expertise locale"),("+5 000","clients accompagnés"),("3 métiers","vente · location · cession")]):
        sx=14*_mm+i*(_W-28*_mm)/3
        c.setFillColor(_BLANC); c.setFont("Helvetica-Bold",20); c.drawString(sx+3*_mm,_H*0.52+14*_mm,num)
        c.setFont("Helvetica",9); c.setFillColor(_colors.HexColor("#FFFFFFBB")); c.drawString(sx+3*_mm,_H*0.52+8*_mm,lbl)
    for i,(title,desc) in enumerate([
        ("Estimation & Valorisation","Analyse précise de la valeur vénale basée sur les données du marché local et notre expertise terrain."),
        ("Vente & Transaction","Diffusion multi-portails, sélection d'acquéreurs qualifiés, négociation et suivi jusqu'à la signature."),
        ("Location Commerciale","Recherche de locataires, rédaction des baux, gestion locative complète."),
        ("Cession d'Entreprise","Accompagnement expert pour la cession ou reprise de fonds de commerce.")]):
        sws=(_W-28*_mm-8*_mm)/2; shs=32*_mm; col=i%2; row2=i//2
        sx4=14*_mm+col*(sws+8*_mm); sy4=_H*0.48-4*_mm-row2*(shs+5*_mm)
        c.setFillColor(_GRIS); c.roundRect(sx4,sy4-shs,sws,shs,2*_mm,fill=1,stroke=0)
        c.setFillColor(_ORANGE); c.rect(sx4,sy4-shs,3*_mm,shs,fill=1,stroke=0)
        c.setFillColor(_BLEU_F); c.setFont("Helvetica-Bold",10); c.drawString(sx4+6*_mm,sy4-8*_mm,title)
        p=_Para(desc,_PS("ds",fontName="Helvetica",fontSize=8.5,textColor=_GTEXTE,leading=12))
        _,ph=p.wrap(sws-10*_mm,9999); p.drawOn(c,sx4+6*_mm,sy4-shs+5*_mm)
    c.setFillColor(_BLEU_F); c.roundRect(14*_mm,14*_mm,_W-28*_mm,20*_mm,2*_mm,fill=1,stroke=0)
    c.setFillColor(_BLANC); c.setFont("Helvetica-Bold",10); c.drawString(20*_mm,28*_mm,"2 place Albert Einstein, 56000 Vannes")
    c.setFont("Helvetica",9); c.drawString(20*_mm,21*_mm,"02.97.47.11.11  ·  contact@barbierimmobilier.com  ·  barbierimmobilier.com")
    _footer(c,6)


def _clean_desc(text):
    """Nettoie les variables n8n résiduelles {{ $json[...] }} d'un texte."""
    import re as _re
    if not text: return ""
    return _re.sub(r'\{\{[^}]+\}\}', '', text).strip()

def generate_dossier_pdf(d, comparables=[]):
    buf = _BytesIO()
    cv  = _canvas.Canvas(buf, pagesize=_A4)
    cv.setTitle(f"Dossier — {d.get('reference','')}")
    _page1(cv, d);              cv.showPage()
    _page2(cv, d);              cv.showPage()
    _page3(cv, d);              cv.showPage()
    _page4(cv, comparables, d); cv.showPage()
    _page5(cv, d);              cv.showPage()
    _page6(cv);                 cv.showPage()
    cv.save(); buf.seek(0)
    return buf.read()

@app.route("/dossier", methods=["POST"])
def dossier():
    """
    Génère le dossier commercial 6 pages style BAR-00322.
    Payload JSON direct depuis le cockpit (données Airtable déjà hydratées).
    Clés attendues :
      reference, type_bien, adresse, code_postal, ville, surface, surface_terrain,
      prix, prix_estime_min, prix_estime_max, prix_retenu,
      negociateur, description, annee_construct, activite, ca_ht, loyer_annuel,
      texte_quartier (optionnel — GPT génère si absent),
      photos (liste d'URLs ou data URLs base64),
      comparables (liste de dicts {Adresse, Ville, Prix, Surface, Statut, Source, Date})
    """
    try:
        data = request.get_json(silent=True) or {}
        if not data:
            return jsonify({"error": "Payload JSON requis"}), 400

        # Texte quartier — utiliser GPT si absent
        texte_q = data.get("texte_quartier") or ""
        if not texte_q:
            try:
                texte_q = _gpt_quartier(
                    data.get("adresse", ""),
                    data.get("ville", "Vannes"),
                    data.get("type_bien", ""),
                    data.get("surface", "")
                )
            except Exception:
                texte_q = (
                    f"Situé à {data.get('ville','Vannes')}, ce bien bénéficie d'une localisation "
                    "stratégique dans un secteur économiquement actif du Morbihan. "
                    "L'accessibilité est optimale grâce à la proximité de la rocade et des axes principaux. "
                    "Le secteur compte de nombreux commerces, services et équipements à proximité immédiate, "
                    "offrant un environnement favorable à l'exploitation d'une activité commerciale ou professionnelle."
                )

        d = {
            "reference":       data.get("reference", ""),
            "type_bien":       data.get("type_bien", ""),
            "adresse":         data.get("adresse", ""),
            "code_postal":     data.get("code_postal", "56000"),
            "ville":           data.get("ville", "Vannes"),
            "surface":         data.get("surface"),
            "surface_terrain": data.get("surface_terrain"),
            "prix":            data.get("prix"),
            "prix_estime_min": data.get("prix_estime_min"),
            "prix_estime_max": data.get("prix_estime_max"),
            "prix_retenu":     data.get("prix_retenu"),
            "negociateur":     data.get("negociateur", "Barbier Immobilier"),
            "description":     _clean_desc(data.get("description", "")),
            "annee_construct": data.get("annee_construct"),
            "activite":        data.get("activite"),
            "ca_ht":           data.get("ca_ht"),
            "loyer_annuel":    data.get("loyer_annuel"),
            "loyer_mensuel":   data.get("loyer_mensuel"),
            "taxe_fonciere":  data.get("taxe_fonciere") or data.get("taxe") or 0,
            "ref_cadastrale": data.get("ref_cadastrale", ""),
            "texte_quartier":  texte_q,
            "photos":          data.get("photos", []),
            "statut_mandat":   data.get("statut_mandat", ""),
            "dvf_source":      data.get("dvf_source", ""),
            "loyer_pm2_min":   data.get("loyer_pm2_min", 0),
            "loyer_pm2_max":   data.get("loyer_pm2_max", 0),
            "loyer_pm2_median":data.get("loyer_pm2_median", 0),
            "loyer_ville_match":data.get("loyer_ville_match", ""),
            "loyer_marche_pm2_an": data.get("loyer_marche_pm2_an", 0),
        }

        comparables = data.get("comparables", [])

        # Détecter location vs vente dès le départ
        _is_location_gen = bool(data.get("loyer_mensuel")) or "location" in str(data.get("statut_mandat","")).lower()

        # ── VENTE : récupérer comparables DVF ─────────────────────────────────
        # DVF = mutations foncières = VENTES uniquement. Ne pas appeler pour location.
        dvf_pm2 = 0
        if not comparables and not _is_location_gen:
            try:
                dvf_comps, dvf_pm2, dvf_stats = _run_dvf(
                    ville       = data.get("ville", "Vannes"),
                    code_postal = data.get("code_postal", "56000"),
                    surface     = float(data.get("surface") or 0),
                    type_bien   = data.get("type_bien", "Local commercial"),
                    limit       = 4
                )
                comparables = dvf_comps
            except Exception:
                pass

        # ── WEB SEARCH : toujours pour location / fallback vente si DVF < 3 ──
        # ── LOCATION : web search JSON strict + fallback 02_Loyers_Marche ──
        if _is_location_gen:
            import os as _os_ws2, urllib.request as _ur_ws2, json as _js_ws2, re as _re_ws2
            _api2   = _os_ws2.environ.get("OPENAI_API_KEY", "")
            _at_pat = _os_ws2.environ.get("AIRTABLE_PAT", "")
            _surf2  = float(str(data.get("surface") or 0))
            _type2  = str(data.get("type_bien") or "Bureau")
            _ville2 = str(data.get("ville") or "Vannes")
            _smin2  = int(_surf2 * 0.75)
            _smax2  = int(_surf2 * 1.25)
            _pm2_min2 = _pm2_max2 = _pm2_ret2 = _nb2 = 0
            _ws_source = ""

            # ── Étape 1 : Web search avec prompt JSON strict ──────────────────
            if _api2 and _surf2 > 0:
                try:
                    _prompt2 = (
                        f"Tu es expert en immobilier commercial. Recherche sur SeLoger, BienIci et Logic-immo "
                        f"les annonces actuelles de {_type2} en location à {_ville2} (Morbihan, 56), "
                        f"surface entre {_smin2} et {_smax2} m². "
                        f"Si pas de résultats pour {_ville2}, utilise les données de Vannes ou du secteur proche. "
                        f"Retourne UNIQUEMENT ce JSON, sans texte avant ni après, sans backticks : "
                        f'{{\"pm2_min\": X, \"pm2_max\": X, \"pm2_retenu\": X, \"nb_annonces\": X}} '
                        f"où X est le loyer annuel HT en euros/m²."
                    )
                    _pl2 = _js_ws2.dumps({
                        "model": "gpt-4o-search-preview",
                        "messages": [{"role": "user", "content": _prompt2}],
                        "max_tokens": 100
                    }).encode()
                    _req2 = _ur_ws2.Request(
                        "https://api.openai.com/v1/chat/completions",
                        data=_pl2, method="POST",
                        headers={"Authorization": f"Bearer {_api2}", "Content-Type": "application/json"}
                    )
                    with _ur_ws2.urlopen(_req2, timeout=35) as _res2:
                        _resp2 = _js_ws2.load(_res2)
                    _txt2 = _resp2["choices"][0]["message"]["content"].strip()
                    # Extraire le JSON même si GPT ajoute du texte malgré l'instruction
                    _m2 = _re_ws2.search(r"\{[^{}]+\}", _txt2)
                    if _m2:
                        _d2 = _js_ws2.loads(_m2.group())
                        _pm2_min2 = int(float(_d2.get("pm2_min") or 0))
                        _pm2_max2 = int(float(_d2.get("pm2_max") or 0))
                        _pm2_ret2 = int(float(_d2.get("pm2_retenu") or 0))
                        _nb2      = int(_d2.get("nb_annonces") or 0)
                        if _pm2_ret2 > 0:
                            _ws_source = f"Sources web — {_nb2} annonces ({_ville2})" if _nb2 else f"Estimation marché {_ville2}"
                except Exception:
                    pass  # Web search indisponible — on passe au fallback

            # ── Étape 2 : Fallback 02_Loyers_Marche si web search vide ────────
            if _pm2_ret2 == 0 and _at_pat:
                try:
                    _at_base = "appscgBdxTzSPtOaZ"
                    _at_tbl  = "tblYEfE6WhP6mnlAf"
                    # Clé exacte : "Bureau|Saint-Avé" — essai ville exacte puis Vannes
                    for _v_try in [_ville2, "Vannes"]:
                        _cle = f"{_type2}|{_v_try}"
                        _filter = _ur_ws2.quote(f"{{Clé}} = \"{_cle}\"")
                        _at_url = f"https://api.airtable.com/v0/{_at_base}/{_at_tbl}?filterByFormula={_filter}&maxRecords=1"
                        _at_req = _ur_ws2.Request(_at_url, headers={"Authorization": f"Bearer {_at_pat}"})
                        with _ur_ws2.urlopen(_at_req, timeout=10) as _at_res:
                            _at_data = _js_ws2.load(_at_res)
                        _at_recs = _at_data.get("records", [])
                        if _at_recs:
                            _af = _at_recs[0].get("fields", {})
                            _pm2_min2 = int(float(_af.get("Loyer min HT m2 an") or 0))
                            _pm2_max2 = int(float(_af.get("Loyer max HT m2 an") or 0))
                            _pm2_ret2 = int(float(_af.get("Loyer median HT m2 an") or 0))
                            if _pm2_ret2 > 0:
                                _ws_source = f"Référentiel Barbier Immobilier ({_v_try}, 2025-Q4)"
                                break
                except Exception:
                    pass  # Fallback Airtable indisponible

            # ── Injection dans d si on a un résultat ─────────────────────────
            if _pm2_ret2 > 0 and _surf2 > 0:
                _loc_min = _pm2_min2 if _pm2_min2 else int(_pm2_ret2 * 0.85)
                _loc_max = _pm2_max2 if _pm2_max2 else int(_pm2_ret2 * 1.15)
                d["loyer_pm2_min"]        = _loc_min
                d["loyer_pm2_max"]        = _loc_max
                d["loyer_pm2_median"]     = _pm2_ret2
                d["loyer_marche_pm2_an"]  = _pm2_ret2
                d["loyer_ville_match"]    = _ville2
                d["dvf_source"]           = _ws_source
                # Loyer mensuel estimé (pm2 annuel → mensuel × surface)
                d["loyer_estime_min"]     = int(_loc_min * _surf2 / 12)
                d["loyer_estime_max"]     = int(_loc_max * _surf2 / 12)
                d["loyer_estime_median"]  = int(_pm2_ret2 * _surf2 / 12)
                # Pour page 5 location : utiliser clés dédiées, ne pas écraser prix_estime vente
                d["prix_retenu"]          = d["loyer_estime_median"]
                if not d.get("prix"):
                    d["prix"] = d["loyer_estime_median"]
                dvf_pm2 = _pm2_ret2

        # ── VENTE : fourchette depuis DVF si suffisant ─────────────────────────
        if dvf_pm2 > 0 and not _is_location_gen and len(comparables) >= 3:
            try:
                surface_val = float(data.get("surface") or 0)
                prix_v = d.get("prix") or 0
                if surface_val > 0 and prix_v:
                    pm2_vente = prix_v / surface_val
                    pm2_ref = (pm2_vente + dvf_pm2) / 2
                    d["prix_estime_min"] = int(pm2_ref * 0.90 * surface_val)
                    d["prix_estime_max"] = int(pm2_ref * 1.10 * surface_val)
                    d["prix_retenu"]     = int(pm2_ref * surface_val)
                    d["prix"] = prix_v
            except Exception:
                pass

        # Enrichir description si absente ou trop courte (< 80 chars)
        if not d.get("description") or len(str(d.get("description",""))) < 80:
            try:
                import os as _os2
                api_key = _os2.environ.get("OPENAI_API_KEY","")
                if api_key:
                    notes_src = " ".join(filter(None, [
                        data.get("notes",""), data.get("description",""),
                        data.get("type_bien",""), str(data.get("surface","")),
                        data.get("activite",""), data.get("adresse",""), data.get("ville","")
                    ]))
                    is_loc = bool(data.get("loyer_mensuel"))
                    op = "à louer" if is_loc else "à vendre"
                    val_info = ""
                    if is_loc and data.get("loyer_mensuel"):
                        try: val_info = f"Loyer : {int(float(str(data['loyer_mensuel'])))} € HT/mois"
                        except: pass
                    elif data.get("prix"):
                        try: val_info = f"Prix : {int(float(str(data['prix'])))} €"
                        except: pass
                    prompt_desc = (
                        f"Tu es négociateur senior chez Barbier Immobilier (Vannes, Morbihan).\n"
                        f"Rédige une présentation commerciale pour ce bien {op}.\n\n"
                        f"TYPE : {data.get('type_bien','')} — {data.get('surface','')} m²\n"
                        f"ADRESSE : {data.get('adresse','')}, {data.get('ville','')}\n"
                        f"{val_info}\n"
                        f"INFORMATIONS DISPONIBLES : {notes_src[:800]}\n\n"
                        f"EXIGENCES :\n"
                        f"- 130-180 mots en texte continu\n"
                        f"- Accroche commerciale forte (1 phrase)\n"
                        f"- Description fonctionnelle : agencement, état, équipements (2-3 phrases)\n"
                        f"- Atouts stratégiques : emplacement, accessibilité, potentiel (2 phrases)\n"
                        f"- Chiffres précis (surface, prix/loyer au m²)\n"
                        f"- Ton professionnel, vendeur, sans formule vague\n"
                        f"- Pas de hashtags ni d'emojis"
                    )
                    gpt_payload = _json.dumps({
                        "model": "gpt-4o",
                        "messages": [{"role": "user", "content": prompt_desc}],
                        "max_tokens": 400, "temperature": 0.65
                    }).encode()
                    gpt_req = _ur.Request("https://api.openai.com/v1/chat/completions",
                        data=gpt_payload, method="POST",
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
                    with _ur.urlopen(gpt_req, timeout=30) as gpt_res:
                        desc_enrichie = _json.load(gpt_res)["choices"][0]["message"]["content"].strip()
                    d["description"] = desc_enrichie
            except Exception:
                pass

        pdf_bytes = generate_dossier_pdf(d, comparables)
        ref = d.get("reference", "bien")
        import urllib.parse as _up
        extra_headers = {
            "Content-Disposition": f'attachment; filename="Dossier_{ref}.pdf"',
        }
        if d.get("texte_quartier"):
            extra_headers["X-Texte-Quartier"] = _up.quote(d["texte_quartier"][:1000], safe="")
        if d.get("description"):
            extra_headers["X-Description-Commerciale"] = _up.quote(d["description"][:1000], safe="")
        return Response(pdf_bytes, mimetype="application/pdf", headers=extra_headers)

    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/urbanisme", methods=["POST"])
def urbanisme():
    """
    Enrichit les données urbanisme pour un bien :
    - Coordonnées GPS de la parcelle (cadastre.data.gouv.fr)
    - Image cadastrale WMS IGN (data.geopf.fr)
    - Zone PLU + libellé + lien règlement (apicarto.ign.fr/gpu)
    - Résumé PLU lisible (GPT-4o)
    - Servitudes et risques si disponibles

    Payload : ref_cadastrale, adresse, ville, code_postal, type_bien
    Retourne : {
        "ok": True,
        "lon": ..., "lat": ...,
        "zone_plu": "Ubd3p", "type_zone": "U",
        "libelle_plu": "Zone urbaine...",
        "url_reglement": "https://...",
        "resume_plu": "Texte GPT 2-3 phrases",
        "servitudes": [],
        "cadastre_image_b64": "data:image/png;base64,..."
    }
    """
    import re as _re_urb, gzip as _gz_urb, os as _os_urb, json as _j_urb
    import urllib.request as _ur_urb, urllib.parse as _up_urb
    import math as _m_urb

    try:
        data = request.get_json(silent=True) or {}
        ref_cad    = (data.get("ref_cadastrale") or "").replace(" ", "").upper()
        adresse    = data.get("adresse", "")
        ville      = data.get("ville", "Vannes")
        type_bien  = data.get("type_bien", "Local commercial")

        # ── 1. Parser la référence cadastrale (optionnelle) ────────────────
        m_ref = _re_urb.match(r"(\d{5})([A-Z]{2})(\d{3,4})", ref_cad)
        if m_ref:
            code_insee = m_ref.group(1)
            section    = m_ref.group(2)
            numero     = m_ref.group(3).zfill(4)
        else:
            # Pas de référence valide — on utilisera uniquement l'adresse
            code_insee = section = numero = ""

        # ── 2. Coordonnées GPS : parcelle si ref valide, sinon adresse ───────
        lon, lat = None, None
        if code_insee and section and numero:
            coords_result = _get_parcelle_coords(code_insee, section, numero)
            if coords_result:
                lon, lat = coords_result

        if lon is None:
            # Fallback : géocodage par adresse
            if not adresse:
                return jsonify({"ok": False, "error": "Adresse manquante — impossible de géolocaliser"}), 400
            try:
                q = _up_urb.quote_plus(f"{adresse}, {ville}, France")
                geo_url = f"https://data.geopf.fr/geocodage/search?q={q}&limit=1"
                req_g = _ur_urb.Request(geo_url, headers={"User-Agent": "BarbierImmo/1.0"})
                with _ur_urb.urlopen(req_g, timeout=8) as rg:
                    gdata = _j_urb.load(rg)
                fc = gdata.get("features", [])
                if fc:
                    c = fc[0]["geometry"]["coordinates"]
                    lon, lat = c[0], c[1]
            except Exception:
                pass

        if lon is None:
            return jsonify({"ok": False, "error": f"Impossible de géolocaliser : {adresse}, {ville}"}), 400

        # ── 3. Image cadastrale WMS ──────────────────────────────────────────
        cadastre_b64 = ""
        try:
            delta = 0.0006
            bbox = f"{lon-delta},{lat-delta},{lon+delta},{lat+delta}"
            wms_url = (
                "https://data.geopf.fr/wms-r/wms?"
                "SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap"
                "&LAYERS=CADASTRALPARCELS.PARCELLAIRE_EXPRESS"
                "&FORMAT=image/png&TRANSPARENT=true"
                "&CRS=CRS:84&STYLES="
                f"&WIDTH=500&HEIGHT=400&BBOX={bbox}"
            )
            req_wms = _ur_urb.Request(wms_url, headers={"User-Agent": "BarbierImmo/1.0"})
            with _ur_urb.urlopen(req_wms, timeout=15) as rwms:
                img_bytes = rwms.read()

            # Ajouter marqueur orange sur la parcelle
            from PIL import Image as _PILUrb, ImageDraw as _IDUrb
            import io as _io_urb
            img = _PILUrb.open(_io_urb.BytesIO(img_bytes)).convert("RGB")
            draw = _IDUrb.Draw(img)
            cx_img, cy_img = img.width // 2, img.height // 2
            r_m = 10
            draw.ellipse([cx_img-r_m, cy_img-r_m, cx_img+r_m, cy_img+r_m],
                         fill=(232, 71, 42), outline=(255,255,255), width=3)
            # Encodage base64
            buf = _io_urb.BytesIO()
            img.save(buf, format="PNG")
            import base64 as _b64u
            cadastre_b64 = "data:image/png;base64," + _b64u.b64encode(buf.getvalue()).decode()
        except Exception as e_wms:
            pass  # Image non bloquante

        # ── 4. Zone PLU via apicarto.ign.fr ─────────────────────────────────
        zone_plu = ""; type_zone = ""; libelle_plu = ""; url_reglement = ""
        try:
            geom_encoded = _up_urb.quote(
                _j_urb.dumps({"type": "Point", "coordinates": [lon, lat]})
            )
            gpu_url = f"https://apicarto.ign.fr/api/gpu/zone-urba?geom={geom_encoded}"
            req_gpu = _ur_urb.Request(gpu_url, headers={"User-Agent": "BarbierImmo/1.0"})
            with _ur_urb.urlopen(req_gpu, timeout=10) as rgpu:
                gpu_data = _j_urb.load(rgpu)
            features_plu = gpu_data.get("features", [])
            if features_plu:
                p = features_plu[0]["properties"]
                zone_plu    = p.get("libelle", "")
                type_zone   = p.get("typezone", "")
                libelle_plu = p.get("libelong", "")
                url_reglement = p.get("urlfic", "")
        except Exception:
            pass

        # ── 5. Résumé PLU par GPT-4o ─────────────────────────────────────────
        resume_plu = ""
        if libelle_plu or zone_plu:
            try:
                api_key = _os_urb.environ.get("OPENAI_API_KEY", "")
                if api_key:
                    prompt_plu = (
                        "Tu es expert en droit de l'urbanisme et en immobilier commercial.\n"
                        "Resume en 2-3 phrases claires et professionnelles la zone PLU suivante "
                        "pour un dossier destine a un investisseur ou locataire professionnel.\n\n"
                        f"Bien : {type_bien} - {adresse}, {ville}\n"
                        f"Zone PLU : {zone_plu} (type {type_zone})\n"
                        f"Libelle officiel : {libelle_plu}\n\n"
                        "Indique ce que la zone autorise, ce qu elle interdit ou limite, "
                        "et pourquoi c est favorable ou non pour ce type de bien.\n"
                        "Ton : factuel, professionnel, accessible a un non-juriste. 2-3 phrases maximum."
                    )
                    gpt_payload = _j_urb.dumps({
                        "model": "gpt-4o",
                        "messages": [{"role": "user", "content": prompt_plu}],
                        "max_tokens": 200, "temperature": 0.3
                    }).encode()
                    req_gpt = _ur_urb.Request(
                        "https://api.openai.com/v1/chat/completions",
                        data=gpt_payload, method="POST",
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                    )
                    with _ur_urb.urlopen(req_gpt, timeout=30) as rgpt:
                        resume_plu = _j_urb.load(rgpt)["choices"][0]["message"]["content"].strip()
            except Exception:
                resume_plu = f"Zone {zone_plu} ({type_zone}) — {libelle_plu[:150]}" if libelle_plu else ""

        # ── 6. Servitudes (best-effort) ──────────────────────────────────────
        servitudes = []
        try:
            serv_geom = _up_urb.quote(_j_urb.dumps({"type": "Point", "coordinates": [lon, lat]}))
            serv_url = f"https://apicarto.ign.fr/api/gpu/servitude?geom={serv_geom}"
            req_serv = _ur_urb.Request(serv_url, headers={"User-Agent": "BarbierImmo/1.0"})
            with _ur_urb.urlopen(req_serv, timeout=8) as rs:
                serv_data = _j_urb.load(rs)
            for sf in serv_data.get("features", [])[:5]:
                sp = sf.get("properties", {})
                libserv = sp.get("libelle") or sp.get("typessup") or ""
                if libserv:
                    servitudes.append(libserv[:80])
        except Exception:
            pass

        return jsonify({
            "ok":           True,
            "lon":          lon,
            "lat":          lat,
            "zone_plu":     zone_plu,
            "type_zone":    type_zone,
            "libelle_plu":  libelle_plu,
            "url_reglement": url_reglement,
            "resume_plu":   resume_plu,
            "servitudes":   servitudes,
            "cadastre_image_b64": cadastre_b64,
            "code_insee":   code_insee,
            "section":      section,
            "numero":       numero,
        })

    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()[:500]}), 500


@app.route("/estimer", methods=["POST"])
def estimer():
    """
    Calcule les fourchettes de prix via DVF et retourne un JSON avec les estimations.
    Payload : type_bien, adresse, ville, code_postal, surface, loyer_mensuel, prix
    Retourne : {"ok": True, "prix_estime_min": X, "prix_retenu": Y, "prix_estime_max": Z, "dvf_pm2": W, "nb_comparables": N}
    """
    try:
        data = request.get_json(silent=True) or {}
        ville      = data.get("ville", "Vannes")
        cp         = str(data.get("code_postal", "56000"))
        surface    = float(data.get("surface") or 0)
        type_bien  = data.get("type_bien", "Local commercial")
        prix_v     = float(data.get("prix") or data.get("prix_de_vente") or 0)
        loyer_m    = float(data.get("loyer_mensuel") or 0)

        if surface <= 0:
            return jsonify({"ok": False, "error": "Surface manquante ou nulle"}), 400

        comps, dvf_pm2, dvf_stats = _run_dvf(ville, cp, surface, type_bien, limit=6)

        if dvf_pm2 <= 0:
            return jsonify({"ok": False, "error": "Pas de données DVF disponibles pour ce secteur", "nb_comparables": len(comps)}), 200

        if loyer_m:
            loyer_m2 = (loyer_m * 12) / surface
            pm2_ref  = (loyer_m2 + dvf_pm2) / 2
            pm_min   = int(pm2_ref * 0.88 * surface)
            pm_ret   = int(pm2_ref * surface)
            pm_max   = int(pm2_ref * 1.12 * surface)
        elif prix_v:
            pm2_vente = prix_v / surface
            pm2_ref   = (pm2_vente + dvf_pm2) / 2
            pm_min    = int(pm2_ref * 0.90 * surface)
            pm_ret    = int(pm2_ref * surface)
            pm_max    = int(pm2_ref * 1.10 * surface)
        else:
            pm_min = int(dvf_pm2 * 0.90 * surface)
            pm_ret = int(dvf_pm2 * surface)
            pm_max = int(dvf_pm2 * 1.10 * surface)

        return jsonify({
            "ok":              True,
            "prix_estime_min": pm_min,
            "prix_retenu":     pm_ret,
            "prix_estime_max": pm_max,
            "dvf_pm2":         round(dvf_pm2, 0),
            "nb_comparables":  len(comps),
            "methode":         "locatif DVF" if loyer_m else "vente DVF",
        })

    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/generer-avis", methods=["POST"])
def generer_avis():
    """
    Génère un texte d'avis de valeur riche et structuré via GPT-4o.
    Payload : type_bien, adresse, ville, surface, loyer_mensuel, prix, activite,
              prix_estime_min, prix_estime_max, prix_retenu, dvf_resume, notes
    Retourne : {"avis": "texte structuré..."}
    """
    import os as _os_av, json as _json_av, urllib.request as _ur_av
    try:
        data = request.get_json(silent=True) or {}
        api_key = _os_av.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            return jsonify({"error": "OPENAI_API_KEY manquant"}), 500

        type_b   = data.get("type_bien", "Local commercial")
        adresse  = data.get("adresse", "")
        ville    = data.get("ville", "Vannes")
        surface  = data.get("surface", "")
        loyer    = data.get("loyer_mensuel") or data.get("loyer", 0)
        prix     = data.get("prix") or data.get("prix_de_vente", 0)
        activite = data.get("activite", "")
        pm_min   = data.get("prix_estime_min") or 0
        pm_max   = data.get("prix_estime_max") or 0
        pm_ret   = data.get("prix_retenu") or 0
        dvf      = data.get("dvf_resume", "")
        notes    = data.get("notes", "")

        is_loc = bool(loyer)
        def fmt(v):
            try: return f"{int(float(str(v).replace(' ',''))) :,}".replace(",", " ") + " €"
            except: return str(v)

        valeur_bien = f"Loyer : {fmt(loyer)}/mois" if is_loc else f"Prix de vente : {fmt(prix)}"
        loyer_m2 = ""
        if is_loc and loyer and surface:
            try:
                lm2 = (float(str(loyer).replace(' ','')) * 12) / float(str(surface).replace(' ',''))
                loyer_m2 = f"Loyer annuel au m² : {lm2:.0f} €/m²/an"
            except: pass

        estim_bloc = ""
        if pm_ret:
            estim_bloc = (
                f"ESTIMATION DVF :\n"
                f"  Fourchette basse : {fmt(pm_min)}\n"
                f"  Valeur retenue   : {fmt(pm_ret)}\n"
                f"  Fourchette haute : {fmt(pm_max)}\n"
            )

        prompt = f"""Tu es expert en évaluation immobilière commerciale chez Barbier Immobilier (Vannes, Morbihan).
Rédige un avis de valeur professionnel et structuré pour ce bien.

DONNÉES DU BIEN :
Type : {type_b}
Adresse : {adresse}, {ville} (Morbihan, 56)
Surface : {surface} m²
{f"Activité : {activite}" if activite else ""}
{valeur_bien}
{loyer_m2}
{estim_bloc}
{f"Données de marché DVF : {dvf[:600]}" if dvf else ""}
{f"Notes : {notes[:400]}" if notes else ""}

STRUCTURE ATTENDUE (utilise exactement ces marqueurs) :

---SYNTHÈSE---
En 4-5 phrases : présentation du bien, contexte du marché local Morbihan, adéquation offre/demande.
Mentionner obligatoirement le loyer ou prix au m² et le comparer aux moyennes du secteur.

---MÉTHODOLOGIE---
En 3-4 phrases : expliquer la méthode d'évaluation utilisée (comparables DVF, méthode par capitalisation si locatif, méthode par comparaison si vente).
Citer les sources de données utilisées (DVF data.gouv.fr, base transactions Barbier, connaissance terrain).

---ÉVALUATION DÉTAILLÉE---
En 5-6 phrases : analyse détaillée de la valeur.
Facteurs positifs (emplacement, visibilité, état, surface, accessibilité).
Facteurs de vigilance éventuels (concurrence, travaux, marché sectoriel).
Comparaison avec les transactions DVF récentes si disponibles.
Conclusion sur le positionnement prix recommandé.

---RECOMMANDATIONS---
En 3-4 phrases : conseils opérationnels pour la mise en marché.
Stratégie de prix recommandée, délai de commercialisation estimé, axes de valorisation possibles.

RÈGLES :
- Ton professionnel et expert, pas commercial
- Chiffres précis obligatoires (€/m², rentabilité brute si locatif, ratio prix/marché)
- Pas de formules vagues
- Langue française impeccable
- Longueur totale : 300-400 mots"""

        payload_gpt = _json_av.dumps({
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 800,
            "temperature": 0.5
        }).encode()
        req = _ur_av.Request("https://api.openai.com/v1/chat/completions",
            data=payload_gpt, method="POST",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
        with _ur_av.urlopen(req, timeout=60) as res:
            avis_txt = _json_av.load(res)["choices"][0]["message"]["content"].strip()

        return jsonify({"avis": avis_txt})

    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/annonce", methods=["POST"])
def annonce():
    """
    Génère un texte d'annonce portail optimisé pour un bien commercial.
    Payload JSON : type_bien, adresse, ville, surface, prix, loyer_mensuel,
                   description_brute, activite, type_bail, statut_mandat, notes
    Retourne : {"annonce": "texte..."}
    """
    try:
        import os as _os
        data = request.get_json(silent=True) or {}
        api_key = _os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            return jsonify({"error": "OPENAI_API_KEY manquant"}), 500

        # Construire le contexte depuis le payload
        type_b  = data.get("type_bien", "")
        adresse = data.get("adresse", "")
        ville   = data.get("ville", "Vannes")
        surface = data.get("surface", "")
        prix    = data.get("prix", "") or data.get("prix_retenu", "")
        loyer   = data.get("loyer_mensuel", "")
        desc    = data.get("description_brute", "") or data.get("notes", "")
        activite= data.get("activite", "")
        bail    = data.get("type_bail", "")
        mandat  = data.get("statut_mandat", "")
        nego    = data.get("negociateur", "Barbier Immobilier")

        prix_str = f"{int(float(str(prix).replace(' ',''))):,} €".replace(","," ") if prix else ""
        loyer_str = f"{int(float(str(loyer).replace(' ',''))):,} € HT/mois".replace(","," ") if loyer else ""
        val_str = prix_str or loyer_str

        is_location = bool(loyer)
        operation = "À LOUER" if is_location else "À VENDRE"
        val_affichee = loyer_str if is_location else prix_str

        prompt = (
            f"Tu es négociateur expert chez Barbier Immobilier, spécialiste de l'immobilier commercial "
            f"dans le Golfe du Morbihan (Vannes, Bretagne Sud). "
            f"Rédige une annonce portail professionnelle, percutante et précise.\n\n"
            f"OPÉRATION : {operation}\n"
            f"TYPE : {type_b}\n"
            f"SURFACE : {surface} m²\n"
            f"LOCALISATION : {adresse}, {ville} (Morbihan, 56)\n"
            f"VALEUR : {val_affichee or 'Prix sur demande'}\n"
        )
        if activite: prompt += f"ACTIVITÉ ACTUELLE / DESTINATION : {activite}\n"
        if bail:     prompt += f"TYPE DE BAIL : {bail}\n"
        if mandat:   prompt += f"TYPE DE MANDAT : {mandat}\n"
        if desc:     prompt += f"\nINFORMATIONS DISPONIBLES SUR LE BIEN :\n{desc[:1500]}\n"
        prompt += (
            "\nRÈGLES DE RÉDACTION :\n"
            "1. Accroche forte en 1 phrase (type de bien + localisation + argument clé)\n"
            "2. Description précise du bien : surface, agencement, état, équipements notables (2-3 phrases)\n"
            "3. Atouts emplacement : visibilité, flux, accessibilité, environnement commercial (1-2 phrases)\n"
            "4. Éléments financiers clés si pertinent : loyer/m²/an, rentabilité, charges\n"
            "5. Call-to-action direct avec contact Barbier Immobilier\n\n"
            "CONTRAINTES :\n"
            "- 150-200 mots, ton professionnel et vendeur\n"
            "- Aucune formule vague (éviter : 'idéalement situé', 'bel emplacement')\n"
            "- Chiffres précis obligatoires (surface m², loyer €/m², etc.)\n"
            "- Pas de hashtags, pas de emojis\n"
            "- Langue française impeccable\n"
            "- Mettre en valeur le rapport qualité/prix et l'opportunité commerciale"
        )

        import json as _json2, urllib.request as _ur2
        payload = _json2.dumps({
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 400,
            "temperature": 0.7
        }).encode()
        req = _ur2.Request("https://api.openai.com/v1/chat/completions",
            data=payload, method="POST",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
        with _ur2.urlopen(req, timeout=30) as res:
            annonce_txt = _json2.load(res)["choices"][0]["message"]["content"].strip()

        return jsonify({"annonce": annonce_txt, "negociateur": nego})

    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500




def _run_dvf(ville, code_postal, surface, type_bien="Local commercial", limit=6):
    """
    Fetch DVF data.gouv.fr pour une commune donnée.
    Retourne (comparables_list, prix_median_m2, stats_dict).
    Callable directement depuis /dossier sans HTTP.
    """
    import csv as _csv2, io as _io2, time as _time2

    cp = str(code_postal or "56000")

    # 1. Code commune INSEE via geo.api.gouv.fr
    code_commune = None
    try:
        geo_url = f"https://geo.api.gouv.fr/communes?nom={_up.quote(ville)}&fields=code,nom,codesPostaux&boost=population&limit=5"
        with _ur.urlopen(_ur.Request(geo_url, headers={"User-Agent": "Barbier-Immobilier/1.0"}), timeout=8) as r:
            geo_data = _json.load(r)
        if geo_data:
            match = next((c for c in geo_data if cp in c.get("codesPostaux", [])), None) or                     next((c for c in geo_data if c["nom"].lower() == ville.lower()), None) or                     geo_data[0]
            code_commune = match["code"]
    except Exception:
        pass
    if not code_commune:
        code_commune = cp
    dept = code_commune[:2]

    # 2. DVF CSV années 2024 → 2022
    # Mapper le type de bien vers les types DVF correspondants
    # Note DVF : les bureaux sont classés "Local industriel. commercial ou assimilé"
    # Il n'existe pas de type "Bureau" dans la nomenclature DVF officielle
    type_bien_lower = (type_bien or "").lower()
    dvf_types_ok = ["local industriel. commercial ou assimilé"]
    if "terrain" in type_bien_lower:
        dvf_types_ok = []  # pas de bati

    results = []
    for annee in ["2024", "2023", "2022"]:
        if len(results) >= limit * 2:
            break
        try:
            csv_url = f"https://files.data.gouv.fr/geo-dvf/latest/csv/{annee}/communes/{dept}/{code_commune}.csv"
            with _ur.urlopen(_ur.Request(csv_url, headers={"User-Agent": "Barbier-Immobilier/1.0"}), timeout=15) as r:
                raw = r.read().decode("utf-8", errors="ignore")
            reader = _csv2.DictReader(_io2.StringIO(raw))
            for row in reader:
                nature = (row.get("nature_mutation") or "").lower()
                type_l = (row.get("type_local") or "").lower()
                surf_str = row.get("surface_reelle_bati") or ""
                prix_str = row.get("valeur_fonciere") or ""
                if not surf_str or not prix_str:
                    continue
                try:
                    s = float(surf_str.replace(",", "."))
                    p = float(prix_str.replace(",", ".").replace(" ", ""))
                except Exception:
                    continue
                if s <= 0 or p < 5000:
                    continue
                if "vente" not in nature:
                    continue
                # Filtrage strict par type de bien DVF
                if dvf_types_ok and not any(kw in type_l for kw in dvf_types_ok):
                    continue
                # Surface similaire : ±60% (plus strict qu'avant)
                if surface and surface > 0:
                    if abs(s - surface) / max(surface, 1) > 0.60:
                        continue
                # Filtre anti-aberration : prix/m² entre 200 et 30000 €/m²
                pm2 = p / s if s > 0 else 0
                if pm2 < 200 or pm2 > 30000:
                    continue
                adresse_row = " ".join(filter(None, [
                    row.get("numero_voie",""), row.get("type_voie",""), row.get("nom_voie","")
                ])).strip().upper()
                results.append({
                    "Adresse": adresse_row or "—",
                    "Ville":   ville,
                    "Prix":    int(p),
                    "Surface": int(s),
                    "Statut":  "Vendu",
                    "Source":  f"DVF {annee}",
                    "Date":    row.get("date_mutation","")[:7] or annee,
                })
                if len(results) >= limit * 2:
                    break
        except Exception:
            continue

    # 3. Si aucun résultat → relancer sans filtre surface, puis commune voisine
    if not results:
        # 3a. Relancer sur même commune sans filtre surface
        pass  # déjà fait dans la boucle ci-dessous

    if not results:
        # 3b. Chercher dans la commune voisine (Vannes = 56260)
        try:
            geo_url2 = f"https://geo.api.gouv.fr/communes?nom=Vannes&fields=code,nom,codesPostaux&boost=population&limit=1"
            with _ur.urlopen(_ur.Request(geo_url2, headers={"User-Agent": "Barbier-Immobilier/1.0"}), timeout=8) as r_v:
                geo_v = _json.load(r_v)
            if geo_v:
                code_vannes = geo_v[0]["code"]
                dept_v = code_vannes[:2]
                for annee in ["2024", "2023", "2022"]:
                    if len(results) >= limit: break
                    try:
                        csv_v = f"https://files.data.gouv.fr/geo-dvf/latest/csv/{annee}/communes/{dept_v}/{code_vannes}.csv"
                        with _ur.urlopen(_ur.Request(csv_v, headers={"User-Agent": "Barbier-Immobilier/1.0"}), timeout=15) as rv:
                            raw_v = rv.read().decode("utf-8", errors="ignore")
                        reader_v = _csv2.DictReader(_io2.StringIO(raw_v))
                        for row_v in reader_v:
                            nature_v = (row_v.get("nature_mutation") or "").lower()
                            type_v = (row_v.get("type_local") or "").lower()
                            if "vente" not in nature_v: continue
                            if dvf_types_ok and not any(kw in type_v for kw in dvf_types_ok): continue
                            try:
                                sv = float((row_v.get("surface_reelle_bati") or "0").replace(",","."))
                                pv2 = float((row_v.get("valeur_fonciere") or "0").replace(",",".").replace(" ",""))
                            except: continue
                            if sv <= 0 or pv2 < 5000: continue
                            if surface and surface > 0 and abs(sv-surface)/max(surface,1) > 0.60: continue
                            pm2_v = pv2/sv if sv > 0 else 0
                            if pm2_v < 200 or pm2_v > 8000: continue
                            adr_v = " ".join(filter(None,[row_v.get("numero_voie",""),row_v.get("type_voie",""),row_v.get("nom_voie","")])).strip().upper()
                            results.append({"Adresse": adr_v or "—","Ville": "Vannes","Prix": int(pv2),"Surface": int(sv),"Statut": "Vendu","Source": f"DVF {annee} Vannes","Date": row_v.get("date_mutation","")[:7] or annee})
                            if len(results) >= limit: break
                    except: continue
        except: pass

    if not results:
        for annee in ["2024", "2023", "2022"]:
            if len(results) >= limit:
                break
            try:
                csv_url2 = f"https://files.data.gouv.fr/geo-dvf/latest/csv/{annee}/communes/{dept}/{code_commune}.csv"
                with _ur.urlopen(_ur.Request(csv_url2, headers={"User-Agent": "Barbier-Immobilier/1.0"}), timeout=15) as r2:
                    raw2 = r2.read().decode("utf-8", errors="ignore")
                reader2 = _csv2.DictReader(_io2.StringIO(raw2))
                for row2 in reader2:
                    nature2 = (row2.get("nature_mutation") or "").lower()
                    type_l2 = (row2.get("type_local") or "").lower()
                    if "vente" not in nature2: continue
                    if dvf_types_ok and not any(kw in type_l2 for kw in dvf_types_ok): continue
                    try:
                        s2 = float((row2.get("surface_reelle_bati") or "0").replace(",","."))
                        p2 = float((row2.get("valeur_fonciere") or "0").replace(",",".").replace(" ",""))
                    except: continue
                    if s2 <= 0 or p2 < 5000: continue
                    pm2_2 = p2 / s2 if s2 > 0 else 0
                    if pm2_2 < 200 or pm2_2 > 8000: continue
                    adr2 = " ".join(filter(None,[row2.get("numero_voie",""),row2.get("type_voie",""),row2.get("nom_voie","")])).strip().upper()
                    results.append({"Adresse": adr2 or "—","Ville": ville,"Prix": int(p2),"Surface": int(s2),"Statut": "Vendu","Source": f"DVF {annee}","Date": row2.get("date_mutation","")[:7] or annee})
                    if len(results) >= limit: break
            except: continue

    # 4. Stats
    top = sorted(results, key=lambda x: x["Date"], reverse=True)[:limit]
    pm2_list = [r["Prix"] / r["Surface"] for r in top if r["Surface"] > 0]
    pm2_median = sorted(pm2_list)[len(pm2_list)//2] if pm2_list else 0
    prix_list  = [r["Prix"] for r in top]
    prix_moyen = sum(prix_list) // len(prix_list) if prix_list else 0

    stats = {
        "pm2_median": int(pm2_median),
        "prix_moyen": prix_moyen,
        "nb": len(top),
        "code_commune": code_commune,
    }
    return top, pm2_median, stats


@app.route("/dvf-comparables", methods=["GET", "POST"])
def dvf_comparables():
    """
    Recherche de comparables DVF (CSV data.gouv) + BienIci pour un bien commercial.
    DVF CSV: files.data.gouv.fr/geo-dvf/latest/csv/{annee}/communes/{dept}/{code_commune}.csv
    """
    try:
        import re as _re, csv as _csv, io as _io, time as _time

        data = request.get_json(silent=True) or {}
        reference = request.args.get("reference") or data.get("reference")
        ville = request.args.get("ville") or data.get("ville") or "Vannes"
        code_postal = str(request.args.get("code_postal") or data.get("code_postal") or "56000")
        type_local = request.args.get("type_local") or data.get("type_local") or "Local commercial"
        surface = float(request.args.get("surface") or data.get("surface") or 0)

        # Charger depuis SeaTable si reference fournie
        if reference:
            try:
                at, uuid = _st_token()
                params = _up.urlencode({"table_name": "01_Biens", "convert_keys": "true", "limit": 300})
                req2 = _ur.Request(f"https://cloud.seatable.io/api-gateway/api/v2/dtables/{uuid}/rows/?{params}",
                    headers={"Authorization": f"Token {at}"})
                with _ur.urlopen(req2) as resp:
                    rows = _json.load(resp)["rows"]
                row = next((r for r in rows if r.get("Reference") == reference), None)
                if row:
                    ville = row.get("Ville") or ville
                    code_postal = str(row.get("Code postal") or code_postal)
                    type_local = row.get("Type de bien") or type_local
                    surface = float(row.get("Surface") or surface or 0)
            except:
                pass

        # 1. Résoudre code commune INSEE
        code_commune = None
        try:
            geo_url = f"https://geo.api.gouv.fr/communes?nom={_up.quote(ville)}&fields=code,nom,codesPostaux&boost=population&limit=5"
            with _ur.urlopen(_ur.Request(geo_url, headers={"User-Agent": "Barbier-Immobilier/1.0"})) as r:
                geo_data = _json.load(r)
            if geo_data:
                match = next((c for c in geo_data if code_postal in c.get("codesPostaux", [])), None) or                         next((c for c in geo_data if c["nom"].lower() == ville.lower()), None) or                         geo_data[0]
                code_commune = match["code"]
        except:
            pass
        if not code_commune:
            # Fallback: le code commune est souvent dept + padding du code postal
            dept = code_postal[:2]
            code_commune = code_postal  # approximation

        dept = code_commune[:2]

        # 2. DVF — CSV par commune (plusieurs années)
        dvf_results = []
        dvf_error = None
        annees = ["2024", "2023", "2022"]

        for annee in annees:
            if len(dvf_results) >= 8:
                break
            try:
                csv_url = f"https://files.data.gouv.fr/geo-dvf/latest/csv/{annee}/communes/{dept}/{code_commune}.csv"
                req_csv = _ur.Request(csv_url, headers={"User-Agent": "Barbier-Immobilier/1.0"})
                with _ur.urlopen(req_csv, timeout=15) as r:
                    content = r.read().decode("utf-8", errors="ignore")

                reader = _csv.DictReader(_io.StringIO(content))
                commercial_kw = ["commercial", "industriel", "bureau"]

                for row in reader:
                    nature = (row.get("nature_mutation") or "").lower()
                    type_l = (row.get("type_local") or "").lower()
                    surf_str = row.get("surface_reelle_bati") or ""
                    prix_str = row.get("valeur_fonciere") or ""

                    if not surf_str or not prix_str:
                        continue
                    try:
                        surf = float(surf_str.replace(",", "."))
                        prix = float(prix_str.replace(",", ".").replace(" ", ""))
                    except:
                        continue

                    if surf <= 0 or prix <= 0 or prix < 1000:
                        continue
                    if "vente" not in nature:
                        continue
                    if not any(kw in type_l for kw in commercial_kw):
                        continue
                    if surface > 0 and abs(surf - surface) / max(surface, 1) > 0.65:
                        continue

                    adresse = " ".join(filter(None, [
                        row.get("adresse_numero", "").strip(),
                        row.get("adresse_nom_voie", "").strip()
                    ])).strip()

                    dvf_results.append({
                        "source": f"DVF {annee} (vendu)",
                        "adresse": adresse,
                        "ville": row.get("nom_commune") or ville,
                        "surface": surf,
                        "prix": prix,
                        "prix_m2": round(prix / surf) if surf > 0 else 0,
                        "date": row.get("date_mutation") or "",
                        "url": "",
                        "type_bien": row.get("type_local") or type_local,
                        "description": f"{row.get('type_local','?')} — {round(prix):,} € — {row.get('date_mutation','')} — {surf} m²".replace(",", " ")
                    })

                    if len(dvf_results) >= 10:
                        break
            except Exception as e:
                dvf_error = str(e)
                continue

        # Trier par date desc + limiter
        dvf_results.sort(key=lambda x: x.get("date", ""), reverse=True)
        dvf_results = dvf_results[:8]

        if not dvf_results and dvf_error:
            dvf_results = [{"source": "DVF", "erreur": dvf_error, "prix": 0}]

        # 3. BienIci
        bienici_results = []
        try:
            ville_slug = ville.lower()
            for ch, rep in [(" ", "-"), ("é","e"),("è","e"),("ê","e"),("à","a"),("ô","o"),("î","i"),("û","u"),("ç","c")]:
                ville_slug = ville_slug.replace(ch, rep)
            cp_str = code_postal.replace(" ", "")
            bi_url = f"https://www.bienici.com/recherche/vente/{ville_slug}-{cp_str}?categories=bureaux_locaux_commerciaux&tri=prix-croissant"
            req_bi = _ur.Request(bi_url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "fr-FR,fr;q=0.9"
            })
            with _ur.urlopen(req_bi, timeout=15) as r:
                html = r.read().decode("utf-8", errors="ignore")

            # Extraire JSON embarqué
            for pat in [
                r'"realEstateAds"\s*:\s*(\[[\s\S]{50,40000}?\](?=\s*[,}]))',
            ]:
                m = _re.search(pat, html)
                if m:
                    try:
                        ads = _json.loads(m.group(1))
                        for ad in ads[:8]:
                            surf = float(ad.get("surfaceArea") or ad.get("surface") or 0)
                            prix = float(ad.get("price") or 0)
                            if surf <= 0 or prix <= 0:
                                continue
                            bienici_results.append({
                                "source": "BienIci (actif)",
                                "adresse": (ad.get("address") or {}).get("street") or ad.get("title") or "",
                                "ville": ad.get("city") or ville,
                                "surface": surf,
                                "prix": prix,
                                "prix_m2": round(prix / surf) if surf > 0 else 0,
                                "date": _time.strftime("%Y-%m-%d"),
                                "url": "https://www.bienici.com" + (ad.get("publicationUrl") or ""),
                                "type_bien": type_local,
                                "description": (ad.get("description") or "")[:200] or f"Annonce active — {round(prix):,} €".replace(",", " ")
                            })
                        break
                    except:
                        continue
        except Exception as e:
            bienici_results = [{"source": "BienIci", "erreur": str(e), "prix": 0}]

        all_valid = [r for r in dvf_results + bienici_results if r.get("prix", 0) > 0]
        return jsonify({
            "reference": reference or "",
            "ville": ville,
            "code_commune": code_commune,
            "dvf": dvf_results,
            "bienici": bienici_results,
            "all": all_valid,
            "total": len(all_valid),
            "dvf_count": len([r for r in dvf_results if r.get("prix", 0) > 0]),
            "bienici_count": len([r for r in bienici_results if r.get("prix", 0) > 0])
        })

    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500

@app.route("/test-modelo", methods=["GET"])
def test_modelo():
    """Test de connectivité API Modelo depuis l'IP Railway"""
    import urllib.request as ur
    try:
        req = ur.Request(
            "https://webapi.netty.fr/apiv1/products?limit=2",
            headers={"x-netty-api-key": "627abdc3-8d06-4249-8245-0e44ce1aaae8"}
        )
        with ur.urlopen(req, timeout=10) as r:
            import json as _json
            body = _json.loads(r.read().decode())
            return jsonify({
                "status": "ok",
                "ip_railway": request.environ.get("HTTP_X_FORWARDED_FOR", "unknown"),
                "modelo_count": body.get("count", 0),
                "sample": body.get("data", [])[:1]
            })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ══════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════
# FICHE COMMERCIALE — v3.13
# Style avis de valeur : sec_title, cellules arrondies, carte OSM
# 3 pages : 01 Le Bien / 02 La Ville / 03 Pourquoi Barbier
# ══════════════════════════════════════════════════════════════

_ORANGE_FC = _colors.HexColor("#F0795B")

def _footer_fiche(c, n, total=3):
    c.setFillColor(_BLEU_F); c.rect(0, 0, _W, 9*_mm, fill=1, stroke=0)
    c.setFillColor(_BLANC); c.setFont("Helvetica", 6.5)
    c.drawString(14*_mm, 3.5*_mm,
        "Barbier Immobilier — 2 place Albert Einstein, 56000 Vannes — 02.97.47.11.11 — barbierimmobilier.com")
    c.drawRightString(_W - 14*_mm, 3.5*_mm, f"{n} / {total}")

def _safe_str(v):
    """Nettoie les caractères problématiques sans perdre les accents."""
    if v is None: return ""
    s = str(v)
    s = s.replace(" ", " ").replace("\u00a0", " ")
    s = s.replace("\u00b2", "2").replace("\u00b3", "3")
    # Remplacer les guillemets typographiques par des guillemets droits
    s = s.replace("\u2018", "'").replace("\u2019", "'")
    s = s.replace("\u201c", '"').replace("\u201d", '"')
    s = s.replace("\u2013", "-").replace("\u2014", "-")
    s = s.replace("\u2026", "...")
    return s

def _fmt_m2(v):
    try: return f"{int(float(v))} m2"
    except: return str(v)

def _fmt_eur(v, suffix=""):
    if not v: return None
    try:
        s = f"{int(float(v)):,}".replace(",", " ")
        return f"{s} {suffix}".strip() if suffix else f"{s} EUR"
    except: return str(v)

def _fiche_header(c, d):
    """En-tête : barre teal + logo + ref. Retourne y de départ."""
    ML = 14*_mm; MR = 14*_mm
    c.setFillColor(_BLEU)
    c.rect(0, _H - 12*_mm, _W, 12*_mm, fill=1, stroke=0)
    _logo(c, ML, _H - 11*_mm, w=22*_mm)
    ref   = _safe_str(d.get("Reference","") or d.get("reference",""))
    ville = _safe_str(d.get("Ville","Vannes") or "Vannes")
    c.setFillColor(_BLANC); c.setFont("Helvetica-Bold", 10)
    c.drawRightString(_W - MR, _H - 5*_mm, "FICHE COMMERCIALE")
    c.setFont("Helvetica", 7); c.setFillColor(_colors.HexColor("#FFFFFFCC"))
    c.drawRightString(_W - MR, _H - 9.5*_mm, "Ref. " + ref + "  \u00b7  " + ville + "  \u00b7  Barbier Immobilier")
    return _H - 14*_mm


def _fiche_cell(c, cx, cy, cw, ch, label, value, r=3):
    """Cellule arrondie gris clair style avis de valeur."""
    from reportlab.lib.units import mm as _mm2
    c.saveState()
    c.setFillColor(_colors.HexColor("#F3F4F6"))
    c.roundRect(cx, cy - ch, cw, ch, r, fill=1, stroke=0)
    c.setFillColor(_colors.HexColor("#6B7280")); c.setFont("Helvetica", 6.5)
    c.drawString(cx + 6, cy - 10, label)
    c.setFillColor(_colors.HexColor("#1F2937")); c.setFont("Helvetica-Bold", 8.5)
    val_str = _safe_str(str(value)) if value not in (None, "", "—") else "—"
    # autofit
    for fsz in [8.5, 7.5, 6.5]:
        c.setFont("Helvetica-Bold", fsz)
        if c.stringWidth(val_str, "Helvetica-Bold", fsz) < cw - 12: break
    c.drawString(cx + 6, cy - 22, val_str)
    c.restoreState()

def _fiche_sec(c, x, y, txt):
    """Titre de section style avis de valeur : barre orange + texte teal."""
    BAR_H = 13; BAR_W = 3
    c.saveState()
    c.setFillColor(_ORANGE)
    c.rect(x, y - BAR_H + 3, BAR_W, BAR_H, fill=1, stroke=0)
    c.setFillColor(_BLEU_F); c.setFont("Helvetica-Bold", 8)
    c.drawString(x + 8, y - BAR_H + 5, txt.upper())
    c.restoreState()
    return y - BAR_H - 8   # curseur après le titre



def _cadastre_or_osm_map(ref_cadastrale, adresse, ville, zoom=18, tiles=3):
    """Carte de localisation précise :
    1. Si ref cadastrale dispo → apicarto.ign.fr → coordonnées exactes → OSM tiles zoom 18
    2. Sinon → OSM via Nominatim sur l'adresse (zoom 17)
    Retourne (PIL.Image, lat, lon)
    """
    import math as _math2
    lat, lon = None, None

    # Essai 1 : coordonnées depuis référence cadastrale
    if ref_cadastrale and ref_cadastrale != "—":
        try:
            # Parser ref : "56260 DE 0107" → code_insee=56260, section=DE, numero=0107
            import re as _re2
            m = _re2.match(r'(\d{5})\s+([A-Z]{1,2})\s+(\d{4})', ref_cadastrale.upper())
            if m:
                code_insee, section, numero = m.group(1), m.group(2), m.group(3)
                r_cad = requests.get(
                    "https://apicarto.ign.fr/api/cadastre/parcelle",
                    params={"code_insee": code_insee, "section": section, "numero": numero},
                    headers={"User-Agent": "BarbierImmo/1.0"}, timeout=8
                )
                if r_cad.status_code == 200:
                    feats = r_cad.json().get("features", [])
                    if feats:
                        coords_list = feats[0]["geometry"]["coordinates"][0][0]
                        lons = [c[0] for c in coords_list]
                        lats = [c[1] for c in coords_list]
                        lon = sum(lons)/len(lons)
                        lat = sum(lats)/len(lats)
        except Exception:
            lat, lon = None, None

    # Essai 2 : Nominatim sur adresse
    if lat is None or lon is None:
        try:
            import urllib.parse as _up2
            q = _up2.quote_plus(f"{adresse}, {ville}, France")
            url = f"https://nominatim.openstreetmap.org/search?q={q}&format=json&limit=1"
            req = _ur.Request(url, headers={"User-Agent": "BarbierImmo/1.0"})
            with _ur.urlopen(req, timeout=8) as res:
                data = _json.load(res)
            if data:
                lat, lon = float(data[0]["lat"]), float(data[0]["lon"])
                zoom = 17  # un peu moins précis sans cadastre
        except Exception:
            lat, lon = 47.6580, -2.7600

    # Assembler tiles OSM centrées sur lat/lon
    n = 2**zoom
    cx = int((lon+180)/360*n)
    cy = int((1-_math2.log(_math2.tan(_math2.radians(lat))+1/_math2.cos(_math2.radians(lat)))/_math2.pi)/2*n)
    half = tiles//2
    rows = []
    for row in range(tiles):
        ri = []
        for col in range(tiles):
            tx, ty = cx-half+col, cy-half+row
            u = f"https://tile.openstreetmap.org/{zoom}/{tx}/{ty}.png"
            rq = _ur.Request(u, headers={"User-Agent": "BarbierImmo/1.0"})
            with _ur.urlopen(rq, timeout=10) as res2:
                tile = _PILImage.open(_BytesIO(res2.read())).convert("RGB")
            ri.append(tile)
        rows.append(ri)
    tw, th = rows[0][0].width, rows[0][0].height
    result = _PILImage.new("RGB", (tw*tiles, th*tiles))
    for row in range(tiles):
        for col in range(tiles):
            result.paste(rows[row][col], (col*tw, row*th))
    return result, lat, lon


def _fiche_page1(c, d):
    """Page 1 : style trame PowerPoint. Photo + carte cadastre cote a cote."""
    import io as _io
    ML = 14*_mm; MR = 14*_mm; CW = _W - ML - MR; GAP = 4*_mm

    y = _fiche_header(c, d)

    BAND_H = 14*_mm
    c.setFillColor(_BLEU_F)
    c.rect(0, y - BAND_H, _W, BAND_H, fill=1, stroke=0)

    ref    = d.get("Reference","")    or d.get("reference","")
    type_b = d.get("Type de bien","") or d.get("type_bien","")  or "Bien"
    surf   = d.get("Surface")         or d.get("surface")       or 0
    stat_m = d.get("Statut mandat","") or d.get("statut_mandat","") or ""
    loyer  = d.get("Loyer mensuel")   or d.get("loyer_mensuel") or 0
    prix   = d.get("Prix de vente")   or d.get("prix_vente")    or 0
    adresse= d.get("Adresse","")      or d.get("adresse","")    or ""
    ville  = d.get("Ville","Vannes")  or d.get("ville","Vannes") or "Vannes"

    surf_str = _fmt_m2(surf) if surf else ""
    if loyer:
        try:   val_str = str(int(float(loyer))) + " EUR HT/mois"
        except: val_str = str(loyer)
    elif prix:
        val_str = _pfmt(prix)
    else:
        val_str = ""

    parts = []
    if ref:      parts.append("Ref. " + _safe_str(ref))
    if type_b:   parts.append(_safe_str(type_b))
    if surf_str: parts.append(surf_str)
    if val_str:  parts.append(val_str)
    if stat_m:   parts.append(_safe_str(stat_m.upper()))
    line = "  \u00b7  ".join(parts)
    c.setFillColor(_BLANC)
    for fsz in [10, 9, 8, 7]:
        c.setFont("Helvetica-Bold", fsz)
        if c.stringWidth(line, "Helvetica-Bold", fsz) < _W - 28*_mm: break
    c.drawString(ML, y - BAND_H + 4.5*_mm, line)
    y -= BAND_H + 6*_mm

    y = _fiche_sec(c, ML, y, "01 \u2014 Le Bien")

    cell_h = 26; cell_gap = 3; col_w3 = (CW - 2*GAP) / 3
    pmr  = d.get("PMR","")       or ""
    dpe  = d.get("DPE classe","") or d.get("dpe_classe","") or ""
    ges  = d.get("GES classe","") or d.get("ges_classe","") or ""
    tb   = d.get("Type de bail","") or d.get("type_bail","") or ""
    act  = d.get("Activit\u00e9","")  or d.get("activite","") or ""

    cells = [
        ("Type de bien",  _safe_str(type_b)),
        ("Surface",       surf_str or "\u2014"),
        ("Activit\u00e9",      _safe_str(act) if act else "\u2014"),
        ("Type de bail",  _safe_str(tb)  if tb  else "\u2014"),
        ("PMR / Acc\u00e8s",   _safe_str(pmr) if pmr else "\u2014"),
        ("Mandat",        _safe_str(stat_m) if stat_m else "\u2014"),
    ]
    for i, (lbl, val) in enumerate(cells):
        col = i % 3; row = i // 3
        cx = ML + col * (col_w3 + cell_gap)
        cy = y - row * (cell_h + cell_gap)
        _fiche_cell(c, cx, cy, col_w3, cell_h, lbl, val)
    y -= 2*(cell_h + cell_gap) + 10

    if dpe or ges:
        dpe_cells = []
        if dpe: dpe_cells.append(("Classe DPE", "Classe " + _safe_str(dpe)))
        if ges: dpe_cells.append(("Classe GES", "Classe " + _safe_str(ges)))
        ncols = len(dpe_cells)
        dpe_cw = (CW - (ncols-1)*cell_gap) / ncols
        for i,(lbl,val) in enumerate(dpe_cells):
            cx = ML + i*(dpe_cw + cell_gap)
            _fiche_cell(c, cx, y, dpe_cw, cell_h, lbl, val)
        y -= cell_h + 10

    photo_raw = d.get("Photo bien (URL)","") or d.get("photo_url","") or d.get("Photo bien","") or ""
    photo_img = None
    if photo_raw:
        try:
            if photo_raw.startswith("data:"):
                import base64 as _b64
                _, b64data = photo_raw.split(",", 1)
                photo_img = _ir(b64data)
            else:
                photo_img = _fetch_photo_image(photo_raw)
        except Exception:
            photo_img = None

    CARTE_H = 72*_mm
    map_pil = None
    try:
        map_pil, map_lat, map_lon = _cadastre_or_osm_map(
            d.get("ref_cadastrale","") or "",
            adresse, ville, zoom=18, tiles=3
        )
    except Exception:
        pass

    if photo_img and map_pil:
        y = _fiche_sec(c, ML, y, "02 \u2014 Photo du bien")
        HALF_GAP = 4*_mm
        PHOTO_W = CW * 0.58 - HALF_GAP/2
        CARTE_W = CW * 0.42 - HALF_GAP/2
        try:
            iw, ih = photo_img.getSize()
            dh = min(CARTE_H, PHOTO_W * ih / iw)
            dw = dh * iw / ih
            dx = ML; dy = y - CARTE_H
            c.saveState()
            path = c.beginPath(); path.roundRect(ML, dy, PHOTO_W, CARTE_H, 3*_mm)
            c.clipPath(path, stroke=0, fill=0)
            c.drawImage(photo_img, dx + (PHOTO_W - dw)/2, dy + (CARTE_H - dh)/2, dw, dh, mask="auto")
            c.restoreState()
        except Exception:
            try: c.restoreState()
            except: pass
        try:
            buf_map = _BytesIO(); map_pil.save(buf_map, "PNG"); buf_map.seek(0)
            from reportlab.lib.utils import ImageReader as _IR2
            map_rl = _IR2(buf_map)
            cx2 = ML + PHOTO_W + HALF_GAP
            c.saveState()
            path2 = c.beginPath(); path2.roundRect(cx2, y - CARTE_H, CARTE_W, CARTE_H, 3*_mm)
            c.clipPath(path2, stroke=0, fill=0)
            c.drawImage(map_rl, cx2, y - CARTE_H, CARTE_W, CARTE_H, mask="auto")
            c.restoreState()
            c.setFillColor(_ORANGE)
            c.circle(cx2 + CARTE_W/2, y - CARTE_H/2, 3*_mm, fill=1, stroke=0)
        except Exception:
            try: c.restoreState()
            except: pass
        y -= CARTE_H + 4*_mm
    elif photo_img:
        y = _fiche_sec(c, ML, y, "02 \u2014 Photo du bien")
        PHOTO_MAX_H = 70*_mm
        try:
            iw, ih = photo_img.getSize()
            dw = CW; dh = min(dw * ih / iw, PHOTO_MAX_H)
            dw = dh * iw / ih
            dy = y - dh
            c.saveState()
            path = c.beginPath(); path.roundRect(ML, dy, CW, dh, 3*_mm)
            c.clipPath(path, stroke=0, fill=0)
            c.drawImage(photo_img, ML + (CW - dw)/2, dy, dw, dh, mask="auto")
            c.restoreState()
            y = dy - 8*_mm
        except Exception:
            try: c.restoreState()
            except: pass
        y = _fiche_sec(c, ML, y, "03 \u2014 Localisation")
        if map_pil:
            try:
                buf_map = _BytesIO(); map_pil.save(buf_map, "PNG"); buf_map.seek(0)
                from reportlab.lib.utils import ImageReader as _IR3
                map_rl = _IR3(buf_map)
                c.saveState()
                path3 = c.beginPath(); path3.roundRect(ML, y - CARTE_H, CW, CARTE_H, 3*_mm)
                c.clipPath(path3, stroke=0, fill=0)
                c.drawImage(map_rl, ML, y - CARTE_H, CW, CARTE_H, mask="auto")
                c.restoreState()
                c.setFillColor(_ORANGE)
                c.circle(ML + CW/2, y - CARTE_H/2, 3*_mm, fill=1, stroke=0)
            except Exception:
                try: c.restoreState()
                except: pass
        y -= CARTE_H
    else:
        y = _fiche_sec(c, ML, y, "02 \u2014 Localisation")
        c.setFillColor(_colors.HexColor("#E8EEF4")); c.setStrokeColor(_colors.HexColor("#CCCCCC")); c.setLineWidth(0.5)
        c.roundRect(ML, y - CARTE_H, CW, CARTE_H, 3*_mm, fill=1, stroke=1)
        c.setFillColor(_colors.HexColor("#999999")); c.setFont("Helvetica", 9)
        c.drawCentredString(ML + CW/2, y - CARTE_H/2, "Localisation")
        y -= CARTE_H

    c.setFillColor(_colors.HexColor("#6B7280")); c.setFont("Helvetica", 6.5)
    adr_leg = _safe_str((adresse + ", " + ville).strip(", "))
    c.drawString(ML, y - 4*_mm, adr_leg)
    _footer_fiche(c, 1, 3)


def _fiche_page2(c, d):
    """Page 2 : style PowerPoint. Bandeau ville teal, texte secteur, opportunite, financiers."""
    ML = 14*_mm; MR = 14*_mm; CW = _W - ML - MR
    adresse = d.get("Adresse","")    or d.get("adresse","")   or ""
    ville   = d.get("Ville","Vannes") or d.get("ville","Vannes") or "Vannes"
    type_b  = d.get("Type de bien","") or ""
    surf    = d.get("Surface","")    or ""

    y = _fiche_header(c, d)
    y -= 4*_mm

    y = _fiche_sec(c, ML, y, "04 \u2014 La Ville & Le Quartier")

    photo_ville_url = d.get("photo_ville_url","") or d.get("Photo ville","") or ""
    photo_ville = None
    if photo_ville_url:
        try:
            if photo_ville_url.startswith("data:"):
                import base64 as _b64
                _, b64data = photo_ville_url.split(",", 1)
                photo_ville = _ir(b64data)
            else:
                photo_ville = _fetch_photo_image(photo_ville_url)
        except Exception:
            photo_ville = None

    VILLE_PHOTO_H = 48*_mm
    if photo_ville:
        try:
            iw, ih = photo_ville.getSize()
            dh = min(VILLE_PHOTO_H, CW * ih / iw)
            dw = dh * iw / ih
            dx = ML + (CW - dw)/2; dy = y - VILLE_PHOTO_H
            c.saveState()
            path = c.beginPath(); path.roundRect(ML, dy, CW, VILLE_PHOTO_H, 3*_mm)
            c.clipPath(path, stroke=0, fill=0)
            c.drawImage(photo_ville, dx, dy + (VILLE_PHOTO_H - dh)/2, dw, dh, mask="auto")
            c.restoreState()
            c.setFillColor(_colors.HexColor("#1B3A5CCC"))
            c.rect(ML, dy, CW, 12*_mm, fill=1, stroke=0)
            c.setFillColor(_BLANC); c.setFont("Helvetica-Bold", 13)
            c.drawString(ML + 6*_mm, dy + 3.5*_mm, _safe_str(ville.upper()))
            y = dy - 8*_mm
        except Exception:
            try: c.restoreState()
            except: pass
            photo_ville = None

    if not photo_ville:
        BAND_H = 20*_mm
        c.setFillColor(_BLEU)
        c.rect(ML, y - BAND_H, CW, BAND_H, fill=1, stroke=0)
        c.setFillColor(_BLANC); c.setFont("Helvetica-Bold", 14)
        c.drawString(ML + 6*_mm, y - BAND_H/2 - 2, _safe_str(ville.upper()))
        c.setFont("Helvetica", 8); c.setFillColor(_colors.HexColor("#FFFFFFBB"))
        c.drawString(ML + 6*_mm, y - BAND_H + 3*_mm, "Secteur g\u00e9ographique \u00b7 Morbihan")
        y -= BAND_H + 8*_mm

    desc_v = d.get("Description ville","") or d.get("description_ville","") or ""
    if not desc_v:
        try:
            desc_v = _gpt_quartier(adresse, ville, type_b, surf)
        except Exception:
            desc_v = ""

    if desc_v:
        from reportlab.platypus import Paragraph as _Para2
        from reportlab.lib.styles import ParagraphStyle as _PS2
        ps_v = _PS2("dv", fontName="Helvetica", fontSize=9,
                    textColor=_colors.HexColor("#1F2937"), leading=14)
        safe_v = _safe_str(desc_v).replace("\n", "<br/>")
        para_v = _Para2(safe_v, ps_v)
        _, ph = para_v.wrap(CW, 9999)
        para_v.drawOn(c, ML, y - ph)
        y -= ph + 10*_mm

    y = _fiche_sec(c, ML, y, "05 \u2014 L'Opportunite")

    desc_c = d.get("Description commerciale","") or d.get("description_commerciale","") or ""
    vp     = d.get("Version portail","")          or d.get("version_portail","")          or ""
    annonce = desc_c or vp or ""

    if annonce:
        from reportlab.platypus import Paragraph as _Para3
        from reportlab.lib.styles import ParagraphStyle as _PS3
        ps_a = _PS3("da", fontName="Helvetica", fontSize=9,
                    textColor=_colors.HexColor("#1F2937"), leading=14)
        safe_a = _safe_str(annonce).replace("\n", "<br/>")
        para_a = _Para3(safe_a, ps_a)
        _, pha = para_a.wrap(CW, 9999)
        para_a.drawOn(c, ML, y - pha)
        y -= pha + 8*_mm

    loyer_m  = d.get("Loyer mensuel")        or d.get("loyer_mensuel")        or 0
    loyer_a  = d.get("Loyer annuel")         or d.get("loyer_annuel")         or 0
    hono     = d.get("Honoraires locataire") or d.get("honoraires_locataire") or 0
    depot    = d.get("D\u00e9p\u00f4t de garantie")    or d.get("depot_garantie")       or 0
    index    = d.get("Indexation")           or d.get("indexation")           or ""
    prix_v   = d.get("Prix de vente")        or d.get("prix_vente")           or 0
    locataire= d.get("Locataire")            or d.get("locataire")            or ""
    bail_type= d.get("Type de bail")         or d.get("type_bail")            or ""

    def _fmt_eur(v):
        try: return str(int(float(v))) + " EUR HT"
        except: return str(v)
    def _fmt_eur_mois(v):
        try: return str(int(float(v))) + " EUR HT/mois"
        except: return str(v)

    fin_cells = []
    if loyer_m:  fin_cells.append(("Loyer mensuel",  _fmt_eur_mois(loyer_m)))
    la = 0
    if loyer_a:
        try: la = int(float(loyer_a))
        except: la = 0
    if not la and loyer_m:
        try: la = int(float(loyer_m)) * 12
        except: la = 0
    if la: fin_cells.append(("Loyer annuel", _fmt_eur(la)))
    if prix_v:   fin_cells.append(("Prix de vente",  _pfmt(prix_v)))
    if depot:    fin_cells.append(("D\u00e9p\u00f4t de garantie", _safe_str(str(depot))))
    if index:    fin_cells.append(("Indexation",      _safe_str(index)))
    if locataire:fin_cells.append(("Locataire actuel",_safe_str(locataire)))
    if bail_type:fin_cells.append(("Type de bail",    _safe_str(bail_type)))
    if hono:     fin_cells.append(("Honoraires",      _safe_str(str(hono))))

    if fin_cells:
        y = _fiche_sec(c, ML, y, "06 \u2014 Informations Financieres")
        GAP = 3; cell_h = 26
        ncols = min(3, len(fin_cells))
        col_wf = (CW - (ncols-1)*GAP) / ncols
        for i, (lbl, val) in enumerate(fin_cells):
            col = i % ncols; row = i // ncols
            cx = ML + col*(col_wf + GAP)
            cy = y - row*(cell_h + GAP)
            _fiche_cell(c, cx, cy, col_wf, cell_h, lbl, val if val else "\u2014")
        nrows = (len(fin_cells) + ncols - 1) // ncols
        y -= nrows*(cell_h + GAP) + 6*_mm

    _footer_fiche(c, 2, 3)


def _page6_fiche(c):
    """Page 3 — Pourquoi Barbier — footer 3/3.
    Contenu identique à _page6() mais avec _footer_fiche(c,3,3)."""
    c.setFillColor(_BLEU); c.rect(0,_H*0.5,_W,_H*0.5,fill=1,stroke=0)
    c.setFillColor(_BLANC); c.rect(0,0,_W,_H*0.5,fill=1,stroke=0)
    _logo(c, _W-54*_mm, _H-56*_mm, w=36*_mm)
    c.setFillColor(_BLANC); c.setFont("Helvetica",11)
    c.drawString(14*_mm,_H-20*_mm,"VOTRE PARTENAIRE EN IMMOBILIER COMMERCIAL")
    c.setFont("Helvetica-Bold",28); c.drawString(14*_mm,_H-38*_mm,"Barbier Immobilier")
    c.setFont("Helvetica",14); c.setFillColor(_colors.HexColor("#FFFFFFCC"))
    c.drawString(14*_mm,_H-50*_mm,"Votre projet devient le n\u00f4tre")
    c.setFillColor(_ORANGE); c.rect(14*_mm,_H-54*_mm,50*_mm,2.5*_mm,fill=1,stroke=0)
    for i,(num,lbl) in enumerate([("33 ans","d'expertise locale"),("+5 000","clients accompagn\u00e9s"),("3 m\u00e9tiers","vente · location · cession")]):
        sx=14*_mm+i*(_W-28*_mm)/3
        c.setFillColor(_BLANC); c.setFont("Helvetica-Bold",20); c.drawString(sx+3*_mm,_H*0.52+14*_mm,num)
        c.setFont("Helvetica",9); c.setFillColor(_colors.HexColor("#FFFFFFBB")); c.drawString(sx+3*_mm,_H*0.52+8*_mm,lbl)
    for i,(title,desc) in enumerate([
        ("Estimation & Valorisation","Analyse pr\u00e9cise de la valeur v\u00e9nale bas\u00e9e sur les donn\u00e9es du march\u00e9 local et notre expertise terrain."),
        ("Vente & Transaction","Diffusion multi-portails, s\u00e9lection d'acqu\u00e9reurs qualifi\u00e9s, n\u00e9gociation et suivi jusqu'\u00e0 la signature."),
        ("Location Commerciale","Recherche de locataires, r\u00e9daction des baux, gestion locative compl\u00e8te."),
        ("Cession d'Entreprise","Accompagnement expert pour la cession ou reprise de fonds de commerce.")]):
        sws=(_W-28*_mm-8*_mm)/2; shs=32*_mm; col=i%2; row2=i//2
        sx4=14*_mm+col*(sws+8*_mm); sy4=_H*0.48-4*_mm-row2*(shs+5*_mm)
        c.setFillColor(_GRIS); c.roundRect(sx4,sy4-shs,sws,shs,2*_mm,fill=1,stroke=0)
        c.setFillColor(_ORANGE); c.rect(sx4,sy4-shs,3*_mm,shs,fill=1,stroke=0)
        c.setFillColor(_BLEU_F); c.setFont("Helvetica-Bold",10); c.drawString(sx4+6*_mm,sy4-8*_mm,title)
        p=_Para(desc,_PS("ds3",fontName="Helvetica",fontSize=8.5,textColor=_GTEXTE,leading=12))
        _,ph=p.wrap(sws-10*_mm,9999); p.drawOn(c,sx4+6*_mm,sy4-shs+5*_mm)
    c.setFillColor(_BLEU_F); c.roundRect(14*_mm,14*_mm,_W-28*_mm,20*_mm,2*_mm,fill=1,stroke=0)
    c.setFillColor(_BLANC); c.setFont("Helvetica-Bold",10)
    c.drawString(20*_mm,28*_mm,"2 place Albert Einstein, 56000 Vannes")
    c.setFont("Helvetica",9)
    c.drawString(20*_mm,21*_mm,"02.97.47.11.11  ·  contact@barbierimmobilier.com  ·  barbierimmobilier.com")
    _footer_fiche(c, 3, 3)


def generate_fiche_commerciale_pdf(d):
    buf = _BytesIO()
    cv  = _canvas.Canvas(buf, pagesize=_A4)
    cv.setTitle(f"Fiche Commerciale - {d.get('Reference', '')}")
    _fiche_page1(cv, d); cv.showPage()
    _fiche_page2(cv, d); cv.showPage()
    _page6_fiche(cv);    cv.showPage()
    cv.save(); buf.seek(0)
    return buf.read()


@app.route("/fiche-commerciale", methods=["POST"])
def fiche_commerciale():
    try:
        d = request.get_json(silent=True) or {}
        if not d:
            return jsonify({"error": "Payload JSON requis"}), 400
        pdf_bytes = generate_fiche_commerciale_pdf(d)
        ref = d.get("Reference", "") or d.get("reference", "bien")
        return Response(pdf_bytes, mimetype="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=Fiche_commerciale_{ref}.pdf"})
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500

@app.route("/dossier-pptx", methods=["POST"])
def dossier_pptx():
    """Génère le dossier de présentation PPTX Barbier (12 slides)."""
    try:
        from gen_pptx import generate_dossier_pptx
        import assets_pptx as _AP
        d = request.get_json(silent=True) or {}
        if not d:
            return jsonify({"error": "Payload JSON requis"}), 400
        # Charger les assets statiques
        assets = {k: getattr(_AP, k) for k in dir(_AP) if k.endswith('_B64')}
        # Carte OSM pour slide 5
        map_buf = None
        try:
            adresse = d.get("Adresse","") or ""
            ville   = d.get("Ville","Vannes") or "Vannes"
            map_pil, _, _ = _cadastre_or_osm_map("", adresse, ville, zoom=16, tiles=3)
            if map_pil:
                import io as _io
                buf = _io.BytesIO(); map_pil.save(buf, "PNG"); buf.seek(0)
                map_buf = buf
        except Exception:
            pass
        pptx_bytes = generate_dossier_pptx(d, assets, map_buf=map_buf)
        ref = d.get("Reference","") or d.get("reference","bien")
        return Response(pptx_bytes,
            mimetype="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            headers={"Content-Disposition": f"attachment; filename=Dossier_{ref}.pptx"})
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500





if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

@app.route("/test-websearch", methods=["GET"])
def test_websearch():
    """Debug endpoint — teste gpt-4o-search-preview."""
    import os, urllib.request, json
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return jsonify({"error": "OPENAI_API_KEY manquante"}), 500
    try:
        payload = json.dumps({
            "model": "gpt-4o-search-preview",
            "messages": [{"role": "user", "content": "Recherche des bureaux en location a Vannes (56) surface 40m2 sur SeLoger ou BienIci. Donne le loyer annuel HT/m2 moyen constate. Reponds uniquement en JSON: {\"pm2_min\": X, \"pm2_max\": X, \"pm2_retenu\": X, \"nb_annonces\": X}"}],
            "max_tokens": 300
        }).encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload, method="POST",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=45) as res:
            resp = json.load(res)
        return jsonify({"ok": True, "response": resp})
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500

@app.route("/debug-location", methods=["POST"])
def debug_location():
    """Debug endpoint — teste le web search location et retourne les données brutes."""
    import os, urllib.request, json, re
    data = request.get_json(silent=True) or {}
    api_key = os.environ.get("OPENAI_API_KEY", "")
    surf = float(str(data.get("surface", 41)))
    type_b = str(data.get("type_bien", "bureau"))
    ville = str(data.get("ville", "Saint-Ave"))
    smin = int(surf * 0.75)
    smax = int(surf * 1.25)
    prompt = (
        f"Recherche sur SeLoger, BienIci, Logic-immo des annonces actuelles de {type_b} "
        f"en location a {ville} (Morbihan, 56), surface entre {smin} et {smax} m2. "
        f"Donne le loyer annuel HT au m2 constate. "
        f"Format attendu: {{pm2_min: X, pm2_max: X, pm2_retenu: X, nb_annonces: X}}"
    )
    try:
        pl = json.dumps({
            "model": "gpt-4o-search-preview",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 300
        }).encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=pl, method="POST",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=40) as res:
            resp = json.load(res)
        raw = resp["choices"][0]["message"]["content"]
        m = re.search(r'\{[^{}]*\}', raw, re.DOTALL)
        parsed = json.loads(m.group()) if m else {}
        return jsonify({"ok": True, "raw": raw[:500], "parsed": parsed})
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()[:800]})

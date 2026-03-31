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

    # Bande teal full-width en haut
    c.saveState()
    c.setFillColor(TEAL)
    c.rect(0, PAGE_H - header_h - 6, PAGE_W, header_h + 6, fill=1, stroke=0)
    c.restoreState()

    # Logo sur fond teal
    logo_h = 30
    logo_w = 30 * (488/662)
    try:
        c.drawImage(_img_reader(LOGO_B64), ML, PAGE_H - header_h - 2, width=logo_w, height=logo_h,
                    mask='auto', preserveAspectRatio=True)
    except:
        pass

    # Titre et sous-titre à droite sur fond teal
    c.saveState()
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 18)
    c.drawRightString(PAGE_W - MR, PAGE_H - header_h + 12, "AVIS DE VALEUR PROFESSIONNEL")
    c.setFont("Helvetica", 8)
    c.setFillColor(colors.HexColor("#FFFFFFBB"))
    c.drawRightString(PAGE_W - MR, PAGE_H - header_h - 1, f"Réf. {d['reference']}  ·  {d['ville']}  ·  {d['negociateur']}")
    c.restoreState()

    y -= header_h + 10
    y -= 8

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
    for idx, (pt, ht, pb, hb) in enumerate(rendered):
        # Séparateur orange entre sections (pas avant la première)
        if idx > 0:
            c.saveState()
            c.setStrokeColor(ORANGE)
            c.setLineWidth(0.8)
            c.line(ML + 8, cy - 2, ML + CW - 8, cy - 2)
            c.restoreState()
            cy -= 6
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
    return jsonify({"service": "Barbier PDF Generator", "status": "ok", "version": "4.88"})


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

def _footer(c, n, total=3):
    c.setFillColor(_BLEU); c.rect(0, 0, _W, 9*_mm, fill=1, stroke=0)
    c.setFillColor(_BLANC); c.setFont("Helvetica", 6.5)
    c.drawString(14*_mm, 3.5*_mm, "Barbier Immobilier — 2 place Albert Einstein, 56000 Vannes — 02.97.47.11.11 — barbierimmobilier.com")
    c.drawRightString(_W-14*_mm, 3.5*_mm, f"{n} / {total}")

def _header(c, sub=""):
    c.setFillColor(_BLEU); c.rect(0, _H-11*_mm, _W, 11*_mm, fill=1, stroke=0)
    c.setFillColor(_BLANC); c.setFont("Helvetica-Bold", 8.5)
    c.drawString(14*_mm, _H-7.5*_mm, f"DOSSIER DE PRÉSENTATION  ›  {sub.upper()}")
    _logo_small(c)

def _sec(c, text, x, y, w=None):
    # Fond léger — largeur explicite ou pleine largeur par défaut
    _sw = w if w is not None else (_W - 28*_mm)
    c.setFillColor(_colors.HexColor("#EBF0F8"))
    c.rect(x, y+2.5*_mm, _sw, 8*_mm, fill=1, stroke=0)
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
    c.setFillColor(_colors.HexColor("#F0F4F8")); c.circle(cx, cy, r, fill=1, stroke=0)
    try:
        ico = _ir(picto_b64)
        s = r*1.2
        c.drawImage(ico, cx-s/2, cy-s/2, width=s, height=s, mask='auto')
    except:
        c.setFillColor(_BLEU); c.setFont("Helvetica-Bold", 8)
        c.drawCentredString(cx, cy-3*_mm, "•")
    c.setFillColor(_colors.HexColor("#777777")); c.setFont("Helvetica", 6.5)
    # Tronquer le label si trop long pour la pill
    _lbl_max = w - r*2 - 8*_mm
    _lbl_txt = label.upper()
    while _lbl_txt and c.stringWidth(_lbl_txt, "Helvetica", 6.5) > _lbl_max:
        _lbl_txt = _lbl_txt[:-1]
    if _lbl_txt != label.upper():
        _lbl_txt = _lbl_txt[:-1] + "…"
    c.drawString(x+r*2+5*_mm, y+h-4.5*_mm, _lbl_txt)
    c.setFillColor(_BLEU_F)
    # Auto-fit valeur + tronquer si nécessaire
    _val_str = str(value)
    for fsz in [9.5, 9, 8, 7]:
        c.setFont("Helvetica-Bold", fsz)
        if c.stringWidth(_val_str, "Helvetica-Bold", fsz) <= _lbl_max: break
    # Si même à 7pt trop long, tronquer
    while _val_str and c.stringWidth(_val_str, "Helvetica-Bold", 7) > _lbl_max:
        _val_str = _val_str[:-1]
    if _val_str != str(value):
        _val_str = _val_str[:-1] + "…"
    c.drawString(x+r*2+5*_mm, y+3.5*_mm, _val_str)

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


def _gpt_atouts(type_bien, ville, surface, adresse, activite):
    import os, re as _re2
    api_key = os.environ.get('OPENAI_API_KEY', '')
    if not api_key: return None
    t = type_bien or 'local'
    v = ville or 'Vannes'
    s = surface or '?'
    act_str = f' ({activite})' if activite else ''
    prompt = (
        f'Tu es expert en immobilier commercial dans le Morbihan. Pour ce bien : {t}{act_str}, {s} m\u00b2, {adresse or v}, {v}.\n'
        'G\u00e9n\u00e8re 4 atouts commerciaux percutants, adapt\u00e9s \u00e0 ce type de bien et cette localisation.\n'
        'Chaque atout : titre court (2-3 mots, MAJUSCULES) + texte 1-2 phrases concr\u00e8tes.\n'
        'Utilise des biais cognitifs : raret\u00e9, ancrage g\u00e9ographique, urgence.\n'
        'R\u00e9ponds UNIQUEMENT avec ce JSON (sans texte, sans backticks) :\n'
        '[{"titre": "TITRE", "texte": "Texte"}, {"titre": "TITRE", "texte": "Texte"}, {"titre": "TITRE", "texte": "Texte"}, {"titre": "TITRE", "texte": "Texte"}]'
    )
    try:
        payload = _json.dumps({'model': 'gpt-4o-mini', 'messages': [{'role': 'user', 'content': prompt}], 'max_tokens': 400, 'temperature': 0.7}).encode()
        req = _ur.Request('https://api.openai.com/v1/chat/completions', data=payload, method='POST',
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'})
        with _ur.urlopen(req, timeout=25) as res:
            raw = _json.load(res)['choices'][0]['message']['content'].strip()
        match = _re2.search(r'\[.*?\]', raw, _re2.DOTALL)
        if match:
            atouts = _json.loads(match.group(0))
            if len(atouts) >= 4: return atouts
    except Exception:
        pass
    return None

# ══════════════════════════════════════════════════════════════
# PAGES
# ══════════════════════════════════════════════════════════════
def _page1(c, d):
    c.setFillColor(_BLEU); c.rect(0, _H*0.50, _W, _H*0.50, fill=1, stroke=0)
    # ── Badge EXCLUSIVITÉ en tout premier, avant le titre ─────────────────────
    statut_mandat_p1 = str(d.get("statut_mandat") or "").lower()
    is_exclu_p1 = "exclusi" in statut_mandat_p1 or "exclusi" in str(d.get("type_mandat","")).lower()
    y_offset_exclu = 0  # décalage vertical si badge présent
    if is_exclu_p1:
        badge_txt = "EXCLUSIVITÉ"
        bh_b = 8*_mm
        bw_b = c.stringWidth(badge_txt, "Helvetica-Bold", 11) + 12*_mm
        c.setFillColor(_ORANGE)
        c.roundRect(14*_mm, _H-28*_mm, bw_b, bh_b, 2*_mm, fill=1, stroke=0)
        c.setFillColor(_BLANC); c.setFont("Helvetica-Bold", 11)
        c.drawCentredString(14*_mm + bw_b/2, _H-23.5*_mm, badge_txt)
        y_offset_exclu = 12*_mm  # espace badge + marge
    # Titre type de bien
    c.setFillColor(_BLANC); c.setFont("Helvetica-Bold", 30)
    c.drawString(14*_mm, _H-38*_mm - y_offset_exclu, _safe(d.get("type_bien"), "Bien immobilier"))
    # Trait orange sous le titre
    c.setFillColor(_ORANGE)
    c.rect(14*_mm, _H-41.5*_mm - y_offset_exclu, 40*_mm, 2*_mm, fill=1, stroke=0)
    c.setFont("Helvetica", 14)
    c.setFillColor(_BLANC)
    c.drawString(14*_mm, _H-50*_mm - y_offset_exclu, _safe(d.get("adresse")))
    c.drawString(14*_mm, _H-58*_mm - y_offset_exclu, f"{_safe(d.get('code_postal'))} {_safe(d.get('ville'))}")
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
        # Vente : prix Airtable = FAI. Estimation = Prix net vendeur
        mode_doc  = d.get("_mode", "commercial")
        prix_nv   = d.get("prix_net_vendeur") or 0
        if mode_doc == "commercial":
            val_affiche = prix
            label_prix  = "PRIX FAI (honoraires inclus)"
        else:
            if prix_nv:
                val_affiche = int(float(str(prix_nv)))
            else:
                taux_h2 = float(d.get("taux_hono") or 0.05)
                val_affiche = int(float(str(prix)) / (1 + taux_h2)) if prix else 0
            label_prix  = "PRIX NET VENDEUR"
        suffix_val   = ""
        show_pm2     = bool(val_affiche and surf)
    # (badge EXCLUSIVITÉ déplacé en tête de page avant le titre)

    # ── Grand prix en premier, label en dessous ────────────────────────────
    prix_str = _pfmt(val_affiche) if val_affiche else "—"
    c.setFillColor(_BLANC)
    if suffix_val:
        c.setFont("Helvetica-Bold", 28)
        c.drawString(14*_mm, _H-84*_mm, prix_str)
        c.setFont("Helvetica", 13); c.setFillColor(_colors.HexColor("#FFFFFFCC"))
        vw = c.stringWidth(prix_str, "Helvetica-Bold", 28)
        c.drawString(14*_mm + vw + 3*_mm, _H-84*_mm, suffix_val)
        c.setFillColor(_BLANC)
    else:
        c.setFont("Helvetica-Bold", 34)
        c.drawString(14*_mm, _H-84*_mm, prix_str)
    # Ligne pm2 juste sous le prix
    if show_pm2:
        c.setFont("Helvetica", 10); c.setFillColor(_colors.HexColor("#FFFFFFBB"))
        if is_location and surf:
            try:
                loyer_an = float(str(val_affiche).replace(" ","")) * 12
                pm2_an   = loyer_an / float(str(surf).replace(" ",""))
                c.drawString(14*_mm, _H-91*_mm, f"soit {int(pm2_an):,} € HT/m²/an".replace(",", " "))
            except Exception:
                c.drawString(14*_mm, _H-91*_mm, f"soit {_pm2(val_affiche, surf)}")
        else:
            c.drawString(14*_mm, _H-91*_mm, f"soit {_pm2(val_affiche, surf)}")
    # Label (PRIX FAI / PRIX NET VENDEUR / LOYER) sous le pm2
    c.setFillColor(_BLANC); c.setFont("Helvetica", 9)
    _label_y = _H-95*_mm if show_pm2 else _H-91*_mm
    c.drawString(14*_mm, _label_y, label_prix)
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
            iw = img0.getSize()[0]; ih = img0.getSize()[1]
            # Fill crop centré : couvrir toute la zone sans bande blanche
            target_ratio = pw0 / ph
            img_ratio    = iw / ih if ih > 0 else 1
            if img_ratio > target_ratio:
                # Image plus large → ajuster sur la hauteur, crop horizontal
                dh = ph;  dw = ph * img_ratio
                dx = px0 - (dw - pw0) / 2; dy = py0
            else:
                # Image plus haute → ajuster sur la largeur, crop vertical
                dw = pw0; dh = pw0 / img_ratio if img_ratio > 0 else ph
                dx = px0; dy = py0 - (dh - ph) / 2
            c.saveState()
            _clip_p1 = c.beginPath()
            _clip_p1.roundRect(px0, py0, pw0, ph, 3*_mm)
            c.clipPath(_clip_p1, stroke=0, fill=0)
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
    # Titre de section fixe
    _sec(c, "Présentation du bien", 14*_mm, _H-32*_mm)
    # ── Titre éditorialisé : type bien gras teal + adresse dessous ────────────
    _type_titre = _safe(d.get("type_bien"), "Bien immobilier").upper()
    _adr_titre  = _safe(d.get("adresse"), "")
    _vl_titre   = _safe(d.get("ville"), "")
    c.setFillColor(_BLEU_F); c.setFont("Helvetica-Bold", 13)
    c.drawString(14*_mm, _H-37*_mm, _type_titre)
    c.setFillColor(_GTEXTE); c.setFont("Helvetica", 9)
    _adr_line = f"{_adr_titre}, {_vl_titre}" if _adr_titre and _vl_titre else (_adr_titre or _vl_titre)
    c.drawString(14*_mm, _H-40.5*_mm, _adr_line)
    desc = _safe(d.get("description"), "Description non disponible.")
    import re as _re2

    def _render_desc_structured(c, desc_txt, start_y, max_h):
        """Parser hybride HTML + texte brut. Retourne le y du bas du rendu."""
        import re as _re_d
        _col_w = _W - 28*_mm
        _x     = 14*_mm
        _y     = start_y
        _GAP   = 2.5*_mm

        def _xs(t):
            return t.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
        def _sb(t):
            import re as _r2
            return _r2.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', t)
        def _strip(t):
            return _re_d.sub(r'<[^>]+>', '', t)

        def _draw_acc(txt):
            nonlocal _y
            _t = _sb(_xs(_strip(txt).strip()))
            if not _t: return
            _p = _Para(_t, _PS("_da0_acc", parent=None, fontName="Helvetica-Bold", fontSize=10,
                               textColor=_BLEU_F, leading=15, alignment=0))
            _, _ph = _p.wrap(_col_w, 9999)
            if _y - _ph < 18*_mm: return
            _p.drawOn(c, _x, _y - _ph); _y -= _ph + _GAP + 1*_mm

        def _draw_sec(txt):
            nonlocal _y
            _t = _xs(_strip(txt).strip())
            if not _t: return
            _p = _Para(_t, _PS("_das_sec", parent=None, fontName="Helvetica-Bold", fontSize=8.5,
                               textColor=_BLEU_F, leading=13, alignment=0))
            _, _ph = _p.wrap(_col_w - 6*_mm, 9999)
            _bh = _ph + 4*_mm
            if _y - _bh < 18*_mm: return
            c.setFillColor(_colors.HexColor("#EBF0F8"))
            c.roundRect(_x, _y - _bh, _col_w, _bh, 1*_mm, fill=1, stroke=0)
            c.setFillColor(_ORANGE); c.rect(_x, _y - _bh, 3*_mm, _bh, fill=1, stroke=0)
            _p.drawOn(c, _x + 6*_mm, _y - _bh + 2*_mm); _y -= _bh + _GAP

        def _draw_li(txt):
            nonlocal _y
            _t = _sb(_xs(_strip(txt).strip()))
            if not _t: return
            _p = _Para("\u2022 " + _t, _PS("_dab_li", parent=None, fontName="Helvetica", fontSize=8.5,
                                      textColor=_GTEXTE, leading=12, alignment=0,
                                      leftIndent=4*_mm, firstLineIndent=-4*_mm))
            _, _ph = _p.wrap(_col_w, 9999)
            if _y - _ph < 18*_mm: return
            _p.drawOn(c, _x, _y - _ph); _y -= _ph + 0.8*_mm

        def _draw_p(txt):
            nonlocal _y
            import re as _re_bold
            _raw = _strip(txt).strip()
            # Mettre en gras les montants €, pourcentages et données chiffrées
            _raw = _re_bold.sub(r'(\d[\d\s]*(?:€|%|m²|ans?)[^.,;]*)','**\1**', _raw)
            _t = _sb(_xs(_raw))
            if not _t: return
            _p = _Para(_t, _PS("_dap_body", parent=None, fontName="Helvetica", fontSize=9,
                               textColor=_GTEXTE, leading=13, alignment=4))
            _, _ph = _p.wrap(_col_w, 9999)
            if _y - _ph < 18*_mm: return
            _p.drawOn(c, _x, _y - _ph); _y -= _ph + _GAP

        # Nettoyage complet avant toute chose
        import html as _html_r
        desc_txt = _html_r.unescape(desc_txt)
        desc_txt = desc_txt.replace('\u00b2', '2').replace('\u00b3', '3')
        desc_txt = desc_txt.replace('\u2019', "'").replace('\u2018', "'")
        desc_txt = desc_txt.replace('\u2013', '-').replace('\u2014', '-')
        desc_txt = desc_txt.replace('\u2026', '...')
        clean = desc_txt.replace('\xa0', ' ').strip()
        is_html = bool(_re_d.search(r'<(p|h[1-6]|ul|li|strong|em|br)\b', clean, _re_d.I))

        if is_html:
            tokens = _re_d.split(
                r'(<h[1-6][^>]*>.*?</h[1-6]>|<p[^>]*>.*?</p>|<li[^>]*>.*?</li>)',
                clean, flags=_re_d.I | _re_d.DOTALL)
            first = True
            _bloc_count = 0
            _max_blocs = 5  # limite blocs HTML — évite annonce trop longue
            for tok in tokens:
                tok = tok.strip()
                if not tok: continue
                _bloc_count += 1
                if _bloc_count > _max_blocs: break
                mh = _re_d.match(r'<h[1-6][^>]*>(.*?)</h[1-6]>', tok, _re_d.I | _re_d.DOTALL)
                mp = _re_d.match(r'<p[^>]*>(.*?)</p>', tok, _re_d.I | _re_d.DOTALL)
                ml = _re_d.match(r'<li[^>]*>(.*?)</li>', tok, _re_d.I | _re_d.DOTALL)
                if mh:
                    if first: _draw_acc(mh.group(1)); first = False
                    else: _draw_sec(mh.group(1))
                elif ml:
                    _draw_li(ml.group(1)); first = False
                elif mp:
                    if first: _draw_acc(mp.group(1)); first = False
                    else: _draw_p(mp.group(1))
        else:
            blocs = [b.strip() for b in clean.split('\n\n') if b.strip()]
            _in_bullet_zone = False  # True après une section → items courts = bullets
            for idx, bloc in enumerate(blocs):
                if _y < 18*_mm: break
                if idx >= 4: break  # max 4 blocs pour éviter débordement
                lines = [l.strip() for l in bloc.splitlines() if l.strip()]
                if not lines: continue
                fl = lines[0]
                is_sec = (fl == fl.upper() and len(fl) > 4
                          and not any(cc.isdigit() for cc in fl[:2]) and len(lines) == 1)
                # Bullet : plusieurs lignes courtes, OU une ligne courte dans la bullet zone
                is_bul = (len(lines) > 1 and all(len(l) < 120 for l in lines)) or \
                         (_in_bullet_zone and len(lines) == 1 and len(fl) < 80)
                if idx == 0:
                    # 1er bloc = titre seul si court (<= 100 chars), sinon paragraphe normal
                    if len(fl) <= 100 and len(lines) == 1:
                        _draw_acc(fl)
                    else:
                        _draw_p(fl)
                        if len(lines) > 1: _draw_p(' '.join(lines[1:]))
                    _in_bullet_zone = False
                elif is_sec:
                    _draw_sec(fl)
                    _in_bullet_zone = True  # les blocs suivants sont des bullets
                elif is_bul:
                    for li in lines: _draw_li(li)
                    _y -= _GAP * 0.3
                else:
                    _draw_p(' '.join(lines))
                    _in_bullet_zone = False

        return _y

    text_y = _H-43*_mm
    # Hauteur max disponible avant les caractéristiques (estimation : 60mm)
    _desc_bot = _render_desc_structured(c, desc, text_y, 60*_mm)
    bot = _desc_bot - 8*_mm
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
    pb = sy-((len(pills)-1)//cols)*(ph2+pgy)-ph2-6*_mm

    # ── Bloc données financières (conditionnel) ──────────────────────────────
    # Champs bail locatif (nouveaux)
    _locataire    = d.get("locataire") or ""
    _loyer_ht     = d.get("loyer_annuel_ht") or 0
    _loyer_init   = d.get("loyer_initial_ht") or 0
    _evol_loyer   = d.get("evolution_loyer") or ""
    _duree_bail   = d.get("duree_bail") or ""
    _taxe         = d.get("taxe_fonciere") or d.get("taxe") or 0
    _ca           = d.get("ca_ht") or 0

    # Détecter si c'est un bien avec bail locatif
    _is_bail = bool(_locataire or _loyer_ht or _loyer_init or _evol_loyer or _duree_bail)

    if _is_bail:
        # ── Bloc bail locatif complet ──────────────────────────────────────────
        # Calculer hauteur selon nombre de champs renseignés
        _n_col1 = sum([bool(_locataire), bool(_loyer_ht)])
        _n_col2 = sum([bool(_loyer_init), bool(_evol_loyer), bool(_duree_bail), bool(_taxe)])
        _n_rows = max(_n_col1, _n_col2)
        # 11mm par ligne + 6mm padding haut + 4mm padding bas
        _bloc_bail_h = max(14*_mm, _n_rows * 11*_mm + 10*_mm)

        _fblock_top = pb - 4*_mm
        _sec(c, "Données du bail", 14*_mm, _fblock_top)
        # _fy = haut intérieur du bloc (sous le titre de section)
        _fy = _fblock_top - 7*_mm

        # Fond bloc
        c.setFillColor(_colors.HexColor("#EBF0F8"))
        c.roundRect(14*_mm, _fy - _bloc_bail_h, _W - 28*_mm, _bloc_bail_h, 2*_mm, fill=1, stroke=0)
        # Barre orange gauche
        c.setFillColor(_ORANGE)
        c.rect(14*_mm, _fy - _bloc_bail_h, 3*_mm, _bloc_bail_h, fill=1, stroke=0)

        # Colonne gauche : locataire + loyer annuel + loyer initial
        _fcx1 = 20*_mm
        _fcx2 = 14*_mm + (_W - 28*_mm) / 2 + 3*_mm
        _fcy  = _fy - 6*_mm

        def _bail_line(x, y, label, valeur):
            c.setFillColor(_BLEU_F); c.setFont("Helvetica-Bold", 7)
            c.drawString(x, y, label.upper())
            c.setFillColor(_GTEXTE); c.setFont("Helvetica", 8.5)
            c.drawString(x, y - 5*_mm, str(valeur))

        if _locataire:
            _bail_line(_fcx1, _fcy, "Locataire", _locataire)
            _fcy -= 11*_mm
        if _loyer_ht:
            try:
                _lht_fmt = f"{int(float(str(_loyer_ht))):,} EUR HT/an".replace(",", " ")
            except Exception:
                _lht_fmt = str(_loyer_ht)
            _bail_line(_fcx1, _fcy, "Loyer annuel HT", _lht_fmt)
            _fcy -= 11*_mm

        # Colonne droite : loyer initial + évolution + durée + taxe
        _fcy2 = _fy - 6*_mm
        if _loyer_init:
            try:
                _li_fmt = f"{int(float(str(_loyer_init))):,} EUR HT".replace(",", " ")
            except Exception:
                _li_fmt = str(_loyer_init)
            _bail_line(_fcx2, _fcy2, "Loyer initial", _li_fmt)
            _fcy2 -= 11*_mm
        if _evol_loyer:
            _bail_line(_fcx2, _fcy2, "Evolution du loyer", _evol_loyer)
            _fcy2 -= 11*_mm
        if _duree_bail:
            _bail_line(_fcx2, _fcy2, "Durée du bail", _duree_bail)
            _fcy2 -= 11*_mm

        # Taxe foncière en bas à droite si disponible
        if _taxe:
            try:
                _tf_fmt = f"{int(float(str(_taxe))):,} EUR/an".replace(",", " ")
            except Exception:
                _tf_fmt = str(_taxe)
            _bail_line(_fcx2, _fcy2, "Taxe foncière", _tf_fmt)

    elif _taxe or _ca:
        # ── Bloc financier simple (vente sans bail) ────────────────────────────
        _fin_items = []
        if _ca:
            try: _fin_items.append(("CA HT annuel", f"{int(float(str(_ca))):,} EUR".replace(",", " ")))
            except: pass
        if _taxe:
            try: _fin_items.append(("Taxe fonciere", f"{int(float(str(_taxe))):,} EUR".replace(",", " ")))
            except: pass
        if _fin_items:
            _fblock_top = pb - 4*_mm
            _sec(c, "Données financières", 14*_mm, _fblock_top)
            _fy = _fblock_top - 6*_mm
            _fw = (_W - 28*_mm) / len(_fin_items) - 2*_mm
            for _fi, (_flbl, _fval) in enumerate(_fin_items):
                _fx = 14*_mm + _fi * (_fw + 2*_mm)
                c.setFillColor(_colors.HexColor("#EBF0F8"))
                c.roundRect(_fx, _fy - 12*_mm, _fw, 12*_mm, 1.5*_mm, fill=1, stroke=0)
                c.setFillColor(_BLEU_F); c.setFont("Helvetica", 6.5)
                c.drawString(_fx + 3*_mm, _fy - 5*_mm, _flbl.upper())
                c.setFont("Helvetica-Bold", 9)
                c.drawString(_fx + 3*_mm, _fy - 10*_mm, _fval)

    # ── Bloc Prix — ancré sur le bas de page (au-dessus du footer) ───────────
    prix_brut = d.get("prix") or 0
    taux_h    = float(d.get("taux_hono") or 0.05)
    if not prix_brut:
        _pnv  = d.get("prix_net_vendeur") or 0
        _hnr  = d.get("honoraires") or 0
        if _pnv and _hnr:
            try:
                prix_brut = int(float(str(_pnv))) + int(float(str(_hnr)))
            except Exception:
                pass
    if prix_brut:
        try:
            prix_fai_v  = int(float(str(prix_brut)))
            prix_nv_v   = d.get("prix_net_vendeur") or 0
            hono_raw    = d.get("honoraires") or 0
            if prix_nv_v:
                prix_nv_v = int(float(str(prix_nv_v)))
                hono_v    = int(float(str(hono_raw))) if hono_raw else (prix_fai_v - prix_nv_v)
            else:
                hono_v    = int(prix_fai_v * taux_h)
                prix_nv_v = prix_fai_v - hono_v
            bloc_h = 22*_mm
            bw3 = (_W - 28*_mm) / 3 - 2*_mm
            hono_charge = d.get("honoraires_charge") or "Acquéreur"
            # Ancrage fixe : juste au-dessus du footer (18mm depuis le bas)
            bloc_y  = 18*_mm
            titre_y = bloc_y + bloc_h + 3*_mm
            _sec(c, "Prix", 14*_mm, titre_y)
            items_prix = [
                ("PRIX DE VENTE FAI", str(prix_fai_v) + " €", _BLEU_F),
                ("HONORAIRES (" + hono_charge[:10] + ")", str(hono_v) + " €", _ORANGE),
                ("PRIX NET VENDEUR", str(prix_nv_v) + " €", _colors.HexColor("#0D5570")),
            ]
            for ip, (lbl_p, val_p, col_p) in enumerate(items_prix):
                bx_p = 14*_mm + ip * (bw3 + 3*_mm)
                c.setFillColor(col_p)
                c.roundRect(bx_p, bloc_y, bw3, bloc_h, 2*_mm, fill=1, stroke=0)
                c.setFillColor(_BLANC); c.setFont("Helvetica", 6.5)
                c.drawString(bx_p + 3*_mm, bloc_y + bloc_h - 7*_mm, lbl_p)
                try:
                    v_int = int(float(str(val_p).replace(" €","")))
                    val_fmt = str(v_int) + " €"
                except Exception:
                    val_fmt = val_p
                c.setFont("Helvetica-Bold", 10)
                c.drawString(bx_p + 3*_mm, bloc_y + 6*_mm, val_fmt)
        except Exception:
            pass
    _footer(c, 3)

def _page_photos(c, d):
    """Page dédiée aux photos du bien — 2 grandes photos pleine largeur empilées."""
    _header(c, f"{_safe(d.get('type_bien'))} — {_safe(d.get('adresse'))}, {_safe(d.get('ville'))}")
    _sec(c, "Photos du bien", 14*_mm, _H-32*_mm)

    photos = d.get("photos") or []
    # Index 1+ : la photo principale (index 0) reste sur la page de couverture
    photos_p = photos[1:] if len(photos) > 1 else []

    # Zone utile entre le titre de section et le footer
    zone_top = _H - 42*_mm
    zone_bot = 12*_mm
    zone_h   = zone_top - zone_bot
    gap      = 5*_mm
    ph_each  = (zone_h - gap) / 2
    pw       = _W - 28*_mm

    def _draw_photo_fill(c, idx, px, py, pw, ph):
        img = _fetch_photo_image(photos_p[idx]) if idx < len(photos_p) else None
        if img:
            try:
                c.saveState()
                path_clip = c.beginPath()
                path_clip.roundRect(px, py, pw, ph, 3*_mm)
                c.clipPath(path_clip, stroke=0, fill=0)
                iw, ih = img.getSize()
                tr = pw / ph
                ir = iw / ih if ih > 0 else 1
                if ir > tr:
                    dh = ph; dw = ph * ir
                    dx = px - (dw - pw) / 2; dy = py
                else:
                    dw = pw; dh = pw / ir if ir > 0 else ph
                    dx = px; dy = py - (dh - ph) / 2
                c.drawImage(img, dx, dy, dw, dh, mask="auto")
                c.restoreState()
            except Exception:
                c.setFillColor(_GRIS); c.setStrokeColor(_colors.HexColor("#DDDDDD"))
                c.roundRect(px, py, pw, ph, 3*_mm, fill=1, stroke=1)
        else:
            c.setFillColor(_GRIS); c.setStrokeColor(_colors.HexColor("#DDDDDD"))
            c.roundRect(px, py, pw, ph, 3*_mm, fill=1, stroke=1)
            c.setFillColor(_colors.HexColor("#BBBBBB")); c.setFont("Helvetica", 9)
            c.drawCentredString(px + pw/2, py + ph/2, f"Photo {idx + 2}")

    # Photo 1 (index 1) — en haut
    _draw_photo_fill(c, 0, 14*_mm, zone_bot + ph_each + gap, pw, ph_each)
    # Photo 2 (index 2) — en bas
    _draw_photo_fill(c, 1, 14*_mm, zone_bot, pw, ph_each)

    _footer(c, 4)


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
PICTO_BANQUE_B64 = "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"
PICTO_DYNAMIQUE_B64 = "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"
PICTO_FORMATION_B64 = "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a9dMv7/11ltyn332UXMk3+LX0UU06Dw2TVMpGTvttJN84YUXZFsLp64YSJlfePAov0HN1RzH8cznDz74wLOh6tOnT965RQJbn1dB53F763AkElHPM01TmqYpd9llF/nMM89IKZtca4lEQh0/HXtv2VwV/QC6a9TX10PKpkWnoaEBUkp88skn2H333dXkq6ysbHXXRAtgOBwueGdFboN8SoVlWWrkuwFM05T77befXLp0KZtNe+DQBSa5pEhQptNptaDOnDkTBx54oJpHsVhMGoYhhRBq+OdWPktToUM30bY2aLdlWZY84IAD5AcffCClbFK+U6lUq4KeFYDyH7lcDplMRl3LVCqFH3/8Efvuu69nfujrXTQaVUJbn7e0xrZldWpt0Nqcz5Kgr620dkejUbnTTjvJ77//vsVcrKura3Fv9tRR9APojkECn8w7UkpceeWVanHza5vk42/LCrCuJlZ9cgZ1KdBNcv3118v6+nqlnbIloGcMffHxuwQcx8Ell1wi11tvvRZCN98CqseVdObw7/79n6k/Vl1dLX//+99LUrrzDVZme9ZwXReZTAZ33nmnjMfjHtdUa2up32K1Li6AfPEu+t+xWEw9pisd+pw955xzZCaTQSqVUrKCfvb0UfQD6I6JKWWzqfXll1+WG2ywgUcbpYlA5iLazegTM18MQJBAq4qKijbNWTRBDcPw7Lai0ah6f9rpAU3BgnPnzmXh34MGCUPXdT07qZkzZ2L48OGe+UmC2K985puLnREIGCQIVr8v9PtpxIgR8tNPP1UKK1kDepOPtacPupazZ8/GyJEj884ZfV2rqqrKGwPgH2TlKnS+kltKn5OhUEgdkz++i2Kyhg8fLl988UUpZdPGyh+M21NH0Q+gqwctqI7j4MYbb5TV1dVK+Le20Akh8u6wdE1yXRfXfK/XF/NYLOb5m45D116j0ai85ZZb2vS38iiP4feLS9mkENx///1KCdQXMP+8yTcPg8andFQx0Hd2fosYHYNlWfKuu+6SfoU1k8mwFaCHjIceekhWVVW1WM+i0aiaH5ZlqQ2WX/jTPFrXdbWtNdnvfshncaCfFRUV8tJLL5WJRELN0WKf264eRT+Ajg4KQNEXE/3C5XI51NTUYP/995eAd5ffWYuhPvn8OyR9IdbTXAp5f5q0usksHo/LI488UpJlg372Ju21nIY+P3Uz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"
PICTO_HOTELLERIE_B64 = "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lzMWEgkjtJdVDjzwQJx88sk4/vjj1S677AKl1KhYPiGEkKEkXaV1+fLl+MMf/qBvueUW3H///caCLdjVSDcml9PIBPDII4/EI4880mtS3KeJYUsuAUjxAnldq9Uwf/78hh0j6mVm+/KXv6y1Hj2hfptqdgKllStXYuLEiXXDIjfWJK2s/V4QBNpxHH3AAQfop59+2mikco/kdTq7IhsbGxtb/5ok6bHf6+zsxJIlS/CRj3yk15JtvWyAmxrfgZ4QwXvvvVfLGB6G4dAnArId/+I4hud5uP3226G1bshEr3WyQlU2m8Wpp56qgN61lkcjosX5vo8vf/nLet26dYmllL6QqAHXdSGhIpVKBVdeeSX+7//+T82cORMtLS0AuhML2U4pra2tW+CMCCGkeZAkPXaStWw2i0mTJuG2225Td999N8aPH49cLofW1lYopYyZvy/iODYWXgC4+OKLTeZY13WHxxKArEeIOeRd73qX/tOf/tRwuV+7NOURRxyB+fPnq9GQ5a9RqtUqli1bht12202Liagv71FBPE+jKILv+2htbcUf/vAHtffeeye+lzZVVatV5HK5wT0RQghpIkT2AT05AeyJb6VSQRAEWLduHY444gj95z//2XynEdkI9EQYRFEEz/Nw/fXX4/TTT1daD5NUwLYms2zZMrz22msANl4r3UbCJYBua8C8efManv2OFrTW+M1vfqPjOEa1Wu2Xg6MIf6UUpk+fjkWLFqm9994b69evBwBTG1wpZUIGXddFLperqxQSQghpjHqpgO1CdUEQIAxDjBs3DgsWLFDz5s2D7/uI4xhtbW0NbV9rjUwmYyZ7V155JYDuSeKwUADkIiil8Oijj+pqtWq0or7QWieiAo455hgF9E9DGulkMhnceuutAHoyJTaqANgmp9/+9rdqzJgxAICxY8eamgpAj5XFvqZUAAghZPMRczzQLbMkk6tkBAR6JmGFQgE33nijmjhxIoIgQHt7e8P7KZVKyGazqNVqeOWVV/DOO+90KwWDf0r9xzaB3HXXXQA2T7jssMMO2GWXXcwFbQYFQGuNN954A88995zR8CTBTyPLIBIictNNN2GfffZBpVIx60Ui9CVvghRb0lqbkExCCCGbh+/7xjwvWXBNpb6/T2KDIADQPVa3trbi+eefV7I00Be2L50khCuVSrj//vu1UmroFQA7E121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KmbdsNGW9rq2BYV47tvnrbXGL3/5Sz1nzhydzWbNoJXuA67rbnKQUEoZYZjP53sN9HvttZe+/fbbdRiGvdL6ViqVxPEMdevs7MRNN92kHcfRhUKh13ORVnAA6M997nObfDYGWwFI91URKI8++mhdobsl0t7Wa2lBXK/PiGISBIHeb7/9zHUrFosN92/5+9xzz8Her9wPaZ7n1VUOtlaznxullN5nn3306tWrzT2T89ia/b8/MrtOowIwkttIVwDs3OHVahUdHR2YOXNmYrBrbW01A548/J7n6QsuuMAc/6ZmxgNt9sPc3t6O2bNnJwYCx3G07/s6n8+bAWvMmDF63rx52haOtvBvVBEQRaaeMiPv2fnH165dC601Ojo68IMf/EDvvffeeuzYseae2wqKHLscfxAEvSwZIhzlt/vvv7/+yU9+oru6uhLHIv9HUbRV8/z31WSZRmuNU089NSFUlFLm/FzXNXnfDzjgAL1hw4atepzp+gLS59rb23HBBRdopZQRtLZCJtaaLdVsYev7fl3rl1zTiRMnGmtcowIwrUCGYYjrr7++roKanv1ns9ktfv6+7yeeCdknAD127Fh97733avt8ZDzbErUiNtaoADRxG+kKgAzOokWLALn44ou14ziJYx83bpwGoLfffnt92223mWMvFosJAbkxgbk5TQYy27RdLpdxxRVXaMdx6s7EdtppJ3311Veb40tbI2q1Wr+E5KZmm3KuaYXHHoCXLl2K6667Tr/vfe9LFDcRRWpjz70UBHrve9+rr7/+er106dK6ilU9YVmpVIaNImBf+5tuuklvs802xpIkAk7+fve739XSL7fGIG7vQ5TC9P2Oogj33HOPnjZtmrlfQ2EOTze775999tlalE+53v25/+vXr09cgz/+8Y/Yd999E8+/53n9KsI12OdqWyVPPfVUvXLlSmjdPf7YVr70/dzSbSAKgBItYFOojYRUaa2Hf6DjKOfggw/WTz31VK/37XtW7/4ppfDEE0/g4IMPHjb3sFQqmZjeUqmE9evX4/HHH9ePP/441q5di+222w5HHHEEDj/8cJXP5xPnJ0jebqAnTepAEWfESqViHPm07l6jfeyxx/QTTzyBtWvXYqeddsLs2bNx2GGHqfHjx5sBwI4USDtS9Qf7oVeW17jtLCme+ervYYYSKiafb9iwAYsXL8bixYv1mjVr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SBNCBYAQQghpQqgAEEIIIU0IFQBCCCGkCaECQAghhDQhVAAIIYSQJoQKACGEENKEUAEghBBCmhAqAIQQQkgTQgWAEEIIaUKoABBCCCFNCBUAQgghpAmhAkAIIYQ0IVQACCGEkCaECgAhhBDShFABIIQQQpoQKgCEEEJIE0IFgBBCCGlCqAAQQgghTQgVAEIIIaQJoQJACCGENCFUAAghhJAmhAoAIYQQ0oRQASCEEEKaECoAhBBCSBNCBYAQQghpQqgAEEIIIU0IFQBCCCGkCaECQAghhDQhVAAIIYSQJoQKACGEENKEUAEghBBCmhAqAIQQQkgTQgWAEEIIaUKoABBCCCFNCBUAQgghpAmhAkAIIYQ0IVQACCGEkCaECgAhhBDShFABIIQQQpoQKgCEEEJIE0IFgBBCCGlCqAAQQgghTQgVAEIIIaQJoQJACCGENCFUAAghhJAmhAoAIYQQ0oRQASCEEEKaECoAhBBCSBNCBYAQQghpQqgAEEIIIU0IFQBCCCGkCaECQAghhDQhVAAIIYSQJoQKACGEENKEUAEghBBCmhAqAIQQQkgTQgWAEEIIaUKoABBCCCFNCBUAQgghpAmhAkAIIYQ0IVQACCGEkCaECgAhhBDShFABIIQQQpoQKgCEEEJIE0IFgBBCCGlCqAAQQgghTQgVAEIIIaQJoQJACCGENCFUAAghhJAmhAoAIYQQ0oRQASCEEEKaECoAhBBCSBNCBYAQQghpQqgAEEIIIU0IFQBCCCGkCaECQAghhDQhVAAIIYSQJuT/A+nDiDozvYCvAAAAAElFTkSuQmCC"
PICTO_COMMERCE_B64 = "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"
PICTO_PARKING_B64 = "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pRJKpRLa2tqwevVqrFixAmvXrsWiRYuwbNkyLFu2rFNr5+52eGwoCSwkUAQ6lhd834fjOCiVSol+ArK0JIHBPvvsgwsvvBAzZsxQAJsIUf9hAEBDVm8GAEEQqO72blNtK5VKWLRoEebNm6ffeustvPTSS3jjjTfw5ptvYs2aNeZvaR8IJXv1y+Vyl1/b9SEAeny/6Y5c33VdfOtb38KPfvQjlcvlzI6BYrGITCZjHoecaNnTbgKiajAAoCGLAUB9swfKyjX1MAxRLpexYMECPPfcc/qRRx7B008/jXnz5iXeL+xZvLD3/sttiw0pLvR932wtTafTKJVKGDt2LH73u9/h0EMPVV29xqQ/ALME1BsYANCQxQCAAJiteUB76t/e0SHfl8+XLVuG+++/Xz/yyCO47777sGbNGjPA20WHjuMgk8kgn88DADKZjGkMJb+j2hMlc7kc8vm86SoIACeeeCKuvPJKlUqlTAZAOg6mUqleemao3jEAoCGLAUB9k+103VXT2xmCMAwRx7EZXIMggOM4+Pvf/65vvvlm3HXXXVi4cCGAZBfBhoYG5PN5aK2RzWbhuq4pFu2JFJba71G+75vU/w477IDf//73ao899uj0ONgpkHoDAwAashgAUCU55Ml13S778tukwY/jOIiiCI8//rj+3e9+hwcffBArVqxIFBJKCn9DDRs2DGvWrIHjOEin0ygUCp1OJ7z44otx3nnnKQlO1q5di+bm5o/5DBB1YABAQxYDgPomA3jl32p9A77ditdu/GRnB95//338/e9/17/97W/x1FNPoVgsmqJAoH05II5j83V35GekbsA+tEiWG6Qh0A477IC7775bbbHFFr3x1BABYABAQxgDAOqOrONHUWS2dlYeE2zXCMjf274sDEO88cYb+OlPf6pvuummTjsFNoS8T9m7EeygYuTIkWY76v7776+ksRHRxhjI8ZNtroioz9hvXF0FfTLrTqVSZl1frieFgvK5vZtALkulUth5551x4403qnfeeUedfvrppjK/2vV5ySro/zsJ0D7DQHoF+L6PlStXYvny5ZgxYwZ+9atfaQ7+NNgxA0B9ihkA6ktSRBjHsWk1vHjxYlx55ZX6l7/8JfL5fGIWL6R2oPJ7suwQhmFiO6FcX4KUXC6HI488Er/73e+ULBVIo6FCoYBsNsteAVQVLgHQkMUAgPpL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seWWW2KTTTZRn/jEJzrtChDlchnpdNrcpnwvCAJT5CfpfjmaV+6Dfb+IqGsMAIj6kAz8mUwGn/jEJ3D22Wdj2LBhia6Bg1kURUin08hkMkilUkilUmhublYjR45ES0sLGhoazHXtwdke6O1tkXKbdh9/mcnLlkDp4GefBChBgnQBBGDaNhNR1xgAEPUxx3FQLBYxduxYnHzyyQrovy6GtUQp1WVavnKmbgcH3e3Rl9vr6nqCgz/R+jFHRkREVIcYABAREdUhBgBERER1iAEAERFRHWIAQEREVIcYABAREdUhBgBERER1iAEAERFRHWIAQEREVIcYABAREdUhBgBERER1iAEAERFRHWIAQEREVIcYABAREdUhBgBERER1iAEAERFRHWIAQEREVIcYABAREdUhBgBERER1iAEAERFRHWIAQEREVIcYABAREdUhBgBERER1iAEAERFRHWIAQEREVIcYABAREdUhBgBERER1iAEAERFRHWIAQEREVIcYABAREdUhBgBERER1iAEAERFRHWIAQEREVIcYABAREdUhBgBERER1iAEAERFRHWIAQEREVIcYABAREdUhBgBERER1iAEAERFRHWIAQEREVIcYABAREdUhBgBERER1iAEAERFRHWIAQEREVIcYABAREdUhBgBERER1iAEAERFRHWIAQEREVIcYABAREdUhBgBERER1iAEAERFRHWIAQEREVIcYABAREdUhBgBERER1iAEAERFRHWIAQEREVIcYABAREdUhBgBERER1iAEAERFRHWIAQEREVIcYABAREdUhBgBERER1iAEAERFRHWIAQEREVIcYABAREdUhBgBERER1iAEAERFRHWIAQEREVIcYABAREdUhBgBERER1iAEAERFRHWIAQEREVIcYABAREdUhBgBERER1iAEAERFRHWIAQEREVIcYABAREdUhBgBERER1iAEAERFRHWIAQEREVIcYABAREdUhBgBERER1iAEAERFRHWIAQEREVIcYABAREdUhBgBERER1iAEAERFRHWIAQEREVIcYABAREdUhBgBERER1iAEAERFRHWIAQEREVIcYABAREdUhBgBERER1iAEAERFRHWIAQEREVIcYABAREdUhBgBERER1iAEAERFRHWIAQEREVIcYABAREdUhBgBERER1iAEAERFRHWIAQEREVIcYABAREdUh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Euvu+lEUYdmyZfjqV7+qx4wZY6I7ieg+TgagMqvg+363Ow2UUt1+jx/84Ac/+NHzh+M4630frfyeZHc35L3XHgvk80022UTPnDlTv//++2bcqazul4lh5RgkBekDPXYOiQxAb1i8eDF+/etf69/85jdYsWKFuVzaD0vQYPdotiNHWW+qjDKrbRdJRES9r7I6X97DK7Ow8l5tXyYz+TAMzc80Nzfj1FNPxdlnn6223nrr/n9A/WzIBwCyNVAphRUrVuDWW2/Vv/71r/HKK6+s9+c8z+u0zURUVqTKi67ySMqh/twSEfU1GcDt99dqGv10deped0XZkydPxplnnomjjjpKjRw5snfu+CAw5AMA+whIuwXj3//+d33dddfhpptuMlWc66vyl5+tHNgrI1AiIup7MhGzB/nKmX/l+OY4DjzPM8cEH3fccfjyl7+MvffeW2mtzcSvXo59H/IBANB+vkAqlTItIoH2P2w+n4fnebj33nv19ddfjyeeeAJr1qxJHB3Z3fMjxR6V15FsgLxwNqb1MBFRPZOCOjkzpvK9FkCn92jXdc2kzXVds9VP+sMcdNBBOPXUU/HZz35WSWt3O1OstTan+g11Qz4AsA8XApJLAkCyUjOfz+PFF1/U9913H+677z689tpr5nbsF0d3laWyE6DyWEkiItpwMj7JpKry/bdywiXH7NomTZqEww8/HIcffjj22GMPJeez5PN55HK5xO+y+7eUy+UhHwQM+QDAZm/hsNsLy/fsIkDZ1vH444/r1157DY899hhefPFFfPDBB2ZronzIedC27qJTIiKqTnfvo5LKt2u0lFIYO3Ys9tprLxx88MHYZZddsM8++yjf9xMDuywDAx3Hu8tAXy6XoZRKjA1D2ZAPAOTsZUnryDkC9vcrL7NrAaSQRAKD1tZWzJ8/H6+//rqeP38+3nvvPaxduxbLly/HihUrsHr1arS1tZlGENUcJ0lERJ2lUil4nodUKoWGhga0tLRg1KhRGD16NJqbm7HDDjtgyy23xE477aS23HJL5HK5xJY9OUlWZvd23wD7pD9hTwwre9AMRUM+AACSqR2g6z+spO3tF0l3twV0Tu1XFo3IGtRQfwEREfUVeQ+tbK3+cd5Xu6ofkMu11p0aDVUGB0NRXQQARERElMTpKRERUR1iAEBERFSHGAAQERHVIQYAREREdYgBABERUR1iAEBERFSHGAAQERHVIQYAREREdYgBABERUR1iAEBERFSHGAAQERHVIQYAREREdYgBABERUR1iAEBERFSHGAAQERHVof8PnGvoRs+45t4AAAAASUVORK5CYII="
PICTO_RESTAURATION_B64 = "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9JsXEP2ibHmaponMnjTnOeciBXY8HsekSZPw6quvtomdTk7nK2fcJB+kXr164YUXXgD5EAEQqbu9wJ2NlP5eccUVqKioEKaBbAzgrgi/kUpNod0ZAM7rc0TLCSIA78V8MpkMiouLxcTxc71M8Im7JW7Uqw2RnIBok5Vtd14gLxRZe5BOp3HRRRdxKjrjHh8/jAZJLgAwceJEPP744wyo5/B1XW9gu84HU0X9kc08crvfffddftxxx3GS5NLptMg97k6kRNfKhaPkPsipSelaUp8CdVqABx54AO+//z7r168fkskkRo4cKSIsgHoHPiL4XiDboekeKhNg/iAzVTJDO2/ePLHASHUvzy0v66+kpATdunUDUD+PwuGwKD9Nz8/2Lin5VCKRQCQSwX333cdef/117LPPPmKzZIyJdSevL5nJJumZ+mjbtkO97/7Ntm3U1tbilFNOwVNPPSU6SdfkWwMpMz2Aky5Q+d7JkyezK664Qpwr0xuCexzpPvTOaEOXfS5OPvlkTup/txZoV1lf7iJJ5G/kFU2ZNQtiBN2daQl3ky/P61wmFT3fjwRIHL5t24jFYoII2baNd955h7/77rsi5a38m1fpkq6hFJx77LEHXn311Zy1HF5Bz6dKa5xzsfCfe+45fsopp2Dr1q2IRCKCeBAD5WWSyyGSsrpQJrCJRALHHHMMZs+ezX71q18x0ghEIhH06tXL8c69FlGSQdkY822fU6iHXImRsH79+pzv27t376yqej/rQ86kd/zxx7O33nqLXX311WIupVIpwQiQhi1fkUKXXnop7r//fhE6R1EJbYVgMIiamhpomoYbbriB/eQnPxEbfyqVQjgcdoylHI3jZX1/9913uO2227isXcvVabwzwK+WubGxKggGQPYBaCnc3GZL79WSMEA34afsVV4gc8HkRBSPx7Fjxw5ce+21qKysFOcSkaHF4AWWZaGoqAiWZaFbt2745JNPGBU8agsvWmoz2UXpuU899RS/6qqrEIvFhEqfpGbZ6ac5kMpU1vqEw2HhqBQMBvHII4/gtddeY2PHjhX+DeFwGKlUCsOGDRP3onfh590bhoEffvgBgLNY0K4inbQ25Op+gFNNTOOeC4YOHdporQcv84DmGSXPsm0bffr0wS233MI++ugj9O/fH6WlpQ5NJyWzysccSafTuO666/Dggw/yeDzuqLGS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"
PICTO_TRANSPORT_B64 = "iVBORw0KGgoAAAANSUhEUgAAAgAAAAIACAYAAAD0eNT6AAABCGlDQ1BJQ0MgUHJvZmlsZQAAeJxjYGA8wQAELAYMDLl5JUVB7k4KEZFRCuwPGBiBEAwSk4sLGHADoKpv1yBqL+viUYcLcKakFicD6Q9ArFIEtBxopAiQLZIOYWuA2EkQtg2IXV5SUAJkB4DYRSFBzkB2CpCtkY7ETkJiJxcUgdT3ANk2uTmlyQh3M/Ck5oUGA2kOIJZhKGYIYnBncAL5H6IkfxEDg8VXBgbmCQixpJkMDNtbGRgkbiHEVBYwMPC3MDBsO48QQ4RJQWJRIliIBYiZ0tIYGD4tZ2DgjWRgEL7AwMAVDQsIHG5TALvNnSEfCNMZchhSgSKeDHkMyQx6QJYRgwGDIYMZAKbWPz9HbOBQAACkV0lEQVR4nOy9d5xcVf3//zq3TdmSQiopBAghhARIAgEioXfpSJVeBEHwowKKBNSfXwui4gMBEZEiIAIiIoRASIJIDwQSSgKJEGpCSN025bbz+2N9nz13djY7m73Zndl5Px+P89jZ2dk7987cc97v865CSgmm9/F9H0IImKYZed51XTiOo34PgqDda8IwRBiGMAwDQgj1vP64I/j7Z5jqhdYIWgeklOo5IUTk92IEQdBu3dGPV8oaxPQeggVA7+L7PizLijwnpVSDhL3v+zBNs92k7GiChmEIADAMYwtfAcMwfRlai4CO15MgCCClhGEYvOZUEKwA9DL6jp528rpC4LoubNuOCHkppXqtbdvqd/oJQP0Pf78Mw3QEbSCK7eBJoAOt6xQAtduntUa3RuqKAsHKQHnDCkCZQBo07fI7onDH35EFoDPTHcMwTEeUasLvaIPCa09lwApAGRAEAYQQSlsOwxC+78MwDFiWhUwmAwBIp9Pqf3TXge/7ShsnjbzUCczfP8NUL7orUV8z6HnP85RZX39NYRwS0GbB1I9T6N5kygtWAHqZQlMbEHULFLoIPM9DIpFQr123bh1Wr16Njz76SH7++edYs2YNNmzYgEwmA8/zsHHjxh69HoZhKgcy5+tCmzYShmGgX79+qK2txcCBAzF48GAMGzYMI0eOFCNHjkT//v0BtK5RtAHR1zG2BJQ/rJ71Mrq2Tbt+0zQRhiHy+Twsy4JpmmhoaMCbb74p33zzTSxduhRvvfUWli5dikwmA9/31fEMw1BxAAzDMJuiME6ILJHk63ddt9i/yWQyCcdxsMsuu2DcuHHYa6+9sPvuu4ttt90W9fX17PuvENgC0Mt0tNv/4osvsGzZMvn3v/8dr776KhYtWgTXdWFZlhL4xSav/pwQgpUBhmE2m0LffjF090AikcAuu+yCww47DHvuuSeOOuooNgGUMawAdBM9T59y8YE285f+HNAWTaub9envq1atwqxZs+T999+P1157DdlslgU4wzAVS319Pfbee2+ccsopOPzww8Xw4cMBRNe9QtcmxUDp9U+KBT/7vg/btnvwavoerADEgG6q9zwPnuchmUy284cB0aAbMvM/8MAD8o477sCbb76JfD7fK9fAMAyzJUgkEiqYcMaMGbj44otx2GGHiX79+iGXyyGZTKrX6r+TpZMCCaWUKmC6WBAi03VYAegmZLYvpsVSWl82m0UqlQLQGlVr2zbeeust/OY3v5GPPvoostlsxI8PtCoKyWQS2Wy2x6+JYRgmDnSXZX19PVpaWhAEAcaMGYNDDz0UV1xxhRg9ejRM04TruirTiSyrnufBsqwOi51xrEH3YAUgBvSUPNd14fu+upFJ4ANAS0sL/v3vf8vf/va3mD9/PpLJJHK5nDoORdGGYahcBQzDMJVKYfogQfFKYRjikEMOwXe/+10cfvjhAmizlhaa+DmrIH5YAegmhbX6Cb0UbxiGePjhh+UNN9yAhQsXtnttYT1u+j/DMNpZBhiGYSoN0zRh2zaCIIDneep53UIwefJkzJw5E8cee6wgE38ul4OUEpZlKWWAy5zHBysAMRCGodJo9Zr9APDQQw/J//f//h/efvtt9XrLspBIJNDS0gLTNCO7fdM01aRgKwDDMJWMHuhHFNvwOI6j6glMnz4dV1xxBY477rhNbveL9VFhugYrAN0kCALk83ll8m9paUFNTQ0WLVqEiy++WL766qtKqFPkql6vn7ThYil9lmVFtGWGYZhKQq9LUrjGOY6jYqX0roJBECCZTGLKlCn44x//KMaMGYPa2tp2ay3TfdiG0k2EEEin06pghud5uPDCC+XkyZPl22+/DcMw1I3rum5EE6bIWIIUBZooLPwZhqlkCpsN6Y8pXooi+3X/fi6Xw2uvvYbJkyfLX/7yl3Lt2rUwTTOSMcDrY/dhC0An6MV5iHw+j0QiEdFmpZR48skn5UUXXYSVK1cCiO7wGYZhmK5BLtIxY8bgrrvuwv777y/0wGqgbT0GovEB7CLoHLYAdILeXKelpQVAa15rc3Oz0lo3bNiAM888Ux5//PH44osvIIRATU0NC3+GYZhuQO6Azz//HAcccAC+/e1vS8qcopgBPcvAMAxVS4WDBDuHLQBdQNc8qWDF/Pnz5WmnnYb169dzxD7DMEzMUBwB/Rw3bhz+/Oc/Y5999lE+AyklstksLMuC4zgdZmcxUVhF6gQS6mEYwrZtpXEmk0nMnDlTHnbYYWhqaooUuwCAurq6XjtnhmGYvoBlWQjDEI7jqF3+smXLsP/+++O2226TQFucQTqdVuuw/nqmY9gCUALUhIc00KamJpx11lny6aefVuamYhH9hSl+DMMwTOlQlVVdTiUSCbXufv3rX8fNN98sqDUxvc7zPLYAlAArAF0gCAJ88MEHOP300yUV9NHzXElB4OA/hmGYeBBCIJVKIZPJRDZV9Hj33XfHv/71LzF48GBVQ8WyrKIB3EwUVgA6gQR7JpPBokWL5EknnYSVK1cimUzC9/2I318v7WsYBizL6qifNsMwDNNFqLFQGIZKyBuGgYEDB2L27Nlil1124Q6BXYBjADqB0koef/xxeeSRR2LlypVwHAe5XC7SrcowDORyOZWrGoYhC3+GYZhuQrn/yWQS+XweYRgilUrB931VRGjNmjU49NBD5fPPPy+L9R5gilP1FgAKIOmo37QQAo8//rg88cQTIaVU5v0tZebXm2TQ7+Rm0OMJ9OeptTDDMExX0NeQznqRFDb2oTWIduHFXk/rEgXzUdl027Zj3SDV1taiubkZtm3jb3/7G0444QShr+l6TYAgCBAEAccIgBUAAEAmk2nXhpJM/w8++KA89dRT1XPU6S+TycTy3oU+LRLmhmEglUqhpaWlqFJQyvdWrLywXoebv3uGYQgS6PqGghr4dLTB0IU8/W/hY32t0TdOcWyiKOWPHgshkM/nMXfuXBx00EGClA5yF5CyYlmWSuWuZqpeAaDdPAl3XSu844475KWXXqqUAv1nnBYAes9iGnHh++gae6FSYNs20uk0BgwYgAEDBqC2thYDBw5U2jk1KaIJzu01Gaa6oU2Hbgn1PA+ZTAa5XA5ffvklWlpasH79ejQ3NwOIWgd0BaCjrCfTNJFIJJDL5dp1SY0DvaMg0Lbxefzxx3HUUUcJAOpcC9fPaocVgP8pAM3NzarhhGmaePzxx+XJJ5+MXC6Hmpoa1bkvkUjEtvsvhMpZUoqLHvBimiZM01RKwtChQ7HjjjvigAMOwIgRIzB+/HiMHTtWDBo0CLZtK98Y0KbZF97w1f7dM0y1Q8JfShkRkIVmcs/z8MUXX+Cjjz6S7733HhYtWoTly5fjueeeUxsUfT2xbbtoT5Oamhq4rhubG1VXOsgNALTWYfF9H4888ggOO+wwoWdrBUHAgYL/o+oVAKCt3r/nebAsCy+++KI87LDDlGuABH6/fv3Q0NCgbuw4tNhirTF19NTCPfbYA0ceeST2339/TJgwQQwYMADZbBa2bXPNa4ZhukxnO2G9zn4h1Mjn9ddfl7Nnz8a8efPw9ttvq5LphL6G6m7JuOqkmKYJwzDgeR5SqZRaEz3Pw4ABA/D666+L0aNHIwgCJBIJuK6rNlTVTtUrAHquqJQSq1atwvjx42VTU5N6Tb9+/dDc3By5WelGixshhCp+EYYhdtppJ5x88sk488wzxfbbb6/Os9ikJe2WjlEY1Kib+mhU+/fPMNVMMatgEARqndA3FmQVoKwnfU0hYf7BBx9g7ty58oEHHsALL7yg1hjHcZBIJEDran19PZqammJZfyj9Wg/mllIqxWPo0KFYtGiRGDZsWOQ6aRNXzVS9AgAAzc3NSKVScF0X48ePl2vXrlUaa2HAn2EYcBynXQ2A7kKlKz3PQ01NDc477zxcfPHFYvz48eomJYUjlUoBaNXAi2mx7NtiGKYUKNOpmIsQgIraJ6Fa2N6XXkNKANHU1IQvvvgC119/vXz44YfR2NgYqeAXVwxAYZ8A27Zhmqaqx0KKydSpU/Hss8+KZDKpYr4Kr6MaqXoFgCJBgyDA4YcfLp999lm10yczkuM48DwPUspIwIl+Q28u+i584MCBOOuss/Dtb39bjBkzBrlcTrUd1jXVYtprsTRGPV2n2I1e7d89w1Q7tC7QmkeKAAUKU5Cg/trClrtkcaS/6XEEQgisWbMGv/3tb+XNN9+MTCajrKdxKQHFLJm0ZtN7SClx1lln4c477xS6xbfaFYCIGacvDzJrSSnR3NysHmezWUgpce2110oAMplMSsuyJABpmqYE0K0hhJBCiHbHTiaT6j1M05RXXXWVJJNYJpPp9c+LBw8ePOIYtJ6tXr0aF198sbQsS62JheshAOk4jlo7u7v+6sexLEv+/ve/l1K2yQMqJuS6rjpfcnXoMqOvjqqwAOj5nrqGSgEuzz77rDzwwAMjmmScaSp6DQGgzbJgGAZ1tRI77LADmpqaVBfBTQXfMAzDVAJ6W15ahxcvXozLL79c/uc//wHQZqbXc/rjQu8N4Ps+6urqMHfuXDFt2rRIJVdSBKotO6DqFAAK+pOy1fzzxRdfYPLkyXLt2rXqhjBNE6lUSqWUdBdSLCg1JggCDB8+HLfeeiuOPvpoEQQBfN+HbdtKOaE0GYZhmEqG3KeO46C5uRnpdBpCCNx1113y2muvxcqVK5ULgRQB3/djcw9QtUJi/PjxWLRokbAsC1JKFeiob7qqpZFQVYRAJpNJtbOnABESxJdccon84osvlC/IcRwEQYDm5ubYUuvovT3Pg+d5OPDAA7Fs2TJx0EEHCaD1Jk0kEhBCoKWlhYU/wzB9gqamJti2rawAqVRKxRmcd9554uWXXxZ77LGHqjgIQFUejKNUL5n0gda1PZVK4b333sP//d//SdM0YVmWqkVAwl//n75On1cA6IskLRSAEux33323fPTRR1FfX69M8roJKo4ofxLs9HjmzJmYM2eOME0T6XS6XQWrmpqaSGVChmGYSqWurk6lJ7uuG7FwAsDo0aOxYMECcdVVVwGAKmIGIFYhTO6FbDaL2tpa3HbbbZg9e7ak96G1VkqpsgmqgT7vAiBTP6V9UGTrZ599hhkzZsi1a9eiublZVfsj4qz3L4TAsGHD8Mgjj2DvvfcWehxC4eONGzeif//+MAyD4wAYhqloyP+u91shlyytzS0tLUgkEli4cKE84ogjsGHDBgCl9zzpDKqrQu9HZv8ddtgBL730kujfv786z8K6MH09S6DPWwDoCyQha5omMpkMbrjhBvnRRx8pUz8Jf9L8MplMLD4gIQS22247LFq0SEybNk3o56I/zufzkFKq2v2u67LwZximoqGmOyT8gyBAMplENptVPv6amhpYloWJEyeKl156SYwaNQo1NTWxCX+K5ielgwK8ly5diptuuknm8/l27l76n75O1VgA6Au1LAuvvfYapk2bJimPXy+CQTdoLpeLJRNg2rRp+Oc//ymGDx+u8mI7yt8n8xPdpNVepYphmMqGdtT5fF5169Mb8xRrwvbRRx/h+OOPl4sWLer2+xeWWtczDWj9X7ZsmRg7dqw6t2rqFNjnFQAAkUpRhmFgr732kq+++mq7LlKbg26m0t0Itm1j+vTpePrppwV14mOBzjAM0walCeoF2UzTxKpVq3D88cfLV199Vb1W7+Sn/+wuRxxxBJ588kll66eWwX3d/A9UgQuAvkwKQLn33nvl4sWLYZpmLEF2+k1IvqxEIoEddtgBs2fPFiT4uRIfwzBMFNr56+5WSpP+xz/+ISZNmqRcodQfRa/VEgcvvvgiZs2aJfVMsWrJBKgKCwDQWkffsixMmTJFvvPOO7G0oiTS6XSk1/XOO++MZ599VgwePLjD/6HPvRq0TIZhmGKQkNUD72hddhwHH3/8Mfbaay+5bt06eJ6Huro61VAoDgsu0LoGT5kyBS+//LKgY9q2XRVB2H3eAgC03lSpVAp/+9vf5DvvvKNunLg0yEwmo9wLo0aNwr/+9S8xcODATf4PN6JgGKba0V2jVJadrAJBEGCbbbbB/PnzRf/+/SGEgN6lNQ7hT10NFy9ejCeeeEIGQaA2h31d+ANVYgFwXReu62KPPfaQy5cvj9W0o5f1tW0bzz//vNh9992VFskwDMN0jB4UWIympiYsXrxYzpgxA0Drzp8CC7uLXqV18uTJePHFFwVlDsRVCK6c6fMWAKoo9dhjj8n33nsv4muKI82PFKgwDPGb3/wGe+yxB4BWxYBaUjIMwzDtofosJPx931f1Vyiguq6uDtOmTRMzZ85EOp2GlBL5fF61Re8OUkqk02l4nocFCxbglVdekZQJEJeLuJypCgtANpvFEUccIZ977jkAbb6juJpPJJNJTJ8+HfPmzVM2fb3AD8MwDNMx1JnP9/2IYCc/PKVLUwZXXDFclB1GKYFHHnkkZs2aJbLZbCwKRrlTFQrAc889Jw866CCVERBXlz+gdac/ZMgQvP/++wJoTQWk96F8/mJwECDDMEwbek0Uz/NgWZaq3koNfb788ktMnTpVrlq1ShX56Q6kSJimqay2b731lpg0aVJVNASq+C1qMQWGNEPa3d9yyy3qRiG//OYIXtM0I7X9TdOE53m466674DgOampq1LELC/4UwkGADMMwbejrIXVOpU2UlBKmaWLo0KH4xS9+ocr2AlFXrmVZat0txYfv+z4SiQSCIFAVC2+99VZZeNy+SsUrAIS+q9c7+3355ZeYP3++eo4q/3XF8uE4jrpJ9LKRlmXhzDPPxOTJk4Vt2/B9v2jDC4ZhGKbrUFqgEEKtp2effbbYe++9I6+jqn1dDb6meIJEIqHKxM+aNUv1I+jrVLwCUFjqEWgr5AAA//rXv+S6deuKaoOl+OipiBBFnFK3KopEveqqq8SgQYPUaymFhVv6MgzDdB/bttHc3AzbttWafeONN4pUKqWqB+ZyObVjp6ysUtIESS6QkgEAn376KebPn9/3fePoAwpAMeiLDMMQt912m3oMtGqK9Lir8Q/kh/J9H1JKXHXVVZg4cSKA9q0rq6GKFMMwzJbEMAx4nofa2loArYI6l8th6tSpOP/88yOZVkEQwHGcLrXz1WWF7/swTROO4+D222+P/2LKkIoPAtRbPOoBIwCwdOlSTJgwQdbX16OxsREANivyP5FIwPM8SClVoYpEIoFVq1aJVCql/Pn5fD6ipXImAMMwzOZDa66+o6f2wg0NDdhuu+0kWXwzmQwsy1IlgymyvzMKA8NJefjss8/EkCFDtsyFlQkVL51IgysWUPfUU09JAGhublavcV23ywUeXNdV+ap0s337299WLS5JidKFPxBfrWqGYZhqhIoEZbNZCCFgWRZc10U6ncbw4cPxjW98Ay0tLSo+QO8sWOpGr7C3AB1r9uzZlb07LoGKtwAAbcUk9M58vu9jv/32k4sWLUI2mwXQlv9PWiL9bynoKSf9+/fHihUrRP/+/QG07x6Vy+XgOA4rAAzDMN2kubkZtbW10Av02LaNTCaDNWvWYMKECTKTySCdTqsiQqWme1M2QWFb9nQ6jRkzZuCpp57q06lafUJC6R2iSAnwPA8vv/wystksTNNUvZ6pC2BX6gGQ7x9odQecd955KuUvDENlFSB/FFkC+oJyxTAM01uEYaj8/5ZlKTcrCeltttkGJ554olIIqH4/1RDoDJIJACIu3kwmg9dee23LXViZ0CcUAD33k3bjjz32mJK+pOHRY6BrjST0oJJ8Po9vf/vbwrZt9V5Aq5JAqSh0Q3GeP8MwzOZTaEUlAa+vrd/97ncFme1pndfTsTujUBaQ62D9+vWYO3euJMVAty4DfSPQu+IVAPpSCr+kF198Mbb3oEhUANh3331BbX6roVAEwzBMObP99ttj4sSJKmUbaEvX7i7PP/98O4FPikVfWP8rXgHQ0zgoSA8A5syZE5sPXr+Rzj777KqoEc0wDFMJ1NXV4eSTT24n8OMo+T5v3jzlQia3cV+K7eoTQYBANOVu9erVGDZsmIyz7r9t27AsCx9//LEYOHCgiiWohpaRDMMw5UoQBPj4448xYcIESRaAuORaXV0dVq5cKWprayNxZn1FEaj4KyhW1OfNN9+U+t+6g+7P32effTB48OB2piCGYRimd5BSYrvttsPEiRMjG7I4TPRNTU1YtmyZqi1QrPJsJVPxEkxvzEO88sorse3MSdi7rotjjz1WlQEGWAFgGIbpTYIgUKmBRxxxhCrYZhhGbOszbSj145EVoNKpeAlWLNJ+8eLFKj0vDigDYL/99hOkBfaFCFCGYZhKRs/qOvTQQyNCuiuZXh3hOA5ee+21SJ0XvT1xpVPxCgCZYshEE4YhPv3004i5prvYto2amhpss802ME0zkvvPMAzD9A60DqdSKYwfP17U1taqQm9xrM+u6+Ldd99VvxfWnKl0OlUAuvIh5nK5drmSxY7hum6kVa6UsugXVoqJhWrwk+aXz+fVFxaHBggAmUwGRx11FNLptOpLTe/NMAzD9A4UoQ+0Buztvffe8H0/1rX5ww8/RBAEqu6L3negMwplmpQyUpcGQKTBXDHCMIxYG6jaYRwKTqdX0NkHqUfCJ5NJSCnhui4cx1ElG+kYFFCxYMECuXjxYqxevRoff/wxMpkMmpubkc1m1YdMJpfOhLjv+0ilUli7di1qa2tRW1urKvIlk8lIt6jNgc597NixSvD3lQhQhmGYSkYvA59IJDB27Fg8/fTTkbLw3cFxHKxcuRIjR46UruuqSoG5XA6u66rCRB1BCgMNy7KQSqVQV1eHVCqFHXbYAWPHjsWee+6JKVOmiOHDh6t0wyAIlIXDNE14ngfLspBOp9HS0qKq0XaHLkfKFX6olmWhqakJdXV1AKLNGGzbxmeffYZ//etf8r777sPrr7+uLoLM9VSfvyM6E7S2bYO+mKamJjQ1NakuUKV0guoMut4pU6ZEntNrRzMMwzA9j26OF0Jg1113BVB6L4DOcF0XyWQSX3zxhXqOhHIymVRdZjuio3MwDAOmaWLRokXqNYlEQo4bNw6HH344Tj31VDFlyhTk83kkEgm4rhvZTMch/IEu1AHYlN+DdsS08weARx99VP7xj3/EM888AwBqRx8EgbpgvcEOfSAUXNFV830ymVSd/igXtDPloissXbpU7LjjjpE0EFYAGIZhyoMgCPDyyy/LGTNmRGRLHFAbeX3z2hWoPwzt7PX/pyJD+vn2798fW221FX784x/j1FNPFXrsWTabRRiGsSgBXVIAigk86sJE/POf/5Tf//738cEHH8A0zXYtGWlHTx9AZ5paZxYAMrEEQQDbtuF5HpLJJHzfb+dr2VySySTWrl0rEomEcnewG4BhGKZ88H0fX3zxBUaNGiXJItBdKwBtIsmdTBtVz/NKWv839f66m6KwvoBlWXAcB9lsFsOGDcPvfvc7nHzyyREBnMvlVP+ZzaUkCdaREKWOTADw7rvv4pBDDpHHH388Vq1ahSAIlMakf1CkPRmGgUQiUfQDMgwDtm2rv29qUGc/oK0WgOd5sQl/IQQGDx5cVNviLACGYZjeQ1+DLcvC0KFDUV9fH+kL0x30PgCpVAphGMLzPJUN1tmwbTsSqFh47pRiXni+vu8jk8lACIFVq1bhlFNOwcSJE+Vzzz2nitx1V/gD3UgDpA/C8zzcfPPNcs8995Rz585FMplEU1MT+vXrpy6EAiccx4kE0pGPnkwg5CagY5fiw9cb9eRyOdXfWQihPtzuYBiGav6jKzLsAmAYhikvbNvGwIEDAcQUJf+/DaznechmswBa5VWpheb0zSj1qiFXtxAikg1nWRYSiUSkfg0JetM08e677+KII47AVVddJePafG6WAiClhO/78H0fJ5xwgvze976HlpYWmKapou4bGhrUh0SZAa7rKuGsV2qiNMDCHEv6kDY1yJpAwtj3fWU50D/czSUIgsjun3f9DMMw5QGt+67rqt16XAFyQJsFgKL9KQAwn8+rnf2mBgl7oC0FkGIA9JRyeq98Pq9kJMnPXC6nKh5ms1nccMMNOOCAA+SHH37Y7evrVAGgHbzuT6ETnTp1qnziiSeUn78w6KKjADxd4HeEXhtgUwOIFmcAEEv0v86AAQMAIBKowf5/hmGY8sBxHBVPttVWW8V6bN1arcs42tlvahQG/BXSUaAibbKJwniGV199FVOnTpULFy6M/B/9T6mBip1KMTKB6Cbv999/H5MmTZKffPJJp29Q6cTlSmAYhmHiRd/4kdWY1uu+4KLt6Bpc10Umk8Ghhx4qn3rqKUnFgfQ4uFI2qV2yAHieh9WrV+P000+XVMCnr2NZViTYQv9C2B3AMAxTHtDanEwmI27hSmZTMsZ1Xaxfvx7HHXccli9frioUAq0ui1KK4HWqAJCfg3ztRxxxhHz77bcBoCp2xoUWgL5wUzEMw/QldEGZSCRiywIoF+h6ChUbss5/5StfkW+//TZqamraxS1sik4VAL0D0sUXXyzffPNNAEA6nY4lyK7coXKMQDTyn4I8GIZhmN6hWMlfy7L6nAIARJUAUgQowL6lpQVf/epXZXNzc0RGdUZJkWxBEOChhx6Sf/7znwG0Cv9MJlMVgXAdKQAMwzBM76NvxuJsA18OFG40C4PjgbaA9PXr1+PYY4+VlKlAaYubolMJHoYhVq9eje985zsqvYI6IcVRa7kS6Es3FMMwTF+FmskBfSNTizadhUoACX9SCGzbRiaTwfPPP4/rr79eCiGQSqU6PX6nn5BhGPjhD38ov/zyS7S0tMBxnFhr7FcCdCMV7v77momJYRim0tDXYTKRA31DAQCi1wQgUkMHgOq8C7QG7f/0pz/FsmXLSjp25BPK5/PtNIsXX3xR3nfffZEqfdRvuZSdsS409cdkTSgUqlQpSf+fTQ39QyAoaC+unTt9uIX1mtkdwDAM07uQL1xv1VtKK/lSKSy5SwV6EolEyfKpUK7pz+vXQT8Lq87q9QIKc/zpMX0OnufhggsukABUJoCuJOnZAYZ+AIoa1H0LN954Y7uCB0CrkC2l25KuvViWpT68lpYW9R7681QpSQiBZDLZaaEFOrbruurD1P1BDMMwDLO5kMCkdrwk92jDvKlBG1OSa0CbHNQ7FpqmCcdxlBAPw7AkEz71JKDjCiHgui7ee+89PPHEEzKZTCKfz6vzDoJAKTRhGMIoNGPrve6XLFmCRx55pJ35gf65VOhCPM+D7/vqYh3HUa1/SVsjTUlKWVIeo16TmT5s2rGziZ5hGIbZXHTLsud5Sj6m0+mS0uCpSq5pmkpAk7yjDraJRAJBEKhqg9Qzp5QgviAI1Madzg8A1qxZgxtvvFG9BmjvEgmCoFUB0GsV6y/805/+JAGonbj+t66YV1zXbWfSoN4AVLmJPsxCTakzE4vebphMP+SuKEWDYhiGYZhikHyhUsNEJpOB53mdyifdakBF9cgCQMX1aIcOQFkFSrGuE8Xc6ADw/PPP4+WXX5bUsZfc9qRomKYJQ/9nvcd9LpfDQw89BMuyIhX/9Dr7pXREsiwLnudF2hfS+9i2HekqCLS5BEhTKqUXgO66oG6ApaZBMAzDMExHUNB7GIawLAuO46hNa2fyiXblJNMAKAsAWRKomA8JbhL+pVgYTNOMVP8j+Ufvc8sttwCIWgfIem8YBiy9kAKdgO/7WLhwoVy5cmXkQ6Dgg65oKXoEPZn0yfSvFxLSfR+kqVAswKbQz0lvmEAfKrsBGIZhmM1Bz3hzHCdicS6Mxi8GySZdFlGHQL10LxBtDEQb587QG9SRW4HeQ0qJf/3rX2hoaEC/fv3g+75qOUzvZwHtC9zQPxZ+ELT7JyFdinCl4Dz6f8MwlDblOA4GDx6MqVOnYq+99sLQoUNVp0HTNJWGtSlIaaAP4E9/+hOWLFnCwp9hGIbpFiQX99prL1xwwQUqhs113ZLkE1mySd5ZloW1a9fijTfewOLFi/Hhhx8in88jkUhEdvOlZpiRG8K2baWckGLieR6amprw9NNPy5NPPlkUS4+06AndPGDbNv7zn/9ELoJ276QR6f/TGXo7xUQigWw2iyFDhuDKK6/Et771LUHpFIWKCCkanUG9kgHgn//8pyQtqFQlhWEYhmEKIVkyduxYnH/++cLzvIhpvjP5olcmJGu17jp/4YUX5I9+9CPMnz8fQNTdUIr80i3nVAKZ3OoUh/DEE0/guOOOi5w3KR3t7BeGYaCxsRELFiwoepF6v+FSKRTi22+/PV544QVxxRVXiGQy2WHtYgrm62zQB0p+FM7PZxiGYboLCWLawJKQJTqTTXqeP8W36XzlK18Rc+fOFddcc42yjlOKfSmbV3qNnlmg1/IRQuDJJ59UKYZkJaDMA5UGqOfOf/7557Hl0Ot5iqlUCltttRWee+45scMOO6haAAzDMAxTbZDc/d73vicuvfRSmKYJz/NK6uRXCkEQoKmpCZ9//nkk0BD4XyxCoaAPwxBLly6NzW6uazFBEOCf//ynGDFiBHzfV9UAGYZhGKbaIFP9gAED8OMf/1hss802SKfTKl4uDlzXxaJFi6RukVBp/cXM7suXL4/ljYGoD+Tss8/G1KlT0dDQUHX9BBiGYRhGhzbguVwOAwcOxBVXXKECAeOwwpMSUSjTtbo+bZX9qCjQqlWrYu2AR0F6V199tWhpaUG/fv0AcC19hmEYpnqhSoOGYSAMQ5x11lkCgCoV3F1oA75q1SoA0T42Usq2IEA9Ar+hoSHWTkqWZWHEiBEYM2YMampq4Ps+crkct9llGIZhqhY9YI+K402ePDlW67gQAhs2bAAQbRwEaM2AdIGfy+VibaRjWRbGjRunSh9SQwK9qALDMAzDVBOGYaiUPM/zYJomxowZAwBdKge8KYIgQEtLS0TOU3aDAbQvBER5iHFgmqbyb+TzedX8IJPJRBotMAzDMEy1oTfzoaJ5caG3CKbfgTZLgAW0lQ2kF8Tpm6dyvmEYIpFIqPehBgWVwO23347bb7+dKwoxDMMwsSOljDSvi6uInb6514v3kZIRn6OfYRiGYZiKgRUAhmEYhqlCWAFgGIZhmCqEFQCGYRiGqUJYAWAYhmGYKoQVAIZhGIapQlgBYBiGYZgqhBUAhmEYhqlCWAFgGIZhmCqEFQCGYRiGqUJYAWAYhmGYKoQVAIZhGIapQlgBYBiGYZgqhBUAhmEYhqlCWAFgGIZhmCqEFYASSSQS7Z6jPssM01tQX2/TNGHbduRvcdyfdIxi9z/D9DZCCJimGbnXDcNQf2M2DSsAnUALbD6fBwDYtg3HcWBZFt9gTK8TBIH6GYYhDMOA4zgQQkBK2e3j0/3vui4Avv+Z8kJKiSAIIKWEZVkwDIOV1i5g9fYJlDu+76vF1DRNeJ7X26fEMAq6L2khBNqEdRx4nsf3P1PWGIahlF/f95UFIJfL9fKZlT9sASgB0iillBBCQAiBZDKJVCrVy2fGMK33ZxiG6jGZRR3HieX4tKDy/c+UI3Sf64ovWa6YTcOfUonYth0xN5F2yWZQpjfRd/41NTVoaWkB0OoSoOfjeA++/5lyREqprFKGYSCVSqGlpQVhGMbmBuvLsALQCclkEgceeCC+9rWvIZ1OwzAMBEGgNEy+wZjexDRNWJaFXC4H13Xxu9/9Dm+99VZspvpEIoGDDjoIJ554ImpqapS5lQKvyPLAML2BZVkIwxCu66p5cMstt+CNN95gd1UJsALQCblcDttssw3OOeccQbsdz/NgmqYyjTJMb+G6rjKB+r6Pu+++W5LfPg4Bnc/nMXr0aJxzzjmC7nfP8zgIkCkLyCplGIZaj2fNmiXffPPNXj6zyoAlWIkIISKZAIZh8O6f6XUcx0EYhsoq5XkeDMNQO6O4MAxDmf1t22bhz5QFQgiljJLLK5fLwff9Xj6zyoAVgBKgG4vSSkjw8yLIlAumaQJoi1WJ0/xJ938ymQQANvszZQlZABzH4c1ZibACwDAMwzBVCCsADMMwDFOFsALAMAzDMFUIKwAMwzAMU4WwAsAwDMMwVQgrAAzDMAxThbACwDAMwzBVCCsADMMwDFOFsALAMAzDMFUIKwAMwzAMU4WwAsAwDMMwVQgrAAzDMAxThbACwDAMwzBVCCsADMMwDFOFsALAMAzDMFUIKwAMwzAMU4WwAsAwDMMwVQgrAAzDMAxThbACwDAMwzBVCCsADMMwDFOFsALAMAzDMFUIKwAMwzAMU4WwAsAwDMMwVQgrAAzDMAxThbACwDAMwzBVCCsADMMwDFOFsALAMAzDMFUIKwAMwzAMU4WwAsAwDMMwVQgrAAzDMAxThbACwDAMwzBVCCsADMMwDFOFsALAMAzDMFUIKwAMwzAMU4WwAsAwDMMwVQgrAAzDMAxThbACwDAMwzBVCCsADMMwDFOFsALAMAzDMFUIKwAMwzAMU4WwAsAwDMMwVQgrAAzDMAxThbACwDAMwzBVCCsADMMwDFOFsALAMAzDMFUIKwAMwzAMU4WwAsAwDMMwVQgrAAzDMAxThbACwDAMwzBVCCsADMMwDFOFsALAMAzDMFUIKwAMwzAMU4WwAsAwDMMwVQgrAAzDMAxThbACwDAMwzBVCCsADMMwDFOFsALAMAzDMFUIKwAMwzAMU4WwAsAwDMMwVQgrAAzDMAxThbACwDAMwzBVCCsADMMwDFOFsALAMAzDMFWIIaUEAAghtswbGAaklAjDUL0PPWYYhmGYakcIoWRwGIaQUsIwur8/J/lLxyZ5HwRB69+7/Q6dQG9sWZZ6c9M0t/TbMgzDMExFQLIRQDthHSekaNB7bHEFgC7CcRz4vr+l345hGIZhKgYppRrAllcA9J89pgDoFgCgzczBMAzDMEwb+i69O+gyVlcyCKvb71DiCYRhCNM0i5o6GIZhGKYaKdyVx0kxq4L+Pj0SAyCEQHNzc8T3H0eAA8MwDMNUOrpQ3hLWccMwIu9Bx+8xF8CaNWvUc7zzZxiGYaqdQkEvpVSxcnEpAYZhwLbtyHM9FgMAtF7Ixo0b1WOCUhEYhmEYptooFPJhGMaeJm8YRoeZdwZpG77vK61gwIABsZogLMvC0qVLsXz5cmX6932/qBsgDMPNUgxc11XuBvJzcJAhwzAM0x0Mw4jIpa5YsAsD333fjzxnGIY6LsXJLVy4MLZUedu24fs+hg8fHnle1QGgNyJhHIYh6uvr25kMugNd+P333y8BIJfLwbIsCCEQBAGCIGjzSWjaSilpg6QtOY6DIAgiSgW7GhiGYZjNhQQ0ySUhBHzfRz6fh+d5nf6/7/tKRhmGoeQe0KYcNDQ0qL+//PLLctWqVQiCIBYlgGTowIEDI89r8tZQb05PjhkzpttvrOM4DqSU+MMf/oA1a9bAcRy4rqsukj5Y0rL01MHO0JWEfD6vPlzbtrngEMMwDLPZkPB2XRdSSgRBAMuykEgkStok27bdztJN6Xgkq0g45/N5/OpXv4LjOEgkErG4yKWUsG0b22+/PQDN909yvzAtzzAMTJw4EZ7nxSJALctCLpeD4zj48ssvcfLJJ0vP8+A4Trvj61qWbi7Z1HAcR/1/KpVSWpnrulx4iGEYhukWpmmitra2nUW5paWlU/kUBAE8z4vIIl3mNjU1AWg1yc+dO1fOmTMH+Xwe+Xwe6XQ6lvMPggATJkwQQJsCoCz/dDL6Se24446RF3cH3/dhmqbSZp5//nkceeSR8osvvlAC3vd95HI55PN59X+6MrCpAbS6FOh86f3iOn+GYRimegmCAK7rAojKlHQ63al8ogj8Qmt2EATwfR91dXUAgE8//RSnnHIKMpmMsixkMplYzj+RSHRo1TeKlR0cPHgwtt1229ii9EkBME0TYRhi/vz52GuvveRdd90lm5qaYFkWkskkEokEwjCE53nKFdDZANr8NJ7nwTAMrjHAMAzDdBtyJauguf8FBFLQfGfySZevFA9AsQCmacL3fcyZM0dOmDBBCWDP85BMJmO7hkmTJik3PNAWACilhFWsS5BpmjjyyCNxyy23xHICrusilUohm82qD+Tjjz/GN77xDVx22WVy7Nix2HHHHTFgwICIiUL/4DcFaVqGYWDJkiXKDaBHWDIMwzBMV/A8D4lEAkCrpTmZTEbkSmdWZpKrZO0mS0A+n8e6devwk5/8RN5zzz0R6ze5zePi6KOPjvyuVx5sF2VHJ7zffvvhD3/4Q7dzEm3bhud5kUjIMAzhOI7ydbz33nt47733EARB60lZFoIgQBiGJZnx9S9EVxxY+DMMwzDdQUqJ119/Hddff71sampCIpFQaeydpZqHYagCASnIfc2aNVi0aBEWLVqk5GIikVBKAFmw9ee6w3777RfJKtBbBFtAq5Zj23ZEQzn66KPFgAED5Lp162BZFnzfRzqdRiaTUab8UvLsaTdOF0JCWb8wXVBLKSPpFaW8h/7/LPQZhmGYuHBdF5988gl+/OMfb7H30OUhxRuUIvz1ejdCCOVWSCaTyOVyGDNmDKZPny5IAdHr8Ni23VoJkIS/XoIwmUzi1FNPVS9OJpMqKEE/EMMwDMMwPQ8Jf2q05/s+HMdRLoSTTjpJ/c0wDCXjKdDQIBNEYcUiADj//POFaZowDCMSaS+lZCWAYRiGYXoRMuvr8tt1XSSTSdi2jbPPPlsU/p3kexiGbQqAECJS2EBKicmTJ+OYY45Rvng9kjCufsUMwzAMw3QdksEkxyl7IJfL4eijj8bOO+8cEf56OqKUEoZlWZFAPyml8vEDwLXXXisAqMAHACrinv3tDMMwDNM7+L4fEeqe56GmpgYAcM011wh6TWHRPQoKNABEFAA9mj4MQ0yePBmXX345crmcMvtToAHDMAzDML2H7tcPggAtLS04//zzMWXKFFW6GIjKeZWVB0BV3Css/yulhOu6+OEPfyhGjx4Nei0pCaXU6mcYhmEYJn50NzwJ9SFDhuDqq68WnudFXPV6YyOS3Qa9iA6gpxUYhgHHcTB06FD84Ac/iOT0O47DVgCGYRiG6SWklKpQURAEsG0b3/72tzF69OhIIyK9+h8AteE39MA/OpBeKYj++Zvf/KY466yzALTu/F3XjbQ2BFqtA1Q6kQMEGYZhGGbzITmaSCSK9rixLAv5fF41xdtnn31w5ZVXCtqsE/S/utVe1QEold/+9rdi1113he/7qK2tVeUNHcdR5gUalHfIMAzDMEzXkVIinU4jn8+3Be5pJnyywnueh1GjRuHPf/6zoE19Ke2KS5LQYRjCdV3U19fjiSeeEDvssAOam5tRX18P0zThum4kt9CyLKRSqZKq+DEMwzAM0x4hBDKZDAzDQF1dndpgUyli0zRRU1MDwzDw8MMPY9ttt1VKQTab7fT4JSsAZEIYOXIkHn30UbHNNtugsbFRCX7btlFfX6/SBbPZLCsADMMwDLOZSCmRSqUQhiGampoAtJX8pYD8lpYW/O1vf8O0adMEueYBIJVKdXr8ThWAfD4f8T8AwNixY/HUU0+JHXfcUZUN9jwPjY2NyOfzqmMSwzAMwzCbj17kh8r2m6YJz/PQr18/3H///TjqqKMEFevTS/p3RqdSmrSJXC6nmhMkEgmMHz8es2bNEhMmTFBlgqkAAbVNZBiGYRhm8xBCIJ/Pw7Zt5HI5eJ6H+vp6uK6Lfv364ZZbbsHpp58uksmkMvlT7X89CLAjOlUASItwHEdlCRDbb789XnnlFXHGGWfAMIxI96I42hgyDMMwTLVC0f16NkBjYyNGjx6N1157TXz9618X+XxeuQpaWlpUKj/976YoyQKQy+UibQR100JNTQ3uvfdecdNNN6Gurk6dJOUkMgzDMAzTdfL5vAq0TyQSyOfzOO2007Bs2TIxevRo+L6v5C3QKo/z+TwMw4jHBQAgYs4vzP2nToGXXnqpeOutt8TZZ5+tToZMEBS0oEMViiitoVjdgFJqCZCLQn+PwvdiGIZhmDjoSgXcjmLhqJ+ODvnwCZJjZHkfMWIEHnjgAfz1r38ViUQClmWpc9HPqbCezybPr+Qr6YAgCJBMJiGlxIgRI3D33XeLJ554AgcffLASylLKSA4j0OpaoOfDMISUMhLdSMWE6IPqaOgBD9yciGEYhokLXUiT/NIr4HYmn6geTuFGNwxDlV2nt/R1XVcdmwR5TU0NfvWrX2HJkiXi1FNPFRQUGMdGt9sKgGmaaG5uBtAWsHDYYYeJp556SvznP//BiSeeiGHDhgFoUxYIKiBUWM+Ych3pQ9rUoPdNpVKRkoimabaLWWAYhmGYrqBXxKVy+TQ6k09SSiXTdJM8bXbpmCQHadPbr18/DB8+HLfddhs+++wzceWVVwqKqzMMAxs3bozl2rrdzScIAiQSCfUhURdB0zSx1157ib322gtffvklnnvuOfnggw/iueeeQyaTAQDVfKgwbUE3j3S2q3ccB/l8vmjRAw5EZBiGYTYXEs4AIrKKKt92ZmZ3HEcpAbrlgJ6jx1JK1NXVYeLEiTj44INx7LHHiqlTp0JKiXw+D9/3UV9fr17fr1+/WK6v2wqAEAK2baOhoUFVKwJaqxBRIYKhQ4fi1FNPFaeddhry+TyWLl2KN954Q65YsQLLli1DNptFc3MzstmsajZEmlFngYS6KaW2thaLFy9GU1OTypfUWyAyDMMwTKkYhoEwDJFOp7HrrruCIu6TySSCIOg01U7f3ZMrIJVKoaamBslkEttuuy3Gjh2LSZMmYfz48WLgwIEAWjfHVIOHrOa+70fcBrpysrl0WwGgEyCNJJ/Pq1LAnuep5kBSSmQyGTiOg9122w277baboAsE2nb6XfVrUOwAAKxfvx5nnnmmfPLJJ1VtAoZhGIbZHMIwRDKZxPHHH48777xT0I6e5FVXAgKB9nKO5Fehm4A2vrp8K3yvOIrtdVsBaGlpUQWAPM+DZVnq4ugifN+HZVlIp9MAWq0DhmFEfPSFgp/MIp2ZWMgnYxgGSHsihaMwYINhGIZhSsU0TeRyOWzcuBGO47S20NWa8XSWaqfLJzpeMfQYAADKvUC/e54H3/eRTCZVnr/neSXl+m+KbisAJPyDIFAmDqBV6JOZgj4s+jDINUCv6SgFsJQ0BnoPsjZkMhnOBmAYhmG6DQli3VJNG1vXdTsVwB0JfNrgUllfclfrLX/12ju2bcO27YhrvLvCH4hBASAKL5SEvv58ocmiq+aTjtBTLmzbVhoSNyNiGIZhuouek0+W6e4I4MINbmGtHF1W6nJSTxuMA+7YwzAMwzBVCCsADMMwDFOFsALAMAzDMFUIKwAMwzAMU4WwAsAwDMMwVQgrAAzDMAxThbACwDAMwzBVCCsADMMwDFOFsALAMAzDMFUIKwAMwzAMU4WwAsAwDMMwVQgrAAzDMAxThbACwDAMwzBVCCsADMMwDFOFsALAMAzDMFUIKwAMwzAMU4WwAsAwDMMwVQgrAAzDMAxThbACwDAMwzBVCCsADMMwDFOFsALAMAzDMFUIKwAMwzAMU4WwAsAwDMMwVQgrAAzDMAxThbACwDAMwzBVCCsADMMwDFOFsALAMAzDMFUIKwAMwzAMU4WwAsAwDMMwVQgrAAzDMAxThbACwDAMwzBVCCsADMMwDFOFsALAMAzDMFUIKwAMwzAMU4WwAsAwDMMwVQgrAAzDMAxThbACwDAMwzBVCCsADMMwDFOFsALAMAzDMFUIKwAMwzAMU4WwAsAwDMMwVQgrAAzDMAxThbACwDAMwzBVCCsADMMwDFOFsALAMAzDMFUIKwAMwzAMU4WwAsAwDMMwVQgrACVgGAby+bz6XQgBAPB9v7dOiWEAAFJKCCHU/WmaJqSUMIz4prZhGMjlcpHfASAIgtjeg2E2B1qDhRBqXU4kEpBSwjTN3jy1isDq7RMod1KpFGzbhuM4AIAwDNXNZVn88TG9i77oBUGgfg/DMJbj0/2fSCTUcen+5wWW6W30Ndj3fYRhCNd1AbQqx8ymYQtAJ2SzWWzYsEH9bhiG2mUxTG+jL3b5fF7tzh3HgW3b3T5+NptFQ0OD+l2//3kOML2N7/toaWkB0KoMOI6jrAFxKcF9Gd7CdkIymURNTY0ys1qWpXY+vAAyvY3jOAjDEIZhIJlMwvM8AG2KQXdJJpNIp9NF73+A5wDTu1iWpawAdC/SHDBNk91UncAKQCfkcjl1YzmOox6TqZVhehvf9+E4DgzDgOd5EELEJphzuZzaSfH9z5QbZPY3TRNhGMK2bViWpWJjmE3DCkAJ2LYN13UjcQBA683HcQBMb6IH5JElwLKsWBUB27aRz+dVHAAd0/d9jgNgehW634HWHb/rukin0zAMg4O0S4ClVwk8++yz+Na3viVt20Ymk4Fpmsq/yiYmprcRQijf/NKlS5UJlBTX7kL3v+M46v4nZZgXWaY3oYBU2pQFQYB///vf7P8vEVYAOsG2bSxduhRLliyBYRjK3ASw8Gd6H9M0IYRQgtgwDHWfxiH8bdvGe++9h6VLl/L9z5Q1pAhIKZFMJpHP5zlGpRNYAegE2k0BiGiZDFMOFN6Lce98+P5nKgX9vtTrVjAdw2mADMMwDFOFsALAMAzDMFUIKwAMwzAMU4WwAsAwDMMwVQgrAAzDMAxThbACwDAMwzBVCCsADMMwDFOFsALAMAzDMFUIKwAMwzAMU4WwAsAwDMMwVQgrAAzDMAxThbACwDAMwzBVCCsADMMwDFOFsALAMAzDMFUIKwAMwzAMU4WwAlAChmFEfgKAbdu9dToMwzDM/xBCFP2d1+jOYQWgE0zTRBiGvX0aDMMwTAGmaUIIAcuylOCXUgIAPM/rzVOrCKzePoFyp1D4W5aFMAzVzVWofTIMwzA9QxAEAFrXYdM0IaVEEASwbZsVgBJgBaAThBAYMWIEdt55Z/i+D9d1YVkWEokELMtCNpvt7VNkGIapSlKpFHK5HIIgUDv/N954A42NjXAcB67r9vIZljesAHRCGIY48sgj8Zvf/EbU1tZGnjcMQ910DMMwTM8SBAEsq02M+b6PCy64QN57770s/EuAFYASSCaTIOHvui4cx1GmJtM0e/nsGIZhqhNaf3O5HGzbhmVZaGxsRBiG7AYoAVYASqClpQX5fB5A2w1HwYFsAWAYhukdKAaLNmVA2xrN8VmdwwpAJ1BwSSKRANBqYqKfuumJYRiG6Vk8z4Nt2zAMA77vKyUgkUioTRvTMZwG2AmFO3zLsiClZOHPMAzTy1Cuv5RSpQQKIdj/XyKsADAMwzBMFcIKAMMwDMNUIawAMAzDMEwVwgoAwzAMw1QhrAAwDMMwTBXCCgDDMAzDVCGsADAMwzBMFcIKAMMwDMNUIawAMAzDMEwVwgoAwzAMw1QhrAAwDMMwTBXCCgDDMAzDVCGsADAMwzBMFcIKAMMwDMNUIawAMAzDMEwVwgoA0w4pJaSURZ8PwzDytyAI2r2m8PXF/gYAnuep/y88Lv2v53nqOdd1I6/xfb+rl8YwsROGYdF7UUqJIAg6nC90zxfOiyAI1LwpNg/140sp4ft+u3lI6OfF84UphBWAKsd1XfXY932EYQghBIQQSvh6nod8Pg8pJQzDgBBCLT6maQKIKg1hGKrFzTCMds8Rtm1DCAEAMAwDrusil8shCAIIIWAYBmzbVq93HEcpBVJKWJa1ZT8chukE3/dhGEbkXiTlNQgCmKaJIAiUIkDzAWi956WUag4UPk/zj+ZW4aD/syxLvQ/NU8KyLHU+dI6u60YUa6Z64RW0yiGhqi8QtLiQ8NWFMP2tcNHSFzf9b6RQ6Auf7/swTVO9jnY7iURCvYYWKMMw4Pu+WuRoAFALLMOUA0EQKAVZv+d15UBXDMIwVPcv7eKL3eeF+L4PIYT6ezabhWVZsG1bHdfzPDiOo5QFff45jgMARZUPprpgBaDKCcMQhmHAMAy1s6YFwvM8JfxJSNNC0tDQgM8//xxLliyRn332GVasWIFVq1Zh/fr1aG5uhhACiUQCQ4YMge/76NevH3beeWdMnjwZY8eOFUOGDEEqlYosTLrp0zAMtcDpC6F+Trx4Mb0NCXe6d3WLmBACrutGhDnd3yTAwzCE67rYsGEDPvjgA/nmm2/i3XffxZo1axCGITZu3IhcLgcpJWprazFgwAAMHToU2267LUaOHInJkyeLkSNHwrZtSCmRzWaRSCTUHC6cI7olIgiCiHLPVB+sAFQ5ZMI0TbPdYmDbNrLZLFKpFMIwxNtvv41Zs2bJp59+GkuWLEFDQ0PE92jbNizLguu6kedpdxIEAbkc5ODBg7HNNtvguOOOw7777ovdd99dpFKpdrsez/MiyoBuEdCVB4bpLUig6sLf8zyYphl5Xlde33vvPcydO1c+9thjWLZsGT755BMAUMo4KRDktzdNE47jwPf9QvO9TKfTGD9+PA488EAcc8wxmDZtmjBNE67rwnEcpYTorgLdwsdUL6wAVDm0UyB831e+fcMwsHz5ctx///3yvvvuw8qVKwG0LlK62T6fzwNoXeD0xYnM/L7vtwtAIkvB4sWL4XkettpqK3n88cfj/PPPx5577ikoBoEWKXpsmiaklGpxY5jehkz3QJu/Xr83yWS/du1a3HffffKWW27Bxx9/jEQiASEEcrkcgDZ3HM0VciOQ2yCbzapjCiGQTCbheR4ymQzefPNNvPPOO/j1r38Ny7LkCSecgLPOOgsHH3ywINcazRmaQ7lcDslksqc+JqYMYQWAQRiGyOVysG1b7dTvvPNOecstt+Cdd95RCxIFO1HgoG3bSvjT34G2RVC3ApAyQPEGtKCRiXLdunW46667cM8992DMmDHywgsvxGWXXSbofWzbVgtWsRgEhukt9KC7fD6vhGomk0EqlcITTzwh77jjDsybNw+5XE7t5vW5A7QKaBL6dH/THKLn9SBAUgho3tG89H0fDz/8MB599FH069dPXn755TjjjDPE6NGj1d+llCz8Gc4CYFoXkHQ6Ddd1cccdd8hdd91VXnjhhXj77beVuZ12J7RImaapdvskkAuj//UIf9rZUFAgWRj0TIIgCOB5HpYvX46ZM2dixIgR8oYbbpArVqwAEA1G5OA/plzQU13JEpDJZPDaa6/JPfbYQ5599tmYNWsWcrkcLMtSrjAS3OQ6A9qUCV2BpqwbijMoDKqleSeEgGVZ6lie52Ht2rW47rrrMGHCBHnhhRfK999/X2UGMAwrAH0cWkQKc+wL84Yffvhhuffee8sLL7wQS5cuRTKZVK/xfT+SLlj4/8VymfWUv2I5/nrAX7EcZtd1sX79elx77bXYa6+95C233CL1iGkKYtJ3PXQedFzdZMowm4uey6+7ssiSpZvYLcvCwoULsf/++8uDDjoICxcuRGNjo/qfwv8HWgV14fObqp9R+Hf9deRuo//RgwHvuusujB8/Xs6cOVMWuhUoVbHYeTJ9F1YA+jgUaUy7cX0HDwArV67EBRdcIE8++WS8/fbbSKfTyiVQDkF2YRjiyy+/xOWXX479999fPvfcc5LcEGRKpZoAevpVGIZIpVK9ffpMhUOR/c3NzQBad/hUq4IsWIRhGJg5c6bcfffd5WuvvdZbpxzBdV0kEgkl6Gtra/Gzn/0MkyZNkv/4xz9kKpVCQ0ODCg4kqx5ZKpi+Te+v8MwWpampSQlyMucTf/rTn+TOO+8s//znP8MwDNTU1CCTyQBAWQh/AKipqQHQupN57rnncOCBB+KnP/2ppB0KRUXrO5ZsNqvSGhmmO9A80NP4KA6Fdvye52H+/Ply1KhR8mc/+xmSyaRSTMsF2gBks1mYpon//ve/OPnkk/H1r39dnaTjOJF5z3E2fZ/yWOWZLUZdXR0AYM2aNSp1rqGhAVdccYW87LLLsHHjRgCtC1tLSwuAVqFbU1NT1MzY01CgVC6XQyqVghAC1113HU499VS5YsUKFZ9gWZYyf9LOv5wWYKZyyWazSKfTANqqZRqGoVLsHnjgAXn44YejoaEBQOu96nleWcwfwzCQz+dhGAZSqVTE1O84Dv76179i7733lm+99RYAKEsAKdFM34a/4T4OTfbBgwfD8zx8+eWX2H///eVvfvMb5PN5FWEPtE7+RCKBlpYWNDU1lUWgHZn3TdOMuCUef/xxHHjggXLBggXI5/OqGpplWWoh5jRBprsEQaAUShKkVIti/fr1OOecc+TZZ58Nz/NUPAq5pspBgIZhiEQiEfH3UwEuOt+lS5di+vTp8pFHHpFUdyOVSrXLUmD6Hr1/hzJbFBLimUwG//3vf7HXXnvJ9957DwBUvX89Cp8WOaA8BGgQBKpMKhVYSafTEELgk08+wSGHHCJff/11mUql0NTUBADo168f1ztnYkH3jScSCViWhWw2i1wuh5NPPlk+/PDDAFrnEinblmUhn8+XRa8KOhchBAYMGKCKe5F1gpT/lpYWfO1rX8PNN98syfSvl+Zm+iasAFQB2WwWX3zxBfbbbz+5YsUKVXiE8oBJ0zdNE8lkMlJjvLchZSSRSESUGYr2b2xsxIwZM/Doo4/Kuro66LEBXOmM6S6kIJOAz2Qy8DwPe+65p5w3b54SpLTjr62tVbUA9MyZ3oIqCkopsWHDhkhgH1kGiPr6enz3u9/FFVdcIQGwBaAKYAWgCli4cKGcPHmyXLNmjRL6NTU1yGazqoY45e2T/5KqlPU2tMDm83kVea1bKOj8Tz/9dNxzzz2SdjzpdJpTmZhuQ0okuaEaGxsxZcoUuWTJEliWpUzsJCybm5th23bZWJ8oBZfif8IwhG3bqhARzS/LslS64u23344rr7xSsgWg78MKQB+hMLeeAvqWL1+Ok08+GY2NjUrAm6aJlpYWOI6jFqrCnuaFbUXLBT0Hmsz8FB9w4YUX4sUXX5TFugoW67HOCgKj3/eFQltvPQ20zqmvfvWr8oMPPoi8joS/ntdfLil0+r2un58er6Bj2zaamppw22234Te/+Y0E2q6L5l6x0t5MZcIKQIWjV+PTW+zW1NTg008/xXHHHSdXrVqFRCIRKdVr23ZZmCi7S01NDYIgQDKZhO/7OOigg/Dqq69KoK0JUWEnQ1IcysFHy/QuukWJ7hUS6NQwhxSBo48+Wr7xxhuR5ysdPSMAaEsVbm5uxjXXXIMHHnhA0udjGIaqZmhZltpkMJULKwAVji7UgdZFq7GxEfl8Hl//+teVqVKv3keFgcrBxN9dWlpaVH8CahJ06qmnoqGhIZKypVcR1OMJGIaqSgJQ5XSB1jmVyWRgmiYuvfRS+dxzz0Ve3xcUALKWUbwPuQMpffDcc8/F0qVL1UYjnU6r1GHuJVD5sAJQ4egtSIFWc2R9fT2uvvpq+fzzzyvhT7t9vWxpOZr4uwp1NgvDUBVf+fLLL3HaaadJqg5IUDYBULwcMlN9FFqHgKhZPJ1O4+abb5Z33nknwjBUdTXCMFS1ASoZfT5Qdg31KaA4gcMOO0zS5+T7Pvr3769ciUxlwwpAhaMLfqBVwD/yyCPyxhtvRE1NTUTo0ev0hiGVDqVpAW3d0DKZDObMmYObbrpJpTSRyZ8UIF7AGKBN8FPbaoJqTrz77ru47rrrlCLZ1NSk2v9S1cxKRs+WyWQy6jqp/4dhGFi5ciUuvPBCCbQpDLz77xuwAlDh0IQkYfb555/jvPPOA9BqHqeJqqf3USOQvqAEUG8DoO0zoDrmP/nJT7Bw4UIAbdHcetoWwwAo2tWSYmQuvvhiuWHDBkgpUV9fr/6nL/WZ0NNlqfeB3gE0DEPce++9ePLJJ6XebKsvWBCrHVYAKhwKxqGI/quvvlrmcrnITpd+UioQNTHpC5G8hmGoBSwIArWTSyQSWL9+PX7961/LNWvWqNeTpcS2bXYBMACi8TNAm1Xg1ltvlS+88AKA1vulsbFRKY5NTU2ora3thbONn0wmo+YRWdTIAkAuNtu2ce6556p6B1RciKlsWAHoA1Ak/BtvvCHvvfdeuK4bKeJBOxc97xcon4Y/3cV1Xdi2jWQyqRYrcnX87W9/w9tvvy3p86BdjZSSXQAMgiBQQl1v+7thwwZce+21KoCUFGhSMgGoDoGVDFkIaUMQBIFSBIA2pSgIAqxZswbXXnutpNoHTOXTNyQAA8MwcNFFF6nfdRO33o8caMt/LzWKmXbYusbfVeWBqgzqxyg03dNx6fdSKvnpuc1k7SCTLl3nN77xDdW1jd6jL6RAMt2H7kHqi0FV86655hrZ3Nys3GUAIiWpuwK52shiQHOn0AVHxbgKz63Ua9DbfJf6vzRnpJTquvQ6BuQOIaX5lltuwaefflqS9ZBdBOVP5TuBqxzK2/3Tn/4kly9frp6Py7ytVzWjimLkF6S/GYaBdDqN4cOHY4cddsDWW2+NIAiwceNGNDY24r333sPnn3+OIAgitQpoUXEcR6Uhmaap3i+uamqff/45br/9dvnNb35T6Asbw1DbaFKYW1pasHTpUtxzzz2qw2R3oCqBpmkqi4FenEefXxR/YFkWxowZg3HjxiGRSKB///4wTRMrV67E8uXLsWrVKmQymUgGA1XApDlGVTO7e5/T/5umqTqJ/r//9//kn/70J0Hv1RHsIih/WAGocKg5yR//+MdIFC+ZKrurhXuepxYpPVLaMAwMGzYMF154Ifbcc0/stddeglwN9J6UU+y6Lj777DM888wz8r777sOLL74YOS/qUU5NV4DWNsbNzc2x7CJyuRxuvfVWnHfeeWrX1RcCIJnuQy4jelxTU4Mbb7xR5vP5WAJFqbkWzUcKLqSOe1RMZ9iwYTj22GNx0kknYfLkyWLAgAFKIdB3+ECrRe+VV16Rr776Kv70pz/h008/VQo/vRe1Ko4DvRIgADzwwAP48Y9/jOHDh7ebnyz0Kwwy/fSFEYYhDjnkECmEkADUz+6Ob3zjG7LwfXr7WvXx4IMPqmt1HEcahiEBSNM0Y7l+Go7jSADywAMPlLNmzZK+76tSqVK2durTf+9ovP322/jmN78pAchkMqnOPZVKSdu2Yz1n/T546KGHVBRzb39nPMpj+L6vHmcyGaxYsQKWZcl+/frFcu/RXAQga2pq2s2lsWPHyj/84Q+SGlzRTyllu7lUOL/o8aOPPir32Wefdu8R19zR11HLsiQA+YMf/EAW+zzJOtjT36P+vieddFJsaz99BieddJLszevbUqPXTyDum6DaFIBsNovp06dLAO2EZxwKAC1UpmnK2tpaef/990sKMuzocyhs30vP5/N5VYAok8ngnXfewS677BJZJGnU19fHNoFp0TrwwAMlvTedZ29/fzx6f9C9KqWkfPdYBQjNQ5qftm3LRCIhf/KTn0iaE/pc8jwPmUwG2WxWnV9H9yrV5fd9H/fcc49MpVIymUzGeg2WZUnDMNTxDMOQ/fv3l2ShKxysAFTO4CDACmf58uV46aWXAET9/np/8u7gui6SySSmTJmCZcuWiVNOOUVQNzFyB+TzeWSz2UhNAj3QiRZY6t4HtOZRjxs3DosXLxbXXHONikZ2HAepVKpd4OLmYhgGfN+HYRj497//jf/+97/KtMtZAAzQtgnK5XJ44IEHVEXJuMzZNC+ocdXo0aPx4osviquuukqYpqmCD8mHb1kWUqmUmhN6YGwQBMhms5EGRKZpwjRNnH766eK///2v2GWXXVRGTHcxTVMpH3rV0Y0bN2LOnDmyo/eI472ZLQ8rABXO/fffLwGo4LzCXOY4+NrXvoYFCxaI4cOHwzRNuK6rgg+B1kCnVCoVKTREWjJRWJJXyjbf649+9CPx17/+Vf09m83CcZxYBDSdA/lT//rXv0oW/AxBAjQMQzz88MOypaVFpZXGcZ+YphlJmZs0aRL+85//iKlTpyKZTEbmKbXgzuVyShnQd510vFQqpY5p2zZyuRyy2Swsy8LWW2+Nl156SRx77LHdPnegbd6SJQJoU0juvPNOeJ7HAbUVDCsAFc4dd9yhOuEB8ZfoPPPMM3HLLbcIoK1hCBUdIqEKIGKOpGJDFIRYKPz1Gv0USXzIIYeI5557TgUd6g1auoOUEslkUkVXk6LBixYDtCnKhmHgvvvuU797nhdLoawgCFRNiqlTp+Kll14SgwYNAoDI3NAj+pPJpGpYJYRQ84nmFym1+v9QZcLm5maYpok77rhDnHHGGd0+f9/3IYRQVhG6piAI8Mwzz7Q7J6ayYAWgzCkmBOm5efPmyXXr1qlcXqBNSHdFeOome/rpOA5mzJiBv/zlLyq6Xy9/So/1WurFegzQAkYU/p2i/2tra7HvvvuKv/zlLxHFohBqUgKg5GIktJD7vo8PP/wQr732WmwKRmfQd6P7DQleNNs+n0wmo74Puod7klWrVmHevHmRwkClWAB0i5teipqeI0aMGIGnnnpKpFKpiFWKKMViR/Or0MqnZytQrYHa2lrce++9YsaMGe2uRz/XUpBSFs0oyOfzeOihhyJNt0hhYAW7MmAFoMwpnKi085ZS4tlnn43l+NTalHz7ANC/f3/ceeedWzynh8qNkiA444wzxAUXXADXdSNmTqpPrhdm0asdbgp9h+J5Ht544w0J9EzKUjKZVC4T+t5IwOmKW7WSTCbR2NiIdDqtys6SFSuuOhClsGDBAqn76oHSrUQk8AprTFDFPMuycMcdd6C2tjZyX/eEkLzzzjtF//79AUDNbzrXOFwcL7/8cqQ9sh4nwApu+cPJ0BWAPllJUAPAnDlzYjl2Op2G67oqzchxHPzyl7/E2LFju338zqCWo7rr4re//a149dVX5cKFCyGEUAsy7ay6kudcrBjK/PnzceGFF/ZIKWQpperBALSeeyqVwurVqzFjxgzZE1aIcsb3faRSKcyePVtsu+22qmsjUFolyLiYPXs2gGh3yVKg75esTIX/n8/nceWVV+Lggw8WQFvHyjiDDDfF2LFj8Ytf/ALf/OY3lcJJXTGpBkF3mDdvnnpMbrbCgl9M+cIKQJkjpVRCTDfdrV27Fm+//Xa3j28YhmprSgV/9thjD5x77rnCdd0e6ZpHAtp1XSUg//jHP4rdd99dUhQyEDWZd2WRps+PduALFixAJpPpkWYu9J7kAqBFMZ1O45NPPinZitFXoe9GN08Xix3Zknieh+effz5SOKsrVQD1e0tvl5vL5TBixAjMnDlTZDIZpNNplT3TU4Go+Xwe5513nvjzn/8sX3rpJTXH6Ry6q4B+8MEHWLlyJbbeemv1eVFXRab8YRdAmVO4EFqWBSklFi9eLOMwIeu7YNu2Yds2rrvuOgA9kyZHAp/eL5VKwXVdTJ06Faeeemq7Rbiw819n6NkI9POzzz7D6tWre8REqfcfIDdGY2MjNm7cqDqqVfsAoOpDkD9Zt/xsSaSUWLNmDT788EOlqAGlu4d0C5Me30LPfec730F9fX0kbbbw2rck5Fa57rrr2sXoxDW/X3/9deVSow0LuwAqA1YAypxikygIAixatCiW41M9csuykMlkMG7cOBx66KFC35VtSfTro92+4zjIZDK49tpr1QqZSCQipYKBri3S+oLr+z7++9//9tjqRD5SMrnW19dj6NChanGu5kE164UQSglobm5WZXO3NEIILF++XFLJXnquVEigkuADoFJl+/XrhzPPPFPQ6+i1eszLloY2DIcddpgYN24cstmsigWIo1SwZVlYsGABAKh0SqZyYAWgzCm2KBmGgffeey+WBZKaoVD63sUXX6xqlfcEehpUJpNRO+R0Oo0JEyZg3333BRANCKPddCmLjZ7HTH5J0zTx7rvv9sgOjN5DCKHaNruui3Xr1vVIFkK5Qylu5JcWQijXTE8JybfeeiuyM+6K+Z8UGKBt109K7XHHHYchQ4YAiGai6K/tCQzDQD6fx0UXXaQsK1SUKI5jv//+++p3vRshuwHKH1YAyhwhhIogB9ry7T/55JNYTKQUuEOPv/a1rwkyV/aEgKJdP+U/A607ZNotn3zyySr6X29L3JUIbf06aOe5YsWKmK+kOIWKipStFRHjrtdQqYRhGClzqwvenog/AVr92IU1LUqNQQiCQEX16/n5lmXhxBNPBNBW+4Ly54HWGJCeUALIypJIJPC1r31N0DnG4f8HWl03a9asAYB2WRDsAih/WAGoAEhAUwqg4zh47bXXYlkgaWECgKlTp2LQoEHKF9tTgUrF8qZramoAAMcee6woDALUc7U7Q/fp6sJlxYoVPaLgUB0EEiZd7ddeLVAgHZmsgXirWXaElBIffvihamtNCim5Z0qB7qMgCJSSapom9t9/f2X+p+f02hU9cX1k8fJ9H0OHDsWuu+4KALEFIpqmqdyR5ProKesh0334m6ogaGJJKWOrVKZbF8aNGxcpNFIO/rxBgwZh++23B4DI9XZ1d6G/XgiBxsZG7gXAQAiBpqYmAJu/Yy02T7bbbjulxPYmeglh0zSxww47RPoKxHF83/cj70Owklv+sAJQARROrnw+r7rxxQHtUqdMmRJ5v3IgmUxi0qRJ6ndaVLqyeOl+eIpUJrMlw6xbtw7A5isAhXEApmli0qRJZbETLpzLkydPjlUwS9nW5RNoW6PKaQ1hOqb371CmUwoXpmw2G3uKVBiG2GGHHSJ+ynLR4Lfbbjv1WC8GVCqF1yGlxIYNG+I5Oabi2bhxo3q8OUqAngFA84esVr2Nfl5SSowbNy72+e37fq+Ub2a6DysAFYA+UX3fV9p2HDsMytkFgCFDhkQWs3IgCAIVSV1IqeepB1DS4B0KQ+jpcJsbwFYoTAcPHlwWWR662zAMQwwdOlRdWxwKAB2fyl0zlUX5rPRMhxRO1M3JV94UtCA4jiP04L9yiOI1TTPiS9UX1VKvv5iw79evX1lcH9P7FKbTkpuoKxQWEEomk2VjQZNSqn4aiURC6EpBd6Fr5OI/lQkrABUE+RcTiURs3ex04ZjP59Xs1f2avU1HVf9Kvf7CRckwDAwbNiyWc2MqGymlUjA3Jyi0MCaFMj7Kbf4Q2WxWxnleVPc/mUz2aO8GJh7K4w5lNoluuhZCqF7hcdPc3KwWi3LR5KWUyOVy7VLDurqIFRZSGjhwYNns0JjeQ0rZrrU1PV8Kxe5DCowrF/RraWpqij3N0rIsrmtRobACUOaQT54q2VGLUcdxYlMCaCFYt26dyn/W87G3JOQ3pKDGwuDGMAwjEft6JTW9eltn6MJeCIFBgwZt9jlvDoWKFS+YbVCLWhpxlKgtlTAMUV9fr5rkpFKpSNXIzigUovQ9r1q1qp1PvLAaYE/0OqBS30RDQ4OyTsQRB0PC3zRN1dsCKJ8AYmbTsAJQ5tAkLfT7DxgwILZAtiAIUFtbi8WLF6v3pLGlobKrtCjSYiWlhOu6ME0T77zzDoC2dEX6LEoNOirsOAegx6K09TaxdC65XC4SeV7tUA8A+m6pWE5PBJVZloVhw4YpYZzNZpV7rRQFmP6vsMHRO++8066ksJ7BUiiYtxRU6IvKKr/zzjvo379/pElWd/B9HwMHDlTlnIHysR4yncMKQIVBQnns2LGxHVMIgZaWFiVoydLQU4VyPM9TO2IynVLFQwB48803I9HMemW9UihUnoIgwLRp03pEwdF3iNlsFmEYIplMoqampsdK3ZYz9BnQT8/zlADpioWnO4wdO7adi6ir6PeYYRiqOp5+DXRvJxKJHo2Yl1Kq+fXWW29h48aNse7QiynTbAGoDFgBKHN0Iaxr7RMnToxN0yaz/9KlS1W6UE8tvkDrrl9P1dP55JNPsGbNGvW8nsJX6iJTaH4XQmC33XYTPRGkpb+nLvBd14Xrur3eire3B5n79c+JovB7YifpeR523HFHNc9IOJd6b9H56nOT3FaffPJJ5LX69fSk8qfP5XfffRcAIjv27mDbNiZMmAAA7eoLcKpt+cMKQIVAAYAktHbaaadYjmvbtqoL/uGHH6oFAugZH6WubPi+r3Yqvu/D8zzMmjVLAm3d/Oh/Sq1XQMKEIrOFEBg4cGCPxQCYponm5mb1mASbaZpwHKfX2/H29kin0xF/NN0LQoge2SULITB+/HhBDakKXW6dUZj+pv8+a9Ysqbu3kslk5Pg91QwIaFU4Fy1ahI8//hiO4yAIglje3/M8jB8/HkDPWWyY+GAFoAIgAaYrADTpugsJefJH3nXXXZJaBPdkWk8YhpGiIpZlwbZtPPTQQx3WPShlASvMGpBSYo899ij5/+OAdnvk+9V3v729A+/tkclkEIZhxPRfGA+yJbEsC9tuuy2A1vuL7vtSU0wLX0f3qGEYePjhh2FZFizLiri2Cl+7JaFrchwH99xzj6RzA+K7/3fccUfox9VTIpnyhr+hCqCwnWwYhth5553FVlttFcvxTdNELpdDIpHAww8/jDVr1vTY5C30neqliN955x0sWLBACQa9HoBewXBTUMSzvlAfd9xxqvvcloYWX6CtFTDQGo1t23av78B7ewCtDZ9IIaDvrCfp378/pkyZ0mXXEqHfk3rnygULFmDJkiUAWudwR/UstjSmaWLDhg34xz/+oeZ6XOcxaNAg7LTTTuzwr1DYZlMhkBWAeosPHToUo0aNUo1MNhfyXwKtO+/PP/8cjz32mLzgggtEPp+PtC/dklBUtGmaSKVSyGazuO2222QmkwEAlaZFUKBiKViWpTIKpJTYb7/9BNAzOzDbtiNmX9r1jh49Gj/96U9LUmL6MslkEmvXrsXIkSPV7lv3IW9pRZSCXb/yla/gjTfeUC1tgdIrAlqWpSwYUkoV/d/S0oLbbrtN/upXvxJUawCAmsM9pYD+z5ImP/nkk9jfc7vttsPw4cOV0kZdB4G2NYspY3p7BxDX8H0fUkoccMABEoA0DEMC6PYQQshzzz1XUlpQEARqspMveksPav1Lj+k8vv/970euUwihHicSiZKv0XEc9dg0TTly5EhJHQellO1+SinR0tJS0rlTiheNws9MPya1FZVS4t1330U6ne7292eapgQgk8mkBCC32247Wey8eFTnoHn9l7/8JTIP9HunOyOdTssPPvhAvQ/Ftkgpkc1mI+dSODdKvUczmYx6TMemeZXL5ZDP5zFy5MjI9di2XfL6R+tK4VpjGIb80Y9+JAvPk9aqnhj0melrx2mnnRbL2k/DMAx51FFHSf1+KXxcqaPPuAA2t0JcZxT63vXHPYGUrTsKPY+cdkYHH3xwO/cAvabUSmSGYagdD+UMf/7557j66qslFWjRf5KJM51Ol3R83Y9LZlAKQKJj0i5fT+/77ne/q3b/3SEIAliWpfKgTznlFEgpuWwpA6D1nvM8D0cccYSgIDa6N+LwkWcyGVx66aWR8ruWZSGTyaigwMJ5QZR6j5J1ge51oK24UiKRwFVXXSU/++yzyN+7EuMjZfudPFkODzzwwHYxE7RW9VQQJxBdO+JslkbvUazGQV+Icaj8K/gf9OXHbX6iACX9mHE34+ns/fXH+k23zz77iBEjRkRu/q4GGenR17r/8re//S1mzZoldYWD/kaQUO0M0tRpIpmmqYq+BEGAdDoNz/PU+V5//fXyhRdeKOnYpUCT17IsnHnmmaKcyrQy5cGgQYOw//77wzRNeJ4XWyVMIQSeffZZ/O53v4sE4JGApnlgmqZyC3SlSE9HbXilbM2SmT17tvz973+v5hZl/AClZfnoa4geIyGlxDbbbIPp06dHFhld6Mfx+ZWKfp4tLS0A4hXQhQpAT17blqTPKAAAIju7uBQAoLVGPpmZ9PfqCfTUMX2HL2VrcY9TTjklsmDo6UWlFvIRQqjX2rat/P7f+9738OGHHyrtPpfLqcC1IAhKKmerC/9i3wddH53z3Llz5dVXX41cLhfLBKa4CSEEJk+erNIn+8oEZrqPbdvI5XI455xz1L1OaYFxkM/ncdVVV2HOnDlS/C8dFSguoGielKoEJJPJiP+dFIIgCPDRRx/h+9//vio+RUKsK9dFc5bORf/9tNNOU1ZDfa0hS0ZPZxERTU1N0D/nOI5dGHTcV2IbKl4BKBSOetBaXIt8Y2MjgOLacE9A0cOmaaqbOgxD+L6P0047Tei50/o5ljIBSJunXbHnecjn87AsC++//z6+9rWvScpjTyaTyGazKn2tFOi8C89Lj6kgU+XChQtxwgknqEUljs+YIrOllDj//PPVdfSVCcx0H1Km9913X5FKpdC/f/9Yjw203vMnnXSSCjSk1ECaA4VtrkvtRUAWSdd1kc1mkUqlEAQBGhoacMIJJ8i3334blmUhm82260FQSt6+7jYA2jYfhmHg1FNPFboVgV5LbpXeorm5ueQAzs6g45C1cnMaRpUzFa8AANEvQt+VxvEFmaaJhoYGANEJoysdWxp9ISD/Fi0ikyZNwtSpU9v9D+2sO0N/DWnxtGsGWsvwTp8+XX744YcAWv2N1I64VBeA/l5SShUpTAudaZp46qmn5IEHHig9z1OLIbVp7Q6kPPXr1w+nn3660K04PZ1uxpQfumVv6NChOPXUU1WfhjgUUEr7JFfiQQcdJJ944gkJtG5W9DmwOWsKpfQ5joNUKoUwDPHhhx9i+vTp8s0334xYwID2lUVLQY+vov+ZNm0aJkyYEIn617MnelrB1gVzQ0ND7O9Pls9Ci0ilU/EKQKHPO5lMxur7CcMQa9eubafR9lQAiOd5EWFeaNZPJBK45JJLkE6nIwtIV27QwqAnPcDPtm2sWLECu+66q3zsscfUQRsbG0tyARTGMOjfVyaTQS6Xw/XXXy+POeYYZLNZ5PN5ZbIkX153SSQSuPjii1FXV8c7fyYCuQ1JeF122WWCqhPGgeu6Kkgvm82ipaUFJ5xwAn7961/LXC4HPdBVT8kFShPQyWRSbVAA4F//+pecMmWK/OijjyJxDKRM6+6HUhUAUpBo919TU4PLLrsMtm1HPie9UicJzJ7GdV1s2LAhtvem46TT6cgGsKfqiGxxejsNIY5B5mIpJS666KKSU1xKHf3795fUR1vvlNcT10ZmQj3VR8rW9B7991GjRqn0HPrZ1TQmPY3QMIxI2k8qlZIA5Omnny4/+ugjSCkjuc+b+m6KPW5oaMD777+PvfbaS51nYeqmZVmxpPAMGDBAbtiwIZJ21VPfH4/KGXR/HnPMMRKIJw2QUgv1e9kwDCmEkPvss498//330dDQ0O4cCh93NMi99cEHH+DUU0+VAGRNTc0m50NXzl//DOgattlmG0nvTUM/J0oR7ok5pr+H7/tobGyEvo7FMQzDkN/+9rclvZ9eubLSR6+fQBxDvwF/9rOfbdaNvqlh27ZcsmSJeo9C4dubg5SfP/zhD+q641i4NrUY1NXVycsuu0wuWbKk3QQsNjE8z1PP+76PuXPnyq997WuxfEek7CWTych1649/+MMfSilblam+kLvLI76hrx00rxcuXAi6t0jokSAXQqh7riu1NjY1p0466SQ5b948Sefium6kPgYNvR6IlK3C6J133sG3vvUtWVtbq4R0nGsfzSN9Q3HHHXdIOp/e/v7oc6DHL730kloX4vwcrr32WimljMQu9fZ1xzF6/QTiGPqN+Pvf/z52AWiapnzmmWckLRCu65bVDZDL5eD7PsaNG6cWLNu2ZRyacCqVihyn8JiTJ0+Wl112mZw9e7b84osv4LpuZEJms1l8+OGH+Pvf/y4vvvhiudNOO8U6Mel89Os2TVPdA6NGjZINDQ0q15rOqbe/Mx7lM/QiMlJKNDU14dJLL1X3GAl6fV2JQ/jr96/jOHLixIny0ksvlf/4xz/kRx99FNlohGEI13XxxRdfYPbs2fKyyy6TkydPLnosekxWu+4OvSDXpEmTpJQyYrXozaFbY33fx4MPPtjuu+ruSCaT8re//a0spzU/rtHrJxDHIK04DEP885//VBp6XILGsiz5m9/8JlLxqpiG3huDlB/P8/Dkk0+227HENeizFEJIx3EiOyK9Ulhtba0cPny4HDlypBw+fLi0LEvatq0Es37MuCZp4WJsGIY6v2K7lWJmSx7VPQp3s0uXLsXAgQPVWqLfq/X19UowdPfeLaakm6ap3AT6XKqtrY3MQxqO48hEIlG0Yl8cgxQA0zTl3LlzJQncchCI+jn4vo9rrrkm1rUFaN0EPfzww2oDqMub3r7+7o5eP4E4RxiGWLp0KeIWMJZlyZNOOkl2dOP19mhoaFAL2Ne//vVYF4FNmdJokeqKpYFKiMbtp6Nzpe8LgDz66KMlfT5SSpXi2NvfF4/yGaTUU6dG2mlLKfGLX/yinZDW50IcMSqbmhObmsM07zp6jRAilvPTj3HRRRdJKdtcEeWyBurnccghh8S69tN44403ImXYpewbCoCQUqIvQNeRz+eRSqVkV6JcO0MIgW233RYffPCBoC+/WAGM3oKqewkhsHz5cuy2227SMAwVjNMd9Hxax3FUsxRqokIRwlTZjH63LCtS5rfwmDS6m4qnNwmiimqe52HQoEF48cUXxbhx41TzFWpuRN9bTzY7YsoTujeA1jVEb7jV0NCAQw89VC5YsCBSgwOIVs7sDnqFTX2u0vxIJpNwXTcyr/Ty3XRepmnCcRwEQRAphdvdNZAykGpra7F06VJBjX8sy+qRZk2lkMvl1Oc0evRouXr16tiOTd/zmjVrxKBBg9Q1b6q4WUXR2xpIdwdpYZ7nqcdbb711rNofmdk++OCDSKBbuWjANGjn8uijj8a+u+5oFGugou8aCoPzDMOIPVCpcOdvGIYy/et+1MLvqy9o8Dy6P/QMG92fLKXEG2+8gUGDBrXbWce1+9ePW2zNod8ty2q3q43bzbep8dhjj0k9jqZcAgClbHPHUvBmnGuLZVkylUpJul5aY/uKC7H31beYIG1MSok99tgjtuOShut5HubOnSupAI/+t96EKvjl83mljR533HHi0ksvjUU7pRa9+u/pdFrVDqDdBkGmVKr7n8vl1A6FWviSqbWUSmSlniPQ1hDkG9/4Bs4//3yRy+WQSCRU9L++c9J7DzDVTbF5TA21Jk+ejB/84AcA2ir0+b6vCvx0F8dxVH49FfRxHEcJG+oPQCZ3HbqXbdtGTU1NpPSuvk51lyuvvBLHHHOMoKqDcfZKiAP6LubMmSOBaIOg7uL7PnbdddeyWvNjpbc1kLhHS0uLCgSJw8+sB9acffbZkt6nnNrJ6n5tetzS0oIpU6bEukMp/DxN04z4Iov5MR3HaddGlP4vjvOiY5MVYLfddpN6uhRZAAqjhTkWgIeU0Z1ssTa9VJnyzDPPVDtuutfijGMpnDuFFgB6Tp9nekrilhrTpk2T1AtFnzPlYgHQW5UfddRR7dbsOD6Dc845RxZec0/WgtmSo+LVGSklgLb61o7jYPz48QDiadggZVv5x0ceeaTd+5YDtm0rXx9pw6Zp4tlnnxVjxoxRFftSqVTRtp6lfE50w+joJnV9QuhQvIB+HPq/UtBLjRLF6nELITBs2DA89dRTgv4PaOsNoV8n+UsZRt8l02O9wiV1rrz55pvFQQcdBKDN4kSlfPXX0n3X1X4ThXNHStnOulZM8GxuzX2aD4VWArp2x3EwatQoPP3004Kul+J8pJSxWRe6SuE6ROeWzWbx5JNPKutJV6DvuNjzpmli1113jbw/0L5qY6VS8QoATTK6ESzLwtSpUwUFqXQXvflFLpfDww8/LIMgUAE35UoikUA6ncbs2bPF4MGDIYRQncL0mv/6hCITn95euLehRinpdBqpVKpdYBMtRP3798cLL7wgBg0a1GuLE9P3oDlSX1+P+++/X0yfPl0FtlIdfr3MbhAEqKuri62bZRwUm9c07ym4kIQdXdOQIUPw1FNPiZqamrJSlgs3LEII5PN5PProo5KUJrrOUtd/vfGYrsTR86QAkDtGf++Kp7dNEHEMvYIWPTds2LDYTN/QzH5HHXWU7O3rLTY2ZZKaP3++HDZsWDuTYrFiJoUlgHt7FDtH0zRV5TMAcujQofK5555T3wsVaiq3IE0elTvIB79q1SpMmjRJAojcg1QTQC/DW07zqKN5XViG23EcOWTIEDlv3jxZ7HPQy66Xy/B9H/vtt1/EhVJqkKbuQinmmqypqZGNjY3tPoO+srb0+gnEMcjPq38xRx111BYpiTlo0CD55ZdflmUU6Kb6FLz77rtFsyPKaZHqaOFyHEcKIWQikYgoBDU1NXL06NFy2bJlyOfz6jvhQj884hqFtfmDIMC6deswY8YMJWhobSCh0xO++bjmFj0mwTdkyBC5ePHidmsIZQD0tPAvxdf+8ccfg74DUsRKzZCg6y7MXqJ4iylTpkj9XKSUZVUDobujPGxU3YT8vLqJ6+ijj461DgAda+3atfjLX/4iC/OCywHdvKf7FMMwxPjx47Fw4UIxduxYAFAdyqSUykRY2IO8HEzpemEWCt4jhgwZgieeeEKMHj060naVzHhx5Gkz1Y1uxicz+sCBAzFr1ixxyCGHwPf9SDdN8idvrm8+bvQ5TFkMtE5K2erLr6mpgZQSY8aMwRtvvCEmTJig1g59LSm3vHc6x7vuukvSWqz/LDW2CYiu8fQ5AcAhhxyiXqvXGykX90636W0NpLtD3+npGuqyZcuAGLRkPfeWtMptttlGlksWQGeaaD6fh57DumHDBhxzzDFFK2UV5hpviWp9mzPq6uokgEht86OOOkp+8cUX6jr1bo10reXyHfGo7BGGITKZTLtOcC0tLbjyyisjO09aMwqzX3pr6HO4cJerj69+9aty/fr1aj3xfV9lQxTbgfd2FDxZJDZu3Ijhw4dHrq2rNRr076lwXXzuuefa1QDQz6G3783ujl4/ge4OXejr6V+u62LnnXeObQIVlta8++67ZW9fe1dGS0uL+pyCIMDMmTNlKpUqWke/txetYoPOUwghf/jDH0YaM+nXmclk1MLFCgCP7o7CNtxBEES6Sm7cuBHPPPOMrK+vl4ZhRBrnlNsonNuJREJaliWvvvpqqW+kCq+5cOgFgXpj0Pv7vo/f/va36lqK9W0oZX3X3TX6Gj9kyBC5cePGdh0Y9Z+VPnr9BOIYet6u/mVddNFFsU2cwp3xxIkTZTl1lSumkdPvev6urgjMmTNHjhgxot2iRTnGW6qtcFcH7fxHjBgh58+fr7py6TEfpKXrSk5fmaQ8enfotSToOd01JaXE4sWLVVwAWQrLYf7otTr059PptBwxYoR8+umnJV0DlQ6XMrpmFM6l3p5bVEzM8zwMHTo0cl2bowToQp++u3Q6LQ877DCpv6eUMhJn1Nv3ZRyj108gjpuBHutfSlNTE15//XV01GSjVDORHh1bKCTvuusuqZ8LFcygUQmBIp7n4ac//akcMGCAus5iZUkLFxX6XPTHhQrEpo5hGEa7DoH6YkWvp53/d77zHakXOertz40HDxq0EfB9H3feeaccNWqUEiJ6URp9zui7zo5cbbr1sdj8KdyUbKrJlm6dSCQS8qqrriqrDUwpo3D3/fOf/7zbClKxhkr03dx3332yt695S49eP4HujkJttLCv9+DBg9WN7zhORPCXqiUW+ohIGZg0aZJcv359RFvOZrMVtfMkJeWdd97BWWedpa6zpqamnfujcKLonyUtSJtSuIopC/ReuoJF1odkMilnzJgh33rrLUgpsWHDBnXe5JPt7c+PB4/CsXr1alx11VWbFOq6ckvryqYqZJKgKpxDhRsZOo7+nP4+J598snz33XchZeem/nIZFHWvu/aWL1+OsWPHdlsBoM+K0jfps6qtrZUrV67s9Wvf0qPXTyCOoe+0C1u+Xn755ZFJ0tHjTQ09+KxQIbj66qullG27gEJTdCUMMqcFQYBFixbhnHPO6TD/vjCoSF+Yign+YgtasR1N4fH3228/+cQTT0R6jxemZPX258aDhy5E8/k8crkcwjBEPp/HunXrMHPmTDl69Gi13nSUHqiX9yUFuFgrbvobvV7/e0fz0LZteeKJJ8pXX30VUrauUYXWynIdhSWHydX5jW98o9vCv5giQOOrX/2q7O1r74nR6ycQx9DzvilKlwTGkiVLUChwuuICANrM//r/JBIJlaP+2muvQcro7r+SFIBi57po0SLMnDlTjhw5MhLhXGqhoMKgyWKCv7DI0uDBg+Ull1wiX375ZVmsVj9FJpdLHXIePGh0NN9d18UXX3yB3//+93LChAlqDjmOo1xgm8q2sSyr02wcCmTTX1dTUyNHjBghZ86cKRctWgQpW5Vm3X2md8os11GstseTTz4ZWZO6Owo3O7Zty4cfflhWwzojpJSodKjTW2HeJ3WhmzZtmnzttdfgOE67PtqdQfmyBHWXA9r6bR9xxBF45JFHRDKZVJ2yKBdY79BVjjQ2NqK+vh5BECCXy0XKmlIu7Lx58+SDDz6If//731ixYkWkN7n4X6cy/XdSvoD2dfv1z9K2bdTX12OfffbBaaedhoMOOkgMGjRIvT91FbRtG1LKSO/0bDaLdDq9hT8dhtk0nudF6mfoa5CUrbnzLS0tal699tprePzxx+WTTz6JhQsXRtYkAGrtoHlCuedU24KEIb228PcxY8bg4IMPximnnIL9999fnQz1sQegTOrif90Hyx1aSyj4b++995aLFy+O5di0hpP8CIIAgwcPxscffyxs2y6LWihbkopXAPQbG0BRoXvHHXfICy+8EEB7gV4qlmXB930kEgk1gXSl46qrrsLPf/5zAbQJT12IljOktJAwd11XCVff91XhizAM8dFHH+GFF16Qzz77LN5//30sWbIEzc3NRZWpwrr9dXV1GD16NCZNmoQ999wT48ePxyGHHCLocwXaf5+FZDIZJBKJivhcmeqEBLouXGl+UEvslpYWmKaJhx56SC5btgwLFy7E0qVLsXLlyshcKpxDhGmaqK2txc4774yddtoJ++23H6ZPny7GjBkTqWVPSgONSpw3+XxerQ/f/va35U033RT7e5CC5bourrjiCtxwww2iUtbv7lDxCkAhrutGtGUhBNatW4d9991XLlmyBEDb7r8rykBNTQ1aWlrU76SpU/MJ27bx0EMP4ZhjjokoAeVOJpNBOp1WSg1NNDIZlqIBb9iwAatWrcK6detkQ0MDmpqa4HkeDMPA0KFDUVNTgyFDhoghQ4YgnU6rhUjvg06Q9YA6lZFW7rpupMOa67oVsXth+jb6hiOfz6u1oCOy2ayqwkmCTRfQLS0tWL16NdauXSszmQxWrVqFMAxh2zbq6urQr18/bLXVVmL48OEYMGBAp+enK/AEpS9allX2axStz1JK/OMf/5AnnXQS0um02gjolUE3F9u21XqVSqXw9ttvi2233Ra+77MFoBLQtWsdKVt9cIlEAjNnzpQ///nPlSm51DLBpCwU/k8qlUI2m43chKNGjcKCBQvEsGHDKkYB0CGhTyZNKWXEPEb3Cj0mBYECc2iy6NaRwmPQ36n8ML1foQKiC3jdKuC6LmzbVqU7+0xJTqZiIVcVzXfyVZMiUPj3MAwjZXULlWDdpE9/pzLD9HddqOvzstjx6Tla+8qpnG9n0Prw2WefYYcddpC5XA4AYhP+QJt1FwCOO+44/OMf/xB6iee+TJ9QADYF3UCrV6/GdtttJ13XVV/25roDdHTFwHEczJgxA3//+99F//79i7oCaMcNdG7uZhiG6cuQBUVXcmjnTetmU1MT9ttvPxXMqCtOcaArAK+++qqYNm1aLMetBPq89CHB279/f3zrW99SzTsogCYuUqkUXNfFvHnzcN5550mgTSM3DEPdYOl0OtJUgmEYplrRd9m0XlqWhebmZpimCc/zcNxxxynhbxiGWrvJWtjd96cN3MEHH4ypU6cCAMjS0NepCglEboDLLrtM9OvXL9ZOcWQuJ3OU4zh49NFH8c1vflPqAp7MeRRwV26dBBmGYXqD5uZm5RKkAMra2loAwJlnninnz5+v4ibIXw8gFhcAHcu2bXz/+99XcUnJZLIquolWhQJAVoCRI0fioosuQi6XUwFm3YVyRUnAk4/6tttuwzXXXCPJH0evJf91NdxcDMMwm8L3fSXsAUQ2Rueff7588MEH4TgOMpkMgKjVNK4YKyEEDjvsMOyzzz6CYidoo9bX6fMxAORPyuVySCaT2LhxI3bZZRe5bt06VbWru1DGgR4L4Ps+wjDE//3f/+HHP/6x6NevH4BolgLHADAMU+1Qdz8AKh7g5JNPln//+9+L1mshK0AcgYC1tbVobm7GSy+9hL333lvQ+VTLutznr5K+SD0W4LrrrlO15LsL+bAoVYdiAYjf/e53OPPMMyUpWo7jqFS3arnJGIZhNoVt2zBNExs2bMDhhx8uH330UbVWAq01RPRiS4ZhxOICaG5uxplnnqmEP9Caqgkgso73Vfq8BQCI5uo2NjaitrYWX/nKV+Qrr7wS23vodQF0PxXQevPuuOOOeOqpp8TAgQNV9kElpeMwDMPEjZ4yvHLlSpx44onylVdeUWtkMplEEARqPa2rq0NTU1Ns77/VVlvhvffeE4MGDUJDQwP69evXLo2zL1MVW1A9H7e+vh6GYeDmm28Wcfh49II2rusilUoprZU01Uwmg9dffx1Tp06VL730kgQQURAYhmGqEQqinj17tpwxY4Z85ZVXYJqmWrNzuZxaRy3LQlNTk/qfODZQ/9//9/+hvr4eAEBuWsuyYNt2VQRq93kFgL5ECuogs8748ePxne98J/JaKjyTTCZLDjChdsT0PtQQCGgr8BEEAdLpND7++GMccMABuPHGG6XjOPA8L+KG0G84Os/C+vkMwzDlAsU+deRO1Sv56eub3jX1+9//vjz55JPx4YcfAoj2AQFa10LqA0B/78q6SH0YgGh55unTp+OSSy4RHVUUrbRCbptDn3cBkIkpn88jmUwCaAsM3LhxI/baay/5ySefKL+PXhRCf9wdqGqg/vjAAw/E/fffL4YNGxYpD0oUK5Gbz+dhmqbKf63EaoMMw/RdVJc5bYdOAdgA0NTUhLq6OgDAf//7X5x33nnyzTffRHNzs8rJj3PnTedBGzHHcdT5vfjii2L33XeP7b0qkT5vAaAvn0w6YRjCdV0EQYD+/fvjz3/+s4ooBVpvYEpLietG1AU1FZj497//jXHjxsmf/vSnUtd0s9ksXNeFEEI12aHMgXQ6HWlGxMKfYZjeJgxD5PN5tW6RT5+sAuTHB1p9+I2NjfjJT34ix48fL59//nk0NzerY3VU1n1zoWwrOq7ruvA8Dz/72c9Q7cIfQJvG1leH3lde73/teZ76/ZprrlE9tKH1ve+sD3cpw7KsTl8zadIk+dhjj0n9vJuamiLX4fs+crkc9B7V+rXx4MGDR08PSqUufJ6ErpQSmUxGPX/ffffJHXfcUQKQjuNI0zSlEEJaliUNw1Brr23b3V576VgAZDKZVM8dcsghks69tz+/3h593gUAbLpzXHNzM2pra7HffvvJF198MVKkp7BX9+ai57JSfAF1FqQug6lUCnvssQeuuOIKHH300UI3mxVDSs4iYBimPJCydVNF1lZ6jtaohx9+WP785z/HW2+9VVL6NaVIx5GqrWdobbfddvj3v/8thgwZEksp4Uqn6hSAfD4fMVNRWeANGzZgp512ks3NzSpeII560HQcvd90IVTQgs5rwoQJ+NWvfoWDDjpI6JOJuujpXcW4lgDDML1FPp+H4zhFu4X6vo+//vWv8ne/+x0WLVoUaZxWV1eHlpaWdiZ6na50be0IPSXbsizMmTMH+++/v+DNUyt9XgHQtdDCXTP5rEjIvvLKK3LvvfeOTfgT+o1smiYoA0APNiTlgG5YKSW22mornH322TjppJOw1157CTpnUmY2ZdlgGIbpCXTBDwCvvfYa/vznP8tHHnkEzc3Nai1Np9PI5/MdxlYJIZBIJFScVlzQmnrjjTfiW9/6lqBWyp1ZWauBPq8AUBEg8lNRhym9yANlBQDA/fffL88444xYtE+gLZNACAHLstrl/xdrSUzvTTeqlBLjx4/HiSeeiGOOOUaMHTsWdXV1vPtnGKbXaWxsxOLFi+Vjjz2GWbNm4b333gPQtrZReV/a8BSWTtcDtHUKC6p1hwsuuAC33HKLSvlj62krfV4B6Ax9F00Wgp/97Gdy5syZAKK790JhHVeaYCmQeyAIAtTU1OArX/kK9ttvP8yYMQPDhg0To0aNUtqs53kwDEOZ1zZVNKPav3+GqXZoDSgUiORy1J/P5/NYtWoVFi9eLJcuXYrHH38cy5Ytw9q1awFsmTTqTUHvQR1W6Vr0uKs999wTr7zyinBdV8UDMK1UvQIAtFkAKIjFsixccskl8rbbblNCVxeouva6pdFv5GKaM2nI9fX1GDlyJLbddluMGDEC9fX1sG0bW2+9dSTmQVdiyCLCMEx1YllWu+h913WRy+WQz+exceNGZDIZrFy5EsuWLcMnn3yiApipOh+Z68li2dOdTimQGmgL3KZ1c6eddsKbb74pAERSvTkGoJWqVwBI+Dc2NqqSkKT5XnzxxfJPf/pTO3cAlf/tiXK+xaJhyS1AAlz3qelug0Jfm37Td6T1MwxTPejrCgUYF1s7inXl0ylcI4u5NrcUFDfgeR5M00QqlUJzc7PqvzJmzBhkMhmk02nl/i10A1crVa8AkO+Jbga6UaSUyGazOPfcc+Xf//532Latuk9R1D61kuwJdHMaad5AW8lgqnDFPQYYhimVYrFO+qYgkUhEApaFEGqt0U3q1P6c/p8q7/UEJNB1pWOnnXbC3/72N7HLLru0K5pGmz6OA2AFQN0E+XxeaZK6aTyTyeDCCy+UDzzwgNKCpZSx9KIuhcL0Gr3CFlB8AheWv9Sf12FTGMNUN47jqDS8wh08gHZrj+4+LESPF+jJRjrpdFoVJBJCYNCgQViwYIEYM2YMWlpaUFNTo5QA6lsQRyO4vkDVKwAAVC1+CggkpUC3Bpx33nny7rvvjjUytRQKFQ2aYBSr0NHk5e+VYZiuUrghoN+LbTIKs5r0GCn6fUsrArQeU+r2xIkTMWvWLLH11lvDdV2k02k0NjYimUzCcRxO/Suguu0f/4NucsdxsHHjRmURSKfTaGpqghACd911l7juuusigrUnfEgk/A3DgG3bygJAzYH0joKFWjs911EWAO/+Gaa6oV07FSoD0C4oUDft27atAgepw6pu8tfXnp4wr9O55XI5TJ48Gc8884wYPXo0pJRIp9Nobm5GfX09HMdBEASRvgQ9YcEtd6reAkC7ffJp6eUh9foApDn+4Q9/kJdffvkmW2DGSWHkPxANDCQrQEd+vM7OkZUAhqleOlr/9YY+XZERFIRcqAxsaU488UTce++9IpVKRdICC+u+0N/Y/dlK1SsAXYGUgLlz58rTTz8d69evV9pkYd+AYjECesGLuAoNMQzDVDKFtVb0+iW0vlIQtN5andbcn//857jkkktEv379WLB3EVYASoSaBpFW+dlnn+GUU06RL730khL2hTmoQFuQjZ4bS1aFns6XZRiGKUcsy1L+fH1dTKfTyGQykdTndDqt0v4efPBBHHDAAWLgwIEA2gKbixUxYtrDn06J0I1kmiaampowcuRIPPPMM+I73/lOpDa/7mOqqamB67pKmyV834fv+3xzMgxT9ZAFIJvNwvd9lctvmiYymQyAVldmOp2GEAKZTAYTJ07EokWLxPHHH6+Efy6XU+ss1WphNg1bADrB931Vz7q5uRnJZFLt4CkQ5sknn5Q/+MEP8P777wNo1WaFEO2yBQo7AvZ0RgHDMEw5Upi5RFZUMv2TdTWdTuOSSy7B97//fTFo0CD1P4WtiDnHvzRYAeiEMAyRz+eRSqUAtOa3UvSrnkv60Ucf4frrr5e33XZbJBo/kUjAdd1IakxXA2sYhmH6Irq71LZtteECoPz9JPy322473HzzzTjiiCMEgKLV/Chgmmq5cDzApmEFoEQ8z4PruqipqQHQ5mtqaWlBKpWCYRhwXRfz5s2Tl19+Of773/9GdvjUkVC/2Xn3zzBMtaOvhXrTMyKVSuGMM87Ar3/9a1FfX49MJgPbtpXCQOmLes8UFvylwQpACVBBIJ3CioEUHEiR/zfccIO84YYbIKVEU1MTTNNEIpFQPi1WABiGYdrKDYdhGLEIJJNJ7Lbbbrj++uux7777Rnb9FBC4KTO/nsbNFIcVgE4gXxJF99MNWij4gahJyvM8rF69Gt/73vfkrFmz0NLSEsnZ78lWwgzDMOWILvD1boKTJk3CZZddhnPOOUcU5vBT2Xa9vK9e+pyj/0uHFYAe4JVXXpG33347Hn74YTQ3N6v4gVwupyJgqcmGXtkPaKu+RZYFKnkJtK89wDAM05Polky99gkJ9sL+JXp8FJnqyYwfBAEGDRqEK6+8Et/61rdEodWViR9WALYwuoVg4cKFuPXWW+W9994Lz/NUH+vCCNhEIqGyDnR6orY2wzBMV0gkEpGUPcMwkEwm1e9kOSUloJjlc9y4cbjkkktw/vnni9ra2kinQWbLwQrAFoZ8VWS+MgwDn376KW6//XZ5xx13YOPGjcoSQNq0bgHQI2PJWpBMJiGEQD6f52qCDMP0GroFU0qpNjVANJVPLw2sF/WZNm0afvCDH+Dwww8X1GcAKB7hz8QPKwC9RBiGyOVyeOSRR+Tf/vY3PPXUUyoIhv4OtNXqpvrVbAFgGKacofimjsz/Q4YMwemnn46LLrpI7LjjjgCg1jbqfULuAw7i27KwAtADZLNZhGGoUgjJBKbf3F9++SVmz54t7777brz44ouq1gBNBppUet1svS42wzBMb5FMJmGapgp2Jr8+5eUPGjQIhx12GM466yx85StfEel0Gp7nqSqqRHNzM2pqajiNr4dgBWALozenKIxQ1btWUWaAEAINDQ2YM2eOfOKJJ/DMM89g7dq1AFA02IZhGKa36CidOZlMYvz48TjooINw/PHH4ytf+UqHxXvy+TyklEgmk+o5quzHFoAtCysAWxjawetRr3p/bSDaktd1XRiGEbnxly5dijfffFPOnj0b9913n4oL0I/FMAzT01DskmEYyGazOPfcc3Haaadh4sSJYvjw4QBa3ZnUJ0X/PZFItOuRorsCOAZgy8MKQC9AATB6SgwpA3oN68JCFk8//bQ8/PDD1e/cUphhmHJBCIGnnnoKhx56qACggp9JkOsZUfR3IQRc11WugE2tf0z88KfbC1BwjP47oae9UAwARdiSBYGei0v4b9iwQfTv3z/yXuXig9PPad68efLQQw+NPBcH5Xz9TDwUfrcbN27EgAEDYrmB9OPOmTMHBx10kCi3+6gnrh9oi/Cn69Z38brwp/8DEIkDKFz/mC0LJ1kyDMMwTBXCCgDDMAzDVCGsADAMwzBMFcIKAMMwDMNUIawAMAzDMEwVwgoAwzAMw1QhrAAwDMMwTBXCCgDDMAzDVCGsADAMwzBMFcIKAMMwDMNUIawAMAzDMEwVwgoAwzAMw1QhrAAwDMMwTBXCCgDDMAzDVCGsADAMwzBMFcIKQJmj9xMPwxBSStVzO65+2UEQAGjtGa738qYe3+WA67qwbRtSSvVT7x2+uRiGAdd1ASBy3fSZMJUN3cNhGAJo+47DMGzXn35zoLlomqa6J4UQCIKgrOaPECJyX9PnERd0/aZpqs9YX7uY8oQVgAqAJm0ikYBlWWryxiGkDMNQx6PFq1AR6E30a29paQEAeJ4X+Ry6AykU9BhoXbjiEA5M+aArd2EYwrbt2O4fHc/zALTOpTgU1DgoVHCBtvOMA1J4AKhNie/7sR2f2XKUxx3KbBLf9yGEQF1dnSDBRIK6u0gpkc1m2y2G5bIDpkXL9300NzfHfnzTNFFTUwOg/a6onHZwzOZR+J2GYQghBNLpdKwCmu5TUlLLCf0+NgwDQRCgubk5NgWfPkfTNFFbWysAVgAqBVYAKgDSqvv37498Pg+gdcGJYwJLKbFmzRq1M4rz2HFApsV8Po81a9YAaLUG+L4fywKeTCbhOA4AtHN9sAJQ+RR+l2TlMk1Tfe/dofCe+fLLLyO/lwO6NYvm9urVq2Uc56hvRIIgQL9+/QBAWdWY8oYVgAqAJnB9fb16Lk4T/YoVKyS9h75QlssiFoYhampq8O677wKI17dYV1enHhcu5uViwmU2H/oOdfcOoX/33YUsCx999JF633KZP3TNnudBCAHDMLBs2bLYjh8EgXoPUgBM04w9zoCJH17hyhx9B1NTUwPTNNXiEscEMwwDixYtUo/1hbIczHhBECgFaMGCBQCAXC4XiV3oDltvvbUK2KJr58Wr70AKgK4I0Pc8fPjwbh9fn5+GYeDdd99V86acrGiFvP7667GenxACyWQS6XRazR2eQ+UPKwBljhBC7S5SqRRGjx4d6840DEO88sorRRetctgB0wK7cuVKvP/+++r5uDIgdtxxx8j76OZMpm8QBIFSbnWTNX333UHf5UspsWjRIjQ0NHT7uFsCUgSam5vxwgsvxKIA6ArW6NGjkUwmAUBlBDDlTe+v8EynUBQvAIwcOTJ2wbxkyRIVvEQKB1B859DTkKB/+eWXJQUBCiFii2IeM2aMsqoUplwyfYNCZY7mz7bbbhvL8fXUws8++wyNjY1lkwao78bpfNavX4/FixfHco/rQckjRoxQLgZyNzDlDSsAFUAymVQCevLkyXBdN7YdsBACq1atwosvvigzmQyAtgWyp4Sg7/tKoPu+DyllRMB7nocHHnhA5Rh3tQYALUS0OxFCqMj/nXfeOfLedPxEIhFrqhTTO3ieB8dxlBWNlAHXdTFx4kQAUPcC0BpgCpRuvqeaFIRpmrj99tulng8fhmHEndaTioFhGOq9SVjfe++9EojHiua6Lurr6+F5HiZNmqSeiyPAktnysAJQ5oRhiFwuh5qaGkgpseuuu0YmdXeRUiKVSuGmm25COp0GAGzcuBFAz/kwLcuCbdtoaWlRUcq0WDU1NaGhoQGzZ89WwUaWZUViAzqDFvVcLgfHcSClVO81YcIEQRHL+oINlIcFhOkeJOQKha7jOJgwYYKwLAstLS0qb5+ybIDSXGCe5ynFMpVKIQgCPPTQQ3BdV91nek2Anq6vQTUzyAWSz+dx++23xxbjYxgGGhsbIYTAnnvuiWw2qxQuVqDLH1YAyhzdNB0EAfbZZx8Rt3btui6efvppLF++HEBbdHRPLVS5XA7ZbFYFOZLP1vM81NXV4frrr1fWCaBrUfq6S4N+J4YNG4addtoJQOtCWSjwyyEGgukehZkdpmkqwTdhwgQMHjxY/V3fEZdaZ8O2beRyOQBANpsFAHz22Wd47LHHJFma1q9fX/Re6gkLm23boLkjhMBDDz0kP/nkEziOE6uCa9s2pk+fLgrTLZnyhle4CiCRSKgd79ixYzF06FAA8QhoErgA8Mtf/lJS8I4ucLckQRAgmUyqXXo+n1fXZds23n//ffzud79TC1YYhup8S9lhCCFUGWHa4dGxdt99d/W++s6s0BLAVC56lcvC55LJJHbZZRdlXXNdV90Ppabx0bF0pTwIAvz6179GLpeD67oYOHAggFZFVwihFIWeUjDT6TQymQyCIMB1112nBH8cga5UVXHo0KHYeuutlRURiC9Ql9lysAJQ5tAiRP5pwzCw7777Aoin2Iausd95551YvHgxmpqakE6neyQSnhYjwzCQzWaVuZJ2aT/4wQ8kLc4kmA3DKPnadWGum2GDIMCRRx6p/saFS6oHvYz0EUccUdTsX6qfnhRY/f4MwxALFizAHXfcIR3HUQKflO1UKtVjyqXneXBdF+l0Gj/60Y/kRx99hCAIItfcHchSd9BBBymB77oum/8rBcqL5VG+o6WlRT3O5/P4+9//LgHEMkzTlACk4zjScRy5ww47SPJf9tSgiGnP8yLP33TTTRKATKVSEoBMJBLSMAx17vrjTV2f/jrLstT/Llu2LFIDgM6h8DkelT/CMESx+/p/qaVF7ye6VzY1DMNod0/S/Tpy5Ei5fPlySNk6b6WUKsg1l8v1yH1G7zF//nwJQPbv31/Ni2QyGdv68fjjj8sgCNT7+b6vMg94lO/o9RPg0fnIZrOQUqrAmlWrVmHUqFGxKQFCCCmEkI7jSADy7LPPllJKNDU1bfFry2Qy6jEpAL7v4/nnn5fpdDqyGNu2rRYcffHpbOgLuW3bEoDcb7/9ZOEiRe6F3v6+eWyZ4ft+u+83CALsueeeag7o91UpCgCNfv36qf/Xx7Rp0yS9FykBzc3NPXrdq1atwpgxY6RhGOocaR7EMYYNGybXr1+PIAiUgsOjMga7ACoA8kv6vg/LsjBs2DDsueeesRXyIH+n67owTRP33HMPfvGLX8ja2tpuH78zyBwaBIEyIa5YsQKnnXYastksDMOAZVkqVZBMrkDpPkz63CzLgud5sG0bX/3qV1WfASmlei2ZgGkhYyobPWZE/77puzUMAyeccIIK+tMD40qNkk8mk2hoaICUUsUC0M/XX38dRx99tKTnKNi1p2hqasJRRx0lP/nkE6XsptPp2PL0hRA44IADMGDAAABtLj2ePxVCb2sgPDY9Cs3iUrbuzOfMmbPJ3Uqp5j3aCRT+TKfT8vrrr5dStpkRXdeNPJZy06Y+fbfleZ56rW4qLPzf9957D6NGjSq6m9rcQSZasnCk02m5YsWKXv9ueZTH+Oijj1BbW7vZu//OhhBCnnPOOZKUBCmj8zoIAnieV3QebcoiRTvuYq8JggANDQ3YbbfdZF1dnTqXRCKh5lapc6ympka9vpjb7fnnn5fr169vd21sDSj/0esnwKPzoZvJySyfz+cxduxYKYSILFabu3CRuV0/XjKZlJdddpmk96eJncvl2p1jEAQq+GdzrjGfz+PZZ5+VyWRS0mIc5wKcSCSUMnDCCSdIKWVRnzCP6hp0D5x44olK+BfGmnRn0DFTqZQ85JBD5IYNGyBlq3Ck+VLohupIqe7obyRofd9X7sKXXnpJTpgwQaZSqYjA72oMDY1kMtnOfWDbthw/frwkl4brusjn8+xGq6DR6yfAo/Oha9L641tvvbXdRBZCqEWnFA2fdsX6ax3HUcewbVtOnjxZPv/881LKth0JBSZ2tPv3PA8tLS3tFAKKSqafZNq/9NJLI4KfAqniGPT5mKYpTdOU//nPf+Smzp1H9Y1nn31WOo6jFEU9JiAOBYCU81GjRslHH31UFjuHwvtRVw4o/qdwPpEyTq9ramrC73//e7nddttF1gR9Tm/uHNLXGTrWrbfeqq6FYhwKH/Mo39HrJ8Bj04MmuK5VNzQ0wHVdZLNZjBkzRgnqzYli1oOB0ul0JBCK/r+urk5aliUvvPBCuWLFioiJLwgChGHY4QKlm/oL/+b7Pu655x45evToyE6JlBIyPcaxANNns//++0v9c+XBg5TZ/fffP3KvxGUFSCaT0rIspVwAkF/96lfl888/L3WFPp/PR6xSNK+KnXMQBGq3T3Ns7ty5cu+99468txBC2rYdWQsKM2M6E/w0HwsVmjFjxshMJgPf99sFNnIWQGWMXj8BHl0fJEjDMMQvf/nLLgn8YkOf4B0tDLpF4Mgjj5R33323XLVqVTtzXzEzJaVgBUGAXC6HBQsW4PLLL5ejR48u+l6JRCJyTt0ZpOD069dPApBPPfWUpPPiXQoPXRGcNWuWBCAHDBgQqwJgGIaaP+SPN01TCiHkjBkz5M9+9jO5dOlSdR7k29fnEFXLLBSqy5cvx/XXXy/Hjx9f9N4vtuun3XypabT6PNJdhL/5zW/kpj5PVgDKf6jSjUz5QpHrhY9zuRyamppwxBFHyIULFwJozRigXUSpJJNJVc6U6vBTdoBpmqpuvmEY7SLvR48ejYEDB2Lw4MGoq6uDZVkqopoWM8uykMlk8Nlnn+Hjjz/Ghg0b2r03VSvTKxOWWo51U1AGAQCceuqp+Otf/yqoQQzDSClVkyApJc444wz54IMPAojn/gNao/+pgZfv+7BtW0Xh68fv378/Ro8ejVGjRqly3NlsFrZtw7Zt9f/r16/Hp59+itWrV2PdunWqf0YYhqpaZrHzNgwjUoCIzqMUTNOEaZqqM+nkyZPxzDPPiLq6OjWXOlqnmDKmtzUQHp2PYpo0mf+klLjzzjvVDlc373XFh1nMVEjH0U2X5CfVswwo7qCrPtNCnyL9TgGJcezA6BgDBgyQS5YsUZ9ZT9Q44FEZQy+0tWTJEgwYMCC2GIDCWhb6/U73Z0cmedM0i7r29PPSd/i660J/nuanZVnt4oVKuQayxtE6kEgk5P333y+lbFubKMuHPkd2sVXG4DoAFYSel0zadRiGOPfcc8W0adPU7pm0/FJqcestcn3fVzsU2knYth2pz0+RvrQT0HcfUkplKdDL9hK0i9BLptJ16N39MpkMbNuOpVwqHeOb3/wmdtppJ/V7bW0tlytl4Pt+pH79TjvthAsuuEDdy92Femro5YeBtvuS0mKpVLXjOGre6umBHZ1LEASRtYDmol5XQ8o2t4KUMtIdsDMsy1L1QWgdOOCAA3D66acL+ozCMIRlWcpC4HkeEolEbB1LmS0HuwD6AL7v46OPPsKkSZMkmfIJIQSSyaSqRw5ACdeeqPXfEyQSCVXbXDdVkolzt912w3PPPSfq6+sjizB3+2MARASsEAIbN27E/vvvLxcvXqyUat2VJISA4zix1dPvbcjVpyvE6XQauVwuspnwfR91dXVYuHCh2H777Xn+9AH4G6xwyLc4ZswY/PGPf4xUMrMsC1JK1WQnmUxCCKEq6vWFbl1kodBjD3zfVwtaKpXCLbfcoqqvkXWCmpgw1Y3rukqQkRJQW1uLW2+9Fel0Wu1uSfibpgnLslRXyUqHLH0Uk5BMJlXMThiG6hqpGdkf/vAHjB49GoZhoHCzwVQerABUOI7joKWlBZZl4ayzzhKHHHIIbNtGXV0dfN9Xgk/3yxV23KtkSMiTeTMMQ6RSKWXduPbaazF9+nRBipHuzuAdDENKsBBC7egty8L06dPFzJkzlUKpl5/2PE+ZxiudIAgghFCbBVojgFZFyHVd1NXVIZlM4qCDDsLXv/51kUgk0NLSoj4TpnJhF0CFo/vhyNc3YcIE+cEHH3QpyrdS0SObdVeAZVk46qij8I9//EPk83kkEgkIIeC6LhzHicQcMNUN3Qt0nwCtGTaO4+DYY4+Vs2fPVgplXV0dmpqaALSPqu+L0JwaN24c3nzzTZFKpUBZNHqLbaYyYQWgwiGBpgctLV26FNOnT5dNTU3Kv0fmOkq3A/rOAkaBiPTYMAxss802eOutt0QqlQIQDTKkz4xhgFYlOp/Pqx2tLtgaGxux5557yo8//ljt+MnS1BfQ1wB9bSCrGlkRX3/9dbHjjjsCaJtLuVyOrQAVDqtvFQ7tZqVs7RkQBAF22mknPPPMM0pb1xerXC6nAuX6yiIGQFVNC4IAW221FV544QWRSqWKmmlJ+LPyy5DiXEyQ5fN51NfX4+WXXxYDBw5UTawo374v7H7pWvRNAj1P0fxz5szBjjvuqBQCoPVzI2sJU7lU/h3MtOZzGgbS6TRM00RzczN23313/OUvf0E6nYbrukilUioViQRlX1jAgDZB7jgOxowZgyeffFIMHTpU7fSbm5uhxwAQcaR5MZWNfg/QvWEYBpqbm1VRrf79++Pxxx8Xo0aNguM4kcJBlQ4V96J1wbIspNNp5PN5pNNp3H///dh7770FBRJTuh/A86cvwC6ACod28YZhRHyYVIlr1qxZ8txzz8WaNWvU/9TU1KClpUVVKKtk9OpqtbW1mDdvnpg8eXLEjEsmS3qOPhv2YTJ0b3T0U3/NO++8g8MOO0yuWbNG3UOVHmND10BrAjF48GDcfffdOPLIIwVdK+3+9foCrARUNqwA9HGklHjllVfkYYcdpoKXgI7LnOqLXlylUDdFZ4so+ShJWSFBToGPQOu1jBgxAs8884wYP348APbzM/Hh+74SekuXLsURRxwhP/3000iOPAXgAm33tF7Wuhg9oUDQHNbncmFZ71QqFakT0r9/fzz00EM4+OCDBQv4vg0rAFXAxo0b8fnnn+Poo4+WK1eujETKp1Kpsolq1isI0n0ZhmEkut+2bbVw0bnuueeemDdvnkgkEpHCLRzpz8SF53mqep7neTj00EPlggULVPMroG1n7Pu+UlgLawz0dACh/r6FBYwo7kEPEK6pqcHcuXPFuHHjOMCvCmD7ZxXQv39/7Lzzznj99dfFlClTAECV6tStAlSK1HGcHjWN046EdlG6TxJoW1hpx0R/S6fT+MY3voGXXnpJUH0DfafDwp+JCyouRYrl888/Ly677LKI8NeD5Cj7RI+5obgBXdHd0ui7fsp20K8pl8spxWDPPffE0qVLxcSJE5FMJivevcGUQG83I+CxZQe1DtYfX3vttaqRiGVZRRuOAIitJe+mhhBCGobRaWMSakRC/ckHDx4s77rrLqm3RpZSoqGhQf2uNyfhwWNzBglvKdsaSNHvQRDgvvvuU424EolEpM1uR/d0qfd8XPOLHluW1a4hELX5/fGPfyz1a6aOojz69mAXQB+nWJ2AbDaLzz//HGeccYZ89dVXI75IivTtzTKftHOnqn6Um0xWi4MOOgj33HOPGDZsWOT/KAiSypayBYDpLoV9AvRAWylbqwQ2NzfjzDPPlLNmzVL/l0gklMKtB6P2NFQVU690SOfnui523XVX/P3vfxdbb721Sofk4Njqgb/lPo7jOPB9X5nOqYzp2LFjMW/ePPHzn/8cI0aMANAmYHO5nDJh9iRU0Ec3l5LwN00TQ4YMwd/+9jc8/fTTEeFPOxYK+mPhz8QFKc50X5ILAGi9z2zbRr9+/fDEE0+If/3rXxg9ejSAVmVUr85JQring+qy2SzCMFTCv66uDgAwfPhw/PSnP8X8+fPF9ttvj2QyGSmEBKBPlApnOqG3TRA8tuygRYhGNpuFlNF+3Q0NDfjhD38o6+vrZTqdlqlUqkv9wrszdHNkoRsilUpJx3HksGHD5E033SSpbzspB9TUKJ/PR65x7dq1kFK2e54Hj64OuofonqJBu3u6F13XRS6XQzabxd133y3HjBmj7uFS7/ctMej9k8mktG1bJhIJecUVV0j9ejKZjLoG3fRP18aj7w52AVQJ2WxW7eoprUlKGamP/+WXX+Kmm26S99xzD1auXNkj0cp60J6ehVBfX4+hQ4fi//7v/3DaaaeJAQMGFC3nS9cAtPZe13u7symTiQP9HqMugPpOXp9bRENDA/7617/KG2+8EatXr0ZjYyOA6D3eE2m29D4jR47E2Wefjcsvv1wMHjxYzR/6qZf1ZRda9cAKQJWj+zT1QjmzZs2SDz74IB577DEV3azfK2TO1JUEPZq/lEWOXgtA/Rw1ahQOPfRQnHnmmdhvv/04CZmpeJ577jl57733Ys6cOfj0008BIBJX0JGiXagc602/CJpDhc9Rxb7jjjsOJ598Mo466iihK/0A18pgWAFg/gdFzVNdcKA19zmXy+G1116Tjz/+OObPn48PPvggUjGMehEUC3DSi5BQ7XT9tYZhoKamBrvtthuOOOIIHHzwwWLChAmqhTHD9CVaWlqwZMkSzJ07V86ePRuLFi1CS0uLUgD0Hh1kft9UwS7TNCOVPGtra7H99tvjgAMOwNFHH4099thDJBIJJeRJkbdtm3f3DABWAKoe2q3rC4Je+awQz/PwySef4O2335YrVqzAZ599Btd1kc1mkcvl2jUfop0L1RgfOHAgttlmG+y4444YNWqU2H777dVCR0PKtsApbjjCVDL5fF4Ja7q3lf9VCHzwwQf49NNP5fvvv4+PP/4Y69evRyaTURa3wrlk2zaSySRSqRQcx8GIESOw3XbbYdKkSWKbbbbpcN4WzmnqBcKV/qobVgCqHN0kSClPQgj4vo98Pq9244VFTHTT5KYopkyQBaDYLkQ/H4bpS3R0b3c0HzaliOv/qyvO9B40LzOZDKhCJsMUwgoAo6DIZt0v6Lru/9/emexADMIwNELl/78XVIk5uXJTd7kO+Em9QJc5EAiJJ8S2bbcLPeoHYNLh+1QIk8OdEddJboxx0hs4R2n+GYToEQVjB4DHPqfFAKfQACoLwj5qrfK7SOmx/bTWjqp/EXa2jR2A5eFdP7fxwSZo41A92vLOIv/NJAuPACIKfESxlcdmdjiSVkq5VdzDZtDPV35ftkmOAPTeTxocfj9+h1kXOwDmqL3Pu4m8aKtFHJONCj0y6lmMO/UNawDMLCgNQB7zESHtQ+3OsyOunHDl1DP7vl+idWZN7AAsTj4x723yeOsHSlzIfXgelQn51DLvTMxsZLtBqgtO95M+4ItYD6k1lYJD/xjjs9jXrMEPntqOkn2P/EgAAAAASUVORK5CYII="



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
        "PARKING":      PICTO_PARKING_B64,
        "TRANSPORT":    PICTO_TRANSPORT_B64,
        "RESTAURATION": PICTO_RESTAURATION_B64,
        "COMMERCE":     PICTO_COMMERCE_B64,
        "BANQUE":       PICTO_BANQUE_B64,
        "HOTELLERIE":   PICTO_HOTELLERIE_B64,
        "FORMATION":    PICTO_FORMATION_B64,
        "DYNAMIQUE":    PICTO_DYNAMIQUE_B64,
        "SANTE":        PICTO_BANQUE_B64,
    }
    picto = next((v for k, v in PICTO_MAP.items() if k in cat), PICTO_LIEU_B64)
    # Tronquer le nom POI si trop long
    _val_str = _safe_str(valeur)
    if len(_val_str) > 28:
        _val_str = _val_str[:27] + "…"
    _pill_picto(c, bx, by, picto, _safe_str(label), _val_str, w=bw, h=bh)


def _page3(c, d, agence_brief=False):
    _header(c, "Quartier & environnement")
    # ── Brève présentation agence (si demandé) ──────────────────────────────
    if agence_brief:
        # ── Bloc agence redesign : plus grand, texte haut + KPIs bas ─────────
        bloc_top = _H - 14*_mm
        bloc_h   = 44*_mm   # plus grand pour être lisible

        # Fond teal Barbier
        c.setFillColor(_BLEU)
        c.roundRect(14*_mm, bloc_top - bloc_h, _W-28*_mm, bloc_h, 2*_mm, fill=1, stroke=0)

        # ── Ligne 1 : Titre + texte de présentation (haut du bloc) ─────────────
        c.setFillColor(_BLANC); c.setFont("Helvetica-Bold", 12)
        c.drawString(20*_mm, bloc_top - 9*_mm, "Barbier Immobilier")
        c.setFillColor(_ORANGE)
        c.rect(20*_mm, bloc_top - 11*_mm, 28*_mm, 1.5*_mm, fill=1, stroke=0)

        _brief_txt = (
            "Spécialiste de l'immobilier commercial dans le Morbihan, "
            "Barbier Immobilier accompagne ses clients en vente, location "
            "et cession d'entreprise. Expertise terrain et données de marché "
            "au service d'une estimation juste et d'une commercialisation efficace."
        )
        _para_brief = _Para(
            _brief_txt,
            _PS("ab", fontName="Helvetica", fontSize=8.5,
                textColor=_colors.HexColor("#FFFFFFDD"), leading=13, alignment=0)
        )
        _txt_w = _W - 28*_mm - 8*_mm   # pleine largeur
        _, _pbh = _para_brief.wrap(_txt_w, 9999)
        _para_brief.drawOn(c, 20*_mm, bloc_top - 15*_mm - _pbh)

        # ── Séparateur orange ──────────────────────────────────────────────────
        _sep_y = bloc_top - bloc_h + 18*_mm
        c.setFillColor(_ORANGE)
        c.rect(20*_mm, _sep_y, _W - 34*_mm, 1*_mm, fill=1, stroke=0)

        # ── Ligne 2 : 3 chiffres clés (bas du bloc, répartis sur toute la largeur) ─
        _kpis = [
            ("36 ans",   "d'expertise locale"),
            ("+5 000",   "clients accompagnés"),
            ("3 métiers", "vente · location · cession"),
        ]
        _kpi_total_w = _W - 34*_mm
        _kpi_col_w   = _kpi_total_w / 3
        _kpi_x0      = 20*_mm
        _kpi_num_y   = _sep_y - 7*_mm
        _kpi_lbl_y   = _sep_y - 14*_mm
        for i, (num, lbl) in enumerate(_kpis):
            kx = _kpi_x0 + i * _kpi_col_w
            c.setFillColor(_BLANC); c.setFont("Helvetica-Bold", 15)
            c.drawCentredString(kx + _kpi_col_w/2, _kpi_num_y, num)
            c.setFillColor(_colors.HexColor("#FFFFFFBB")); c.setFont("Helvetica", 7.5)
            # 2 lignes si contient " · "
            if " · " in lbl:
                _parts = lbl.split(" · ", 1)
                c.drawCentredString(kx + _kpi_col_w/2, _kpi_lbl_y + 2*_mm, _parts[0])
                c.drawCentredString(kx + _kpi_col_w/2, _kpi_lbl_y - 1.5*_mm,
                                    " · ".join(_parts[1:]))
            else:
                c.drawCentredString(kx + _kpi_col_w/2, _kpi_lbl_y, lbl)

        _header_top_offset = bloc_h + 5*_mm
    else:
        _header_top_offset = 0
    _sec(c, "Le quartier", 14*_mm, _H-32*_mm - _header_top_offset)
    _annonce_top_offset = 0  # plus de bloc annonce ici
    # Ligne d'accroche orange sous le titre
    ville = _safe(d.get("ville", "Vannes"))
    type_b_raw = d.get("type_bien") or ""
    if type_b_raw and type_b_raw != "—":
        _chapeau = f"Un emplacement stratégique pour votre {type_b_raw.lower()} au cœur de {ville}."
    else:
        _chapeau = f"Un emplacement stratégique au cœur de {ville}."
    c.setFillColor(_ORANGE); c.setFont("Helvetica-Bold", 9)
    c.drawString(14*_mm, _H-38*_mm - _header_top_offset - _annonce_top_offset, _chapeau)
    texte = d.get("texte_quartier") or (
        f"Situe a {_safe(d.get('ville','Vannes'))}, ce bien beneficie d'une localisation strategique "
        "dans un secteur economiquement actif du Morbihan. L'accessibilite est optimale grace a la "
        "proximite de la rocade et des axes principaux. Le secteur compte de nombreux commerces, "
        "services et equipements a proximite immediate, offrant un environnement favorable a "
        "l'exploitation d'une activite commerciale ou professionnelle."
    )

    # ── Layout inversé : zone carte+POI ancrée en bas, texte prend l'espace dispo ──
    zone_h   = 75*_mm          # hauteur commune carte & POI
    col_gap  = 5*_mm
    col_w    = (_W - 28*_mm - col_gap) / 2

    # Ancrage bas de page : footer(9) + zone(75) + titre_col(10) + gap(2) = 96mm
    _footer_h = 9*_mm
    zone_bot  = _footer_h + 0          # bas de la zone carte/POI
    zone_top  = zone_bot + zone_h       # haut de la zone carte/POI
    qbot      = zone_top + 12*_mm      # bas disponible pour le texte (inclut titres colonnes)

    # Espace disponible pour le texte (du bas du chapeau au dessus de qbot)
    _text_top   = _H - 41*_mm - _header_top_offset - _annonce_top_offset
    max_text_h  = _text_top - qbot
    if max_text_h < 10*_mm:
        max_text_h = 10*_mm  # garde-fou minimum

    # Première phrase en gras, reste en regular — ReportLab XML inline
    import re as _re3
    _parts3 = _re3.split(r'(?<=[.!?])\s+', texte.strip(), maxsplit=1)
    if len(_parts3) == 2:
        _p1 = _parts3[0].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        _p2 = _parts3[1].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        texte_xml = f"<b>{_p1}</b><br/><br/>{_p2}"
    else:
        texte_xml = texte.replace("&", "&amp;")

    # Choisir la taille de fonte pour que le texte tienne dans max_text_h
    p = _Para(texte_xml, _PS("b", fontName="Helvetica", fontSize=9, textColor=_GTEXTE, leading=14, alignment=4))
    _, ph = p.wrap(_W-28*_mm, 9999)
    if ph > max_text_h:
        for fsz in [9, 8, 7.5, 7]:
            p2 = _Para(texte_xml, _PS("bq%s" % fsz, fontName="Helvetica", fontSize=fsz, textColor=_GTEXTE, leading=fsz*1.6, alignment=4))
            _, ph2 = p2.wrap(_W-28*_mm, 9999)
            if ph2 <= max_text_h:
                p = p2; ph = ph2
                break
        else:
            # Tronquer par phrases si même 7pt dépasse
            _sentences = texte.replace(". ", ".|").split("|")
            for _fsz_final in [7.5, 7]:
                _kept = []
                for _s in _sentences:
                    _candidate = " ".join(_kept + [_s])
                    _pt = _Para(_candidate, _PS("bt%s" % _fsz_final, fontName="Helvetica", fontSize=_fsz_final, textColor=_GTEXTE, leading=_fsz_final*1.6, alignment=4))
                    _, _ph = _pt.wrap(_W-28*_mm, 9999)
                    if _ph <= max_text_h:
                        _kept.append(_s)
                    else:
                        break
                if _kept:
                    _texte_final = " ".join(_kept)
                    p = _Para(_texte_final, _PS("bf%s" % _fsz_final, fontName="Helvetica", fontSize=_fsz_final, textColor=_GTEXTE, leading=_fsz_final*1.6, alignment=4))
                    _, ph = p.wrap(_W-28*_mm, 9999)
                    break
    # Dessiner le texte ancré par le haut (sous l'accroche), pas par le bas
    _text_y = _text_top - ph  # bas du texte = haut - hauteur réelle
    if _text_y < qbot:        # si déborde sur la carte, ancrer par le bas
        _text_y = qbot
    p.drawOn(c, 14*_mm, _text_y)

    # Titres des deux colonnes — largeur limitée à la colonne
    _sec(c, "Localisation", 14*_mm, zone_top + 2*_mm, w=col_w)
    _sec(c, "Environnement du quartier", 14*_mm + col_w + col_gap, zone_top + 2*_mm, w=col_w)

    # ── Colonne gauche : carte OSM ────────────────────────────────────────
    mx = 14*_mm; mw = col_w; mh = zone_h; my = zone_bot
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
        c.saveState()
        p_map = c.beginPath(); p_map.roundRect(mx, my, mw, mh, 3*_mm)
        c.clipPath(p_map, stroke=0, fill=0)
        c.drawImage(_IR2(buf2), mx, my, width=mw, height=mh)
        c.restoreState()
        # Marqueur
        px2 = mx+mw/2; py2 = my+mh/2
        c.setFillColor(_ORANGE); c.circle(px2, py2, 3.5*_mm, fill=1, stroke=0)
        c.setFillColor(_BLANC); c.setFont("Helvetica-Bold", 8)
        c.drawCentredString(px2, py2-2.5*_mm, "+")
        # Bulle adresse
        adr = f"{_safe(d.get('adresse'))}, {_safe(d.get('ville'))}"
        bwb = min(c.stringWidth(adr,"Helvetica-Bold",6.5)+12, mw-8*_mm)
        c.setFillColor(_BLANC); c.setStrokeColor(_colors.HexColor("#AAAAAA")); c.setLineWidth(0.4)
        c.roundRect(px2-bwb/2, py2+5*_mm, bwb, 8*_mm, 1*_mm, fill=1, stroke=1)
        c.setFillColor(_BLEU_F); c.setFont("Helvetica-Bold", 6.5)
        c.drawCentredString(px2, py2+8.5*_mm, adr)
        # Bordure + copyright
        c.setStrokeColor(_colors.HexColor("#CCCCCC")); c.setLineWidth(0.6)
        c.roundRect(mx, my, mw, mh, 3*_mm, fill=0, stroke=1)
        c.setFillColor(_colors.HexColor("#FFFFFF88")); c.rect(mx, my, mw, 5*_mm, fill=1, stroke=0)
        c.setFillColor(_colors.HexColor("#666666")); c.setFont("Helvetica", 5)
        c.drawRightString(mx+mw-2*_mm, my+1.5*_mm, "© OpenStreetMap contributors")
    except Exception:
        c.setFillColor(_colors.HexColor("#E8F0F4")); c.roundRect(mx,my,mw,mh,3*_mm,fill=1,stroke=0)
        c.setFillColor(_colors.HexColor("#AAAAAA")); c.setFont("Helvetica",8)
        c.drawCentredString(mx+mw/2, my+mh/2, "Carte indisponible")

    # ── Colonne droite : POI ──────────────────────────────────────────────
    POI_CATS_PRO = {"parking", "transport", "restauration", "commerce", "banque", "sante"}
    poi_blocks = []
    if lat and lon:
        try:
            raw_blocks = _get_poi_blocks_osm(lat, lon, radius=500)
            poi_blocks = [b for b in raw_blocks if b[0].lower() in POI_CATS_PRO]
        except Exception:
            pass

    if len(poi_blocks) < 3:
        try:
            import os as _os_poi, json as _j_poi, urllib.request as _ur_poi
            api_key = _os_poi.environ.get("OPENAI_API_KEY", "")
            if api_key:
                prompt_poi = (
                    "Tu es expert en immobilier commercial dans le Morbihan."
                    f" Pour : {d.get('type_bien','')} au {d.get('adresse','')}, {d.get('ville','')},"
                    " liste les points d'interet REELS certains dans un rayon de 500m."
                    " Reponds UNIQUEMENT en JSON (sans backticks ni markdown) :"
                    ' [{"categorie":"Parking","nom":"Nom exact"}]'
                    " Categories : Parking, Transport, Restauration, Commerce, Banque, Sante."
                    " Maximum 6 elements. N'inclus QUE ce dont tu es certain."
                )
                gpt_payload = _j_poi.dumps({
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt_poi}],
                    "max_tokens": 300, "temperature": 0.1
                }).encode()
                req_poi = _ur_poi.Request(
                    "https://api.openai.com/v1/chat/completions", data=gpt_payload, method="POST",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                )
                with _ur_poi.urlopen(req_poi, timeout=20) as rp:
                    resp_poi = _j_poi.load(rp)
                raw_poi = resp_poi["choices"][0]["message"]["content"].strip().strip("`").strip()
                if raw_poi.startswith("json"): raw_poi = raw_poi[4:].strip()
                cat_colors = {"Parking":"#16708B","Transport":"#0D5570","Restauration":"#E8472A",
                              "Commerce":"#16708B","Banque":"#0D5570","Sante":"#16708B"}
                existing_cats = {r[0] for r in poi_blocks}
                for poi_item in _j_poi.loads(raw_poi):
                    cat = poi_item.get("categorie",""); nom = poi_item.get("nom","")
                    if cat and nom and cat not in existing_cats and len(poi_blocks) < 6:
                        poi_blocks.append((cat, nom[:28], cat_colors.get(cat,"#16708B")))
                        existing_cats.add(cat)
        except Exception:
            pass

    _CATS_PRO = {"parking", "transport", "restauration", "commerce", "banque", "sante", "santé"}
    poi_blocks = [b for b in poi_blocks if b[0].lower() in _CATS_PRO]

    # Cards POI en 1 colonne pleine largeur dans la colonne droite
    poi_x     = 14*_mm + col_w + col_gap
    poi_cw    = col_w   # pleine largeur de la colonne droite
    poi_gap   = 3*_mm
    _n_poi    = min(len(poi_blocks), 5)
    # Hauteur adaptée pour que les cartes remplissent exactement zone_h
    if _n_poi > 0:
        poi_ch = (zone_h - (_n_poi - 1) * poi_gap) / _n_poi
    else:
        poi_ch = 16*_mm
    for i, item in enumerate(poi_blocks[:_n_poi]):
        lbl, val, col_hex = item if len(item) == 3 else (item[0], item[1], "#16708B")
        bx = poi_x
        by = zone_top - (i + 1) * poi_ch - i * poi_gap
        _draw_poi_card(c, bx, by, poi_cw, poi_ch, lbl, val, col_hex)

    # (bloc carac_bien supprimé — déplacé dans _page2)

    # ── Plan cadastral + Zone PLU ────────────────────────────────────────────
    ref_cad  = d.get("ref_cadastrale","")
    zone_plu = d.get("zone_plu","") or d.get("Zone PLU","") or ""
    res_plu  = d.get("resume_plu","") or d.get("Résumé PLU","") or ""
    url_regl = d.get("url_reglement","") or d.get("URL Règlement PLU","") or ""

    # Position cadastre : juste sous la zone carte/POI
    cad_start_y = zone_bot - 16*_mm

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

    _footer(c, 2)

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

        _footer(c, 5)
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
            # Infos bas : surface et source sans trait séparateur
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
    _footer(c,5)


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
        # Tentative GPT dynamique
        atouts_gen = _gpt_atouts(d.get("type_bien"), d.get("ville"), d.get("surface"), d.get("adresse"), d.get("activite"))
        if atouts_gen and len(atouts_gen) >= 4:
            atouts = atouts_gen
        else:
            surf = _safe(d.get("surface"))
            ville = _safe(d.get("ville"))
            type_bien = _safe(d.get("type_bien"), "bien")
            atouts = [
                {"titre": "LOCALISATION PRIME", "texte": f"Au cœur de {ville}, ce {type_bien.lower()} bénéficie d'une visibilité immédiate et d'un accès fluide pour vos clients et collaborateurs."},
                {"titre": "FORMAT OPTIMISÉ", "texte": f"{surf} m² agencés pour maximiser la productivité. Une surface rare sur ce secteur, prisée des professions libérales et PME."},
                {"titre": "ZONE EN ESSOR", "texte": "Secteur à forte dynamique économique. Vos clients vous trouvent facilement, vos équipes s'y installent durablement."},
                {"titre": "DISPONIBILITÉ IMMÉDIATE", "texte": "Bien disponible rapidement. Les opportunités de cette qualité se louent vite — prenez de l'avance sur vos concurrents."},
            ]
    # Mise en page : 2 colonnes × 2 lignes — alternance teal plein / blanc bordé
    gap = 4 * _mm
    card_w = (total_w - gap) / 2
    card_h = 30 * _mm
    row_gap = 5 * _mm
    for i, atout in enumerate(atouts[:4]):
        col = i % 2
        row = i // 2
        cx = x + col * (card_w + gap)
        cy = y - card_h - row * (card_h + row_gap)
        style_plein = (i % 2 == 0)  # cards 0 et 2 = teal plein, 1 et 3 = blanc bordé
        if style_plein:
            # Style teal plein (original)
            c.setFillColor(_BLEU)
            c.roundRect(cx, cy, card_w, card_h, 2 * _mm, fill=1, stroke=0)
            c.setFillColor(_ORANGE)
            c.roundRect(cx, cy + card_h - 2.5 * _mm, card_w, 2.5 * _mm, 2 * _mm, fill=1, stroke=0)
            titre = atout.get("titre", "").upper()
            c.setFillColor(_ORANGE)
            c.setFont("Helvetica-Bold", 8)
            c.drawString(cx + 3 * _mm, cy + card_h - 7 * _mm, titre[:28])
            texte = atout.get("texte", "")
            para = _Para(texte, _PS("ac", fontName="Helvetica", fontSize=8.5,
                                    textColor=_BLANC, leading=12, alignment=4))
        else:
            # Style blanc avec bordure teal et texte foncé
            c.setFillColor(_BLANC)
            c.setStrokeColor(_BLEU)
            c.setLineWidth(1.2)
            c.roundRect(cx, cy, card_w, card_h, 2 * _mm, fill=1, stroke=1)
            c.setFillColor(_ORANGE)
            c.roundRect(cx, cy + card_h - 2.5 * _mm, card_w, 2.5 * _mm, 2 * _mm, fill=1, stroke=0)
            titre = atout.get("titre", "").upper()
            c.setFillColor(_BLEU_F)
            c.setFont("Helvetica-Bold", 8)
            c.drawString(cx + 3 * _mm, cy + card_h - 7 * _mm, titre[:28])
            texte = atout.get("texte", "")
            para = _Para(texte, _PS("acw", fontName="Helvetica", fontSize=8.5,
                                    textColor=_GTEXTE, leading=12, alignment=4))
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
        ay=by2-54*_mm; _sec(c,"Pourquoi investir ici ?",14*_mm,ay); cw2=(_W-28*_mm-6*_mm)/2
        # Atouts 2x2 pleine largeur
        atouts_h = 2*(30*_mm + 5*_mm)
        _draw_atouts_cards(c, d, 14*_mm, ay-3*_mm, (_W-28*_mm))
        # Bloc positionnement loyer — en dessous
        loyer_y = ay - 3*_mm - atouts_h - 8*_mm
        lm2_str = f"{int(loyer_m2_actuel)} €/m²/an" if loyer_m2_actuel else "cohérent avec le marché"
        loyer_txt = (
            f"Le loyer affiché est positionné à {lm2_str}, en cohérence avec le marché "
            "local des locaux commerciaux de ce secteur. "
            "Les DVF recensent uniquement les ventes ; notre positionnement "
            "s'appuie sur les baux commerciaux en cours et la demande locative locale."
        )
        loyer_para = _Para(loyer_txt, _PS("lp", fontName="Helvetica", fontSize=8.5,
                           textColor=_GTEXTE, leading=13, alignment=4))
        _, lph = loyer_para.wrap(_W-36*_mm, 9999)
        box_h = lph + 16*_mm
        c.setFillColor(_colors.HexColor("#EEF4F8")); c.roundRect(14*_mm, loyer_y-box_h, _W-28*_mm, box_h, 2*_mm, fill=1, stroke=0)
        c.setFillColor(_BLEU_F); c.setFont("Helvetica-Bold", 8.5)
        c.drawString(18*_mm, loyer_y-7*_mm, "POSITIONNEMENT LOYER")
        loyer_para.drawOn(c, 18*_mm, loyer_y - 11*_mm - lph)
        _footer(c,6)
        return  # Fin branche location

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
    ay=by2-54*_mm; _sec(c,"Pourquoi investir ici ?",14*_mm,ay); cw2=(_W-28*_mm-6*_mm)/2
    # Atouts 2x2 pleine largeur
    atouts_h_v = 2*(30*_mm + 5*_mm)
    _draw_atouts_cards(c, d, 14*_mm, ay-3*_mm, (_W-28*_mm))
    # ── Taxe foncière (estimation uniquement, sans l'encart DVF) ─────────────
    taxe = d.get("taxe_fonciere") or d.get("taxe") or 0
    if taxe:
        try:
            taxe_fmt = f"{int(float(str(taxe).replace(' ',''))) :,}".replace(","," ") + " €/an"
            taxe_y = ay - 3*_mm - atouts_h_v - 8*_mm
            c.setFillColor(_colors.HexColor("#EEF4F8"))
            c.roundRect(14*_mm, taxe_y - 18*_mm, _W-28*_mm, 18*_mm, 2*_mm, fill=1, stroke=0)
            c.setFillColor(_colors.HexColor("#888888")); c.setFont("Helvetica", 7)
            c.drawString(18*_mm, taxe_y - 7*_mm, "TAXE FONCIÈRE ANNUELLE")
            c.setFillColor(_BLEU_F); c.setFont("Helvetica-Bold", 10)
            c.drawString(18*_mm, taxe_y - 14*_mm, taxe_fmt)
        except Exception:
            pass
    _footer(c,6)

def _page6(c):
    c.setFillColor(_BLEU); c.rect(0,_H*0.5,_W,_H*0.5,fill=1,stroke=0)
    c.setFillColor(_BLANC); c.rect(0,0,_W,_H*0.5,fill=1,stroke=0)
    _logo(c, _W-54*_mm, _H-56*_mm, w=36*_mm)
    c.setFillColor(_BLANC); c.setFont("Helvetica",11); c.drawString(14*_mm,_H-20*_mm,"VOTRE PARTENAIRE EN IMMOBILIER COMMERCIAL")
    c.setFont("Helvetica-Bold",28); c.drawString(14*_mm,_H-38*_mm,"Barbier Immobilier")
    c.setFont("Helvetica",14); c.setFillColor(_colors.HexColor("#FFFFFFCC")); c.drawString(14*_mm,_H-50*_mm,"Votre projet devient le nôtre")
    c.setFillColor(_ORANGE); c.rect(14*_mm,_H-54*_mm,50*_mm,2.5*_mm,fill=1,stroke=0)
    for i,(num,lbl) in enumerate([("36 ans","d'expertise locale"),("+5 000","clients accompagnés"),("3 métiers","vente · location · cession")]):
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
    _footer(c,7)


def _clean_desc(text):
    """Nettoie variables n8n. Préserve la structure \n\n pour le parser."""
    import re as _re
    import html as _html
    if not text: return ""
    # Supprimer variables n8n
    text = _re.sub(r'\{\{[^}]+\}\}', '', text)
    # Décoder toutes les entités HTML (&#8201; &#160; &nbsp; etc.) avant de stripper les balises
    text = _html.unescape(text)
    # Nettoyer entités résiduelles et caractères invisibles
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u2009', ' ')
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&nbsp;', ' ').replace('&#39;', "'")
    # Supprimer les queues de listes orphelines n8n : ", , , , 'Arz, Ville, ..."
    import re as _re2
    text = _re2.sub(r'(,\s*){3,}[^.!?\n]{0,300}$', '', text, flags=_re2.MULTILINE)
    text = _re2.sub(r',\s*$', '', text.rstrip())  # virgule finale résiduelle
    # Nettoyer espaces multiples SUR CHAQUE LIGNE mais préserver les \n
    lines = text.split('\n')
    lines = [' '.join(l.split()) for l in lines]
    return '\n'.join(lines).strip()

def generate_dossier_pdf(d, comparables=[], mode="commercial"):
    # mode = "commercial" (acquéreur, FAI, sans comparables/estimation)
    #        "estimation"  (usage interne, Net Vendeur, toutes pages)
    d["_mode"] = mode
    buf = _BytesIO()
    cv  = _canvas.Canvas(buf, pagesize=_A4)
    cv.setTitle(f"Dossier — {d.get('reference','')}")
    _page1(cv, d);              cv.showPage()
    _page3(cv, d, agence_brief=True); cv.showPage()  # Quartier
    _page2(cv, d);              cv.showPage()  # Description + caractéristiques + prix
    _page_photos(cv, d);        cv.showPage()  # Photos pleine page
    if mode == "estimation":
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
            # Bail locatif
            "locataire":       data.get("locataire", ""),
            "loyer_annuel_ht": data.get("loyer_annuel_ht", 0),
            "loyer_initial_ht":data.get("loyer_initial_ht", 0),
            "evolution_loyer": data.get("evolution_loyer", ""),
            "duree_bail":      data.get("duree_bail", ""),
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

            # ── Étape 1 : Airtable 02_Loyers_Marche (source fiable et stable) ─
            if _at_pat and _pm2_ret2 == 0:
                try:
                    _at_base = "appscgBdxTzSPtOaZ"
                    _at_tbl  = "tblYEfE6WhP6mnlAf"
                    for _v_try in [_ville2, "Vannes"]:
                        # Recherche par champs Type + Ville (robuste, indépendant de la clé)
                        _filter = _ur_ws2.quote(
                            f"AND({{Type de bien}}=\"{_type2}\",{{Ville}}=\"{_v_try}\")"
                        )
                        _at_url = f"https://api.airtable.com/v0/{_at_base}/{_at_tbl}?filterByFormula={_filter}&maxRecords=1"
                        _at_req = _ur_ws2.Request(_at_url, headers={"Authorization": f"Bearer {_at_pat}"})
                        with _ur_ws2.urlopen(_at_req, timeout=10) as _at_res:
                            _at_data = _js_ws2.load(_at_res)
                        _at_recs = _at_data.get("records", [])
                        if _at_recs:
                            _af = _at_recs[0].get("fields", {})
                            _pm2_min2 = int(float(_af.get("Loyer min HT m2 an") or _af.get("fldWCZtVnGPDZatRD") or 0))
                            _pm2_max2 = int(float(_af.get("Loyer max HT m2 an") or _af.get("fldj9Fh1LzgAabMtV") or 0))
                            _pm2_ret2 = int(float(_af.get("Loyer median HT m2 an") or _af.get("fldbykZt4LePoeCqS") or 0))
                            _periode  = _af.get("Période") or _af.get("fldTk3fCIw1xfqjGk") or ""
                            if _pm2_ret2 > 0:
                                _ws_source = f"Référentiel Barbier Immobilier ({_v_try}{', ' + _periode if _periode else ''})"
                                break
                except Exception:
                    pass  # Airtable indisponible

            # ── Étape 2 : Web search si Airtable vide ────────────────────────────
            if _api2 and _surf2 > 0 and _pm2_ret2 == 0:
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
                    pass  # Web search indisponible

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
                        f"Tu es négociateur senior chez Barbier Immobilier (Vannes, Morbihan). "
                        f"Rédige une présentation commerciale {op} pour le dossier client.\n\n"
                        f"DONNÉES DU BIEN :\n"
                        f"- Type : {data.get('type_bien','')} | Surface : {data.get('surface','')} m²\n"
                        f"- Adresse : {data.get('adresse','')}, {data.get('ville','')} (Morbihan 56)\n"
                        f"- {val_info}\n"
                        f"- Informations disponibles : {notes_src[:1000]}\n\n"
                        "STRUCTURE ATTENDUE EN TEXTE PUR (3 paragraphes séparés par \n\n) :\n"
                        "§1 ACCROCHE — 1 phrase forte et spécifique. "
                        "Chiffre ou fait concret. Jamais générique.\n"
                        "§2 DESCRIPTION — 3-4 phrases. "
                        "Surface exacte, agencement, état, équipements clés, configuration. "
                        "Chiffres précis (m², capacité, CA si FDC, loyer/m²/an si locatif).\n"
                        "§3 ATOUTS & OPPORTUNITÉ — 2-3 phrases. "
                        "Emplacement concret (flux, visibilité, accès). "
                        "Argument décisif pour l'acquéreur ou le locataire.\n\n"
                        "RÈGLES ABSOLUES :\n"
                        "- Texte pur uniquement, aucune balise HTML, aucun markdown\n"
                        "- INTERDIT : 'idéalement situé', 'rare', 'opportunité unique', 'bel emplacement'\n"
                        "- Français impeccable, phrases courtes et rythmées\n"
                        "- 150-200 mots au total"
                    )
                    gpt_payload = _json.dumps({
                        "model": "gpt-4o",
                        "messages": [{"role": "user", "content": prompt_desc}],
                        "max_tokens": 500, "temperature": 0.68
                    }).encode()
                    gpt_req = _ur.Request("https://api.openai.com/v1/chat/completions",
                        data=gpt_payload, method="POST",
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
                    with _ur.urlopen(gpt_req, timeout=30) as gpt_res:
                        desc_enrichie = _json.load(gpt_res)["choices"][0]["message"]["content"].strip()
                    d["description"] = desc_enrichie
            except Exception:
                pass

        mode_doc = data.get("mode", "commercial")  # "commercial" ou "estimation"
        d["_mode"] = mode_doc
        pdf_bytes = generate_dossier_pdf(d, comparables, mode=mode_doc)
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

        prompt = (
            "Tu es expert en évaluation immobilière commerciale chez Barbier Immobilier (Vannes, Morbihan). "
            "Rédige un avis de valeur professionnel en HTML structuré.\n\n"
            "FORMAT OBLIGATOIRE :\n"
            "<h2>Avis de valeur — [type de bien], [ville]</h2>\n"
            "<h3>Synthèse</h3>\n"
            "<p>[4-5 phrases : présentation du bien, contexte marché Morbihan, adéquation offre/demande. "
            "Mentionner loyer ou prix au m² et comparer aux moyennes du secteur.]</p>\n"
            "<h3>Méthodologie</h3>\n"
            "<p>[3-4 phrases : méthode d'évaluation utilisée (comparables DVF, capitalisation si locatif, "
            "comparaison si vente). Sources : DVF data.gouv.fr, base transactions Barbier, connaissance terrain.]</p>\n"
            "<h3>Évaluation détaillée</h3>\n"
            "<p>[5-6 phrases : analyse de la valeur, facteurs positifs (emplacement, état, surface), "
            "facteurs de vigilance éventuels, comparaison DVF récentes si disponibles, "
            "conclusion sur le positionnement prix recommandé.]</p>\n"
            "<h3>Recommandations</h3>\n"
            "<p>[3-4 phrases : stratégie de prix, délai de commercialisation estimé, "
            "axes de valorisation. Ton expert, pas commercial.]</p>\n"
            "<p><strong>Barbier Immobilier — Expert immobilier commercial Morbihan — 02.97.47.11.11</strong></p>\n\n"
            "RÈGLES ABSOLUES :\n"
            "- Chiffres précis partout : €/m², rentabilité brute si locatif, ratio prix/marché\n"
            "- Ton professionnel et expert, jamais commercial ou vague\n"
            "- Uniquement les balises <h2>, <h3>, <p>, <strong>\n"
            "- Français impeccable, 300-400 mots au total\n\n"
            "DONNÉES DU BIEN :\n"
            f"Type : {type_b}\n"
            f"Adresse : {adresse}, {ville} (Morbihan, 56)\n"
            f"Surface : {surface} m²\n"
            + (f"Activité : {activite}\n" if activite else "")
            + f"{valeur_bien}\n"
            + (f"{loyer_m2}\n" if loyer_m2 else "")
            + (estim_bloc if estim_bloc else "")
            + (f"Données DVF : {dvf[:600]}\n" if dvf else "")
            + (f"Notes : {notes[:400]}\n" if notes else "")
        )

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
            import re as _re_av
            avis_txt = _re_av.sub(r"^```html\s*", "", avis_txt, flags=_re_av.IGNORECASE)
            avis_txt = _re_av.sub(r"```\s*$", "", avis_txt).strip()

        return jsonify({"avis": avis_txt})

    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/annonce", methods=["POST"])
def annonce():
    """
    Génère un texte d'annonce portail optimisé + PDF mise en page charte Barbier.
    Payload JSON : type_bien, adresse, ville, surface, prix, loyer_mensuel,
                   description_brute, activite, type_bail, statut_mandat, notes,
                   dpe, reference, photo_url, negociateur
    Retourne : {"annonce": "texte...", "pdf_b64": "...base64..."}
    """
    try:
        import os as _os_an
        import io as _io_an
        import base64 as _b64_an
        import re
        data = request.get_json(silent=True) or {}
        api_key = _os_an.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            return jsonify({"error": "OPENAI_API_KEY manquant"}), 500

        type_b   = data.get("type_bien", "")
        adresse  = data.get("adresse", "")
        ville    = data.get("ville", "Vannes")
        surface  = str(data.get("surface", ""))
        prix     = data.get("prix", "") or data.get("prix_retenu", "") or data.get("prix_de_vente", "")
        loyer    = data.get("loyer_mensuel", "")
        desc     = data.get("description_brute", "") or data.get("notes", "") or data.get("Description commerciale", "")
        activite = data.get("activite", "") or data.get("Activité", "")
        bail     = data.get("type_bail", "")
        mandat   = data.get("statut_mandat", "")
        nego     = data.get("negociateur", "Barbier Immobilier")
        dpe      = data.get("dpe", "") or data.get("DPE classe", "")
        reference= data.get("reference", "") or data.get("Référence Modelo", "")
        photo_url= data.get("photo_url", "") or data.get("Photo principale URL", "")
        charges  = data.get("charges_mensuelles", "")
        honoraires = data.get("honoraires", "")
        hon_charge = data.get("honoraires_charge", "")

        def fmt_v(v):
            try: return f"{int(float(str(v).replace(' ','').replace(',','.'))):,}".replace(",", " ") + " \u20ac"
            except: return str(v) if v else ""

        prix_str  = fmt_v(prix) if prix else ""
        loyer_str = fmt_v(loyer) + " HT/mois" if loyer else ""
        is_location = bool(loyer)
        operation   = "\u00c0 LOUER" if is_location else "\u00c0 VENDRE"
        val_affichee = loyer_str if is_location else prix_str

        # ── Prompt GPT amélioré ───────────────────────────────────────────
        prompt = (
            "Tu es directeur de la communication chez Barbier Immobilier, agence référente "
            "de l'immobilier commercial et professionnel dans le Morbihan (Vannes, Bretagne Sud). "
            "Tu rédiges des annonces pour les portails spécialisés : BureauxLocaux, Loopnet, SeLoger Pro.\n\n"
            "MISSION : Rédige une annonce structurée, éditoriale et percutante en HTML simple.\n\n"
            "FORMAT OBLIGATOIRE (respecte exactement cette structure) :\n"
            "<h2>[ACCROCHE FORTE — 1 phrase, 15 mots max, argument clé du bien]</h2>\n"
            "<p><strong>[Sous-titre : opération + type de bien + ville]</strong></p>\n"
            "<p>[Description du bien : surface, agencement, état, équipements. 2-3 phrases concrètes et précises.]</p>\n"
            "<p>[Atouts commerciaux : flux, visibilité, accès, environnement. 1-2 phrases. Aucune formule vague.]</p>\n"
            "<p>[Éléments financiers si pertinents : loyer/m²/an, rentabilité, charges. Omettre si non disponible.]</p>\n"
            "<p><strong>Contact : Barbier Immobilier — 02.97.47.11.11</strong></p>\n\n"
            "RÈGLES ABSOLUES :\n"
            "- Accroche h2 : percutante, jamais générique, chiffre ou fait concret si possible\n"
            "- Chiffres précis partout : m², €/m²/an, CA, nombre de couverts, etc.\n"
            "- INTERDIT : 'idéalement situé', 'rare sur le marché', 'bel emplacement', 'à saisir', 'opportunité unique'\n"
            "- 180-250 mots au total, phrases courtes et rythmées\n"
            "- Uniquement les balises <h2>, <p>, <strong> — rien d'autre\n"
            "- Français impeccable, ton professionnel et vendeur\n\n"
            "DONNÉES DU BIEN :\n"
            f"- Opération : {operation}\n"
            f"- Type : {type_b}\n"
            f"- Surface : {surface} m²\n"
            f"- Localisation : {adresse}, {ville} (Morbihan 56)\n"
            f"- Valeur : {val_affichee or 'Prix sur demande'}\n"
        )
        if activite: prompt += f"- Activité / destination : {activite}\n"
        if bail:     prompt += f"- Type de bail : {bail}\n"
        if charges:  prompt += f"- Charges : {fmt_v(charges)}/mois\n"
        if honoraires: prompt += f"- Honoraires : {fmt_v(honoraires)} ({hon_charge})\n"
        if dpe:      prompt += f"- DPE : {dpe}\n"
        if mandat:   prompt += f"- Mandat : {mandat}\n"
        if desc:     prompt += f"\nDESCRIPTIF BRUT (utilise ces infos, ne les recopie pas mot pour mot) :\n{desc[:2000]}\n"
        prompt += (
            "\nCONTRAINTES ABSOLUES :\n"
            "- 200-250 mots au total\n"
            "- Ton professionnel, direct, vendeur — jamais vague ni générique\n"
            "- Zéro formule creuse : interdit d'écrire 'idéalement situé', 'rare sur le marché', 'bel emplacement', 'à saisir'\n"
            "- Chiffres précis obligatoires (surface m², loyer €/m²/an, etc.)\n"
            "- Pas de hashtags, pas d'emojis, pas de puces\n"
            "- Français impeccable, phrases courtes et rythmées\n"
            "- Mettre en avant l'opportunité concrète pour l'acquéreur ou le locataire"
        )

        import json as _json_an, urllib.request as _ur_an
        gpt_payload = _json_an.dumps({
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 650,
            "temperature": 0.70
        }).encode()
        req_gpt = _ur_an.Request("https://api.openai.com/v1/chat/completions",
            data=gpt_payload, method="POST",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
        with _ur_an.urlopen(req_gpt, timeout=30) as res_gpt:
            annonce_txt = _json_an.load(res_gpt)["choices"][0]["message"]["content"].strip()
            # Nettoyer les backticks markdown si GPT en ajoute
            annonce_txt = re.sub(r"^```html\s*", "", annonce_txt, flags=re.IGNORECASE)
            annonce_txt = re.sub(r"```\s*$", "", annonce_txt).strip()

        # ── Génération PDF avec charte Barbier ───────────────────────────
        try:
            from reportlab.pdfgen import canvas as _cv_an
            from reportlab.lib.pagesizes import A4 as _A4_an
            from reportlab.lib import colors as _rc_an
            from reportlab.lib.utils import ImageReader as _IR_an
            from reportlab.platypus import Paragraph as _Para_an
            from reportlab.lib.styles import ParagraphStyle as _PS_an
            from reportlab.lib.enums import TA_LEFT as _TAL_an, TA_CENTER as _TAC_an, TA_JUSTIFY as _TAJ_an
            from reportlab.lib.units import mm as _mm_an

            TEAL   = _rc_an.HexColor("#16708B")
            ORANGE = _rc_an.HexColor("#F0795B")
            DARK   = _rc_an.HexColor("#1A2E3B")
            GRIS   = _rc_an.HexColor("#F4F5F7")
            WHITE  = _rc_an.white

            PW, PH = _A4_an
            ML = 20 * _mm_an
            MR = 20 * _mm_an
            CW = PW - ML - MR

            buf_an = _io_an.BytesIO()
            c = _cv_an.Canvas(buf_an, pagesize=_A4_an)

            # ── Bande header ──────────────────────────────────────────────
            c.setFillColor(TEAL)
            c.rect(0, PH - 14*_mm_an, PW, 14*_mm_an, fill=1, stroke=0)
            # Logo
            try:
                lh = 10 * _mm_an
                lw = lh * (488/662)
                logo_buf = _io_an.BytesIO(_b64_an.b64decode(LOGO_B64))
                c.drawImage(_IR_an(logo_buf), ML, PH - 12*_mm_an, width=lw, height=lh, mask='auto')
            except: pass
            c.setFillColor(WHITE)
            c.setFont("Helvetica-Bold", 9)
            c.drawRightString(PW - MR, PH - 7*_mm_an, f"ANNONCE — {operation} — {type_b.upper()}")
            c.setFont("Helvetica", 7.5)
            ref_txt = f"Réf. {reference}  ·  {ville}  ·  {nego}" if reference else f"{ville}  ·  {nego}"
            c.drawRightString(PW - MR, PH - 11*_mm_an, ref_txt)

            y = PH - 14*_mm_an - 8*_mm_an

            # ── Photo du bien ─────────────────────────────────────────────
            if photo_url:
                try:
                    photo_img = _fetch_photo_image(photo_url)
                    if photo_img:
                        ph_h = 65 * _mm_an
                        c.drawImage(photo_img, ML, y - ph_h, width=CW, height=ph_h,
                                    preserveAspectRatio=True, mask='auto')
                        y -= ph_h + 4*_mm_an
                except: pass

            # ── Badges info ───────────────────────────────────────────────
            badges = []
            if surface: badges.append(f"{surface} m²")
            if type_b:  badges.append(type_b)
            if ville:   badges.append(ville)
            if dpe:     badges.append(f"DPE {dpe}")

            bx = ML
            for badge in badges:
                bw = len(badge) * 5.5 + 12
                c.setFillColor(GRIS)
                c.roundRect(bx, y - 7*_mm_an, bw, 7*_mm_an, 3, fill=1, stroke=0)
                c.setFillColor(DARK)
                c.setFont("Helvetica-Bold", 8)
                c.drawString(bx + 6, y - 4.5*_mm_an, badge)
                bx += bw + 4

            y -= 10*_mm_an

            # ── Prix mis en évidence ──────────────────────────────────────
            if val_affichee:
                c.setFillColor(ORANGE)
                c.roundRect(ML, y - 12*_mm_an, CW, 12*_mm_an, 4, fill=1, stroke=0)
                c.setFillColor(WHITE)
                c.setFont("Helvetica-Bold", 18)
                c.drawCentredString(PW/2, y - 8.5*_mm_an, val_affichee)
                if honoraires and hon_charge:
                    c.setFont("Helvetica", 8)
                    c.drawCentredString(PW/2, y - 11.5*_mm_an,
                        f"+ {fmt_v(honoraires)} honoraires ({hon_charge})")
                y -= 15*_mm_an

            # ── Texte annonce ─────────────────────────────────────────────
            c.line(ML, y, ML + CW, y)
            y -= 5*_mm_an

            blocs = annonce_txt.split("\n\n")
            for i_b, bloc in enumerate(blocs):
                bloc = bloc.strip()
                if not bloc: continue
                # Premier bloc = accroche en gras teal
                if i_b == 0:
                    style = _PS_an("accroche", fontName="Helvetica-Bold", fontSize=12,
                                   leading=16, textColor=TEAL, alignment=_TAL_an, spaceAfter=6)
                else:
                    style = _PS_an("corps", fontName="Helvetica", fontSize=9.5,
                                   leading=14.5, textColor=DARK, alignment=_TAJ_an, spaceAfter=8)
                p = _Para_an(bloc, style)
                aw, ah = p.wrap(CW, 9999)
                p.drawOn(c, ML, y - ah)
                y -= ah + 8

            # ── Adresse ───────────────────────────────────────────────────
            y -= 4*_mm_an
            c.setFillColor(GRIS)
            c.roundRect(ML, y - 8*_mm_an, CW, 8*_mm_an, 3, fill=1, stroke=0)
            c.setFillColor(TEAL)
            c.setFont("Helvetica-Bold", 8.5)
            c.drawString(ML + 6, y - 5*_mm_an, f"📍  {adresse}  —  {ville} (Morbihan)")
            y -= 11*_mm_an

            # ── Footer ────────────────────────────────────────────────────
            c.setFillColor(TEAL)
            c.rect(0, 0, PW, 10*_mm_an, fill=1, stroke=0)
            c.setFillColor(WHITE)
            c.setFont("Helvetica-Bold", 8.5)
            c.drawCentredString(PW/2, 6*_mm_an, "barbier immobilier  ·  02.97.47.11.11  ·  contact@barbierimmobilier.com")
            c.setFont("Helvetica", 7)
            c.drawCentredString(PW/2, 3*_mm_an, "2 place Albert Einstein  —  56000 Vannes  ·  barbierimmobilier.com")

            c.save()
            buf_an.seek(0)
            pdf_b64 = _b64_an.b64encode(buf_an.read()).decode()
        except Exception as e_pdf:
            pdf_b64 = None

        result = {"annonce": annonce_txt, "negociateur": nego}
        if pdf_b64:
            result["pdf_b64"] = pdf_b64
        return jsonify(result)

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







@app.route("/mandat", methods=["POST"])
def generer_mandat():
    import io as _io_m, re as _re_m
    from datetime import datetime as _dt_m
    from reportlab.pdfgen import canvas as _cv
    from reportlab.lib.pagesizes import A4 as _A4
    from reportlab.lib import colors as _rc
    from reportlab.lib.utils import ImageReader as _IR
    from reportlab.platypus import Paragraph as _Para, Frame as _Frame
    from reportlab.lib.styles import ParagraphStyle as _PS
    from reportlab.lib.enums import TA_LEFT as _TAL, TA_CENTER as _TAC, TA_JUSTIFY as _TAJ, TA_RIGHT as _TAR
    from reportlab.lib.units import mm as _mm

    data = request.get_json(silent=True) or {}

    MAND = {
        "societe": "RESOLIMMO", "forme": "SARL", "capital": "10 000",
        "adresse": "2 place Albert Einstein", "cp": "56000", "ville": "Vannes",
        "tel": "+33 2 97 47 11 11", "email": "contact@barbierimmobilier.com",
        "rcs": "RCS Vannes n\u00b0 833871585", "tva": "TVA FR93833871585",
        "cpi": "CPI n\u00b0 5605 2018 000 027 030", "cpi_delivre": "Morbihan",
        "rcpro": "MMA Entreprise \u2014 14 boulevard Albert Einstein Alexandre Oyon, Le Mans",
        "garantie": "Galian \u2014 89 rue de la Bo\u00e9tie, Vannes \u2014 n\u00b0 A11060563",
        "sequestre": "CIC Vannes \u2014 n\u00b0 00021578201",
    }
    NEGO_MAP = {
        "Marina": "Marina LE PALLEC", "Ma\u00efwen": "Ma\u00efwen LE GALL",
        "Am\u00e9lie": "Am\u00e9lie PATARD", "Sophie": "Sophie NICOL",
        "Marie": "Marie GAUTHERET", "Laurent": "Laurent BARADU",
    }
    MOIS = ["janvier","f\u00e9vrier","mars","avril","mai","juin",
            "juillet","ao\u00fbt","septembre","octobre","novembre","d\u00e9cembre"]

    def fmt_date(s):
        try:
            dt = _dt_m.strptime(str(s).strip(), "%Y-%m-%d")
            return f"{dt.day} {MOIS[dt.month-1]} {dt.year}"
        except:
            return str(s)

    def fmt_prix(v):
        try: return f"{int(float(str(v).replace(' ','').replace(',','.'))):,}".replace(",", " ") + " \u20ac"
        except: return str(v) if v else "\u2014"

    def clean_desc(s):
        """Nettoie la description : retire les // et dupliqués de prix"""
        if not s: return ""
        s = _re_m.sub(r'\s*//\s*', '\n', s)
        s = _re_m.sub(r'#\w[\w-]*', '', s)
        s = _re_m.sub(r'&nbsp;', '', s)
        s = _re_m.sub(r' +', ' ', s)
        return s.strip()

    num_mandat   = data.get("num_mandat", "____")
    date_sig_raw = data.get("date_signature", _dt_m.today().strftime("%Y-%m-%d"))
    date_sig     = fmt_date(date_sig_raw)
    type_mandat  = data.get("type_mandat", "Simple")
    duree_mois   = data.get("duree_mois", 12)
    negociatrice = NEGO_MAP.get(data.get("negociatrice", ""), data.get("negociatrice", "Marina LE PALLEC"))
    mandant_type    = data.get("mandant_type", "physique")
    mandant_nom     = data.get("mandant_nom", "").strip()
    mandant_adresse = data.get("mandant_adresse", "").strip()
    mandant_cp      = data.get("mandant_cp", "").strip()
    mandant_ville   = data.get("mandant_ville", "").strip()
    mandant_societe = data.get("mandant_societe", "").strip()
    mandant_siren   = data.get("mandant_siren", "").strip()
    mandant_forme   = data.get("mandant_forme", "").strip()
    mandant_capital = data.get("mandant_capital", "").strip()
    mandant_repr    = data.get("mandant_representant", "").strip()
    bien_adresse    = data.get("bien_adresse", "").strip()
    bien_desc       = clean_desc(data.get("bien_description", ""))[:600]
    bien_occup      = data.get("bien_occupation", "Libre")
    prix_net        = fmt_prix(data.get("prix_net_vendeur", ""))
    prix_vente      = fmt_prix(data.get("prix_de_vente", ""))
    honoraires      = fmt_prix(data.get("honoraires", ""))
    hon_charge      = data.get("honoraires_charge", "Acqu\u00e9reur")

    # ── Couleurs charte ───────────────────────────────────────────────────
    TEAL   = _rc.HexColor("#16708B")
    ORANGE = _rc.HexColor("#F0795B")
    DARK   = _rc.HexColor("#1A2E3B")
    GRIS   = _rc.HexColor("#F0F4F6")
    GRIS2  = _rc.HexColor("#D8E2E8")
    WHITE  = _rc.white

    PW, PH = _A4
    ML = 22*_mm; MR = 22*_mm; CW = PW - ML - MR
    FOOTER_H = 12*_mm
    HEADER_H = 14*_mm

    buf = _io_m.BytesIO()
    c = _cv.Canvas(buf, pagesize=_A4)
    page_num = [1]

    def draw_header(cv):
        cv.setFillColor(TEAL)
        cv.rect(0, PH - HEADER_H, PW, HEADER_H, fill=1, stroke=0)
        try:
            lh = 9*_mm; lw = lh*(488/662)
            lb = _io_m.BytesIO(base64.b64decode(LOGO_B64))
            cv.drawImage(_IR(lb), ML, PH - HEADER_H + 2.5*_mm, width=lw, height=lh, mask='auto')
        except: pass
        cv.setFillColor(WHITE)
        cv.setFont("Helvetica-Bold", 8.5)
        cv.drawRightString(PW - MR, PH - 6*_mm, f"MANDAT {type_mandat.upper()} DE VENTE N\u00b0 {num_mandat}")
        cv.setFont("Helvetica", 7)
        cv.drawRightString(PW - MR, PH - 10*_mm, "barbierimmobilier.com  \u00b7  +33 2 97 47 11 11")

    def draw_footer(cv, pg):
        cv.setFillColor(TEAL)
        cv.rect(0, 0, PW, FOOTER_H, fill=1, stroke=0)
        cv.setFillColor(WHITE)
        cv.setFont("Helvetica", 7)
        cv.drawCentredString(PW/2, FOOTER_H - 5*_mm,
            f"Paraphes — Page {pg}  \u00b7  Fait \u00e0 Vannes, le {date_sig}  \u00b7  1 exemplaire pour chaque partie")
        cv.setFont("Helvetica", 6.5)
        cv.drawCentredString(PW/2, FOOTER_H - 9*_mm, "barbier immobilier  \u00b7  2 place Albert Einstein, 56000 Vannes  \u00b7  barbierimmobilier.com")

    def new_page(cv):
        draw_footer(cv, page_num[0])
        cv.showPage()
        page_num[0] += 1
        draw_header(cv)

    def hline(cv, x, y, w, col=TEAL, t=0.5):
        cv.setStrokeColor(col); cv.setLineWidth(t)
        cv.line(x, y, x+w, y)

    def sec_box(cv, x, y, w, txt):
        """Boîte de section teal — retourne y après la boîte"""
        bh = 7.5*_mm
        cv.setFillColor(TEAL)
        cv.roundRect(x, y - bh, w, bh, 2, fill=1, stroke=0)
        cv.setFillColor(WHITE)
        cv.setFont("Helvetica-Bold", 8.5)
        cv.drawString(x + 6, y - bh + 2.5*_mm, txt)
        return y - bh - 4*_mm

    def draw_para(cv, txt, x, y, w, font="Helvetica", size=8.5, leading=13.5,
                  color=DARK, align=_TAJ, bold_font="Helvetica-Bold"):
        """Dessine un paragraphe et retourne le y après le texte."""
        if not txt: return y
        style = _PS("p", fontName=font, fontSize=size, leading=leading,
                    textColor=color, alignment=align)
        p = _Para(txt, style)
        aw, ah = p.wrap(w, 9999)
        if y - ah < FOOTER_H + 15*_mm:
            new_page(cv)
            y = PH - HEADER_H - 12*_mm
        p.drawOn(cv, x, y - ah)
        return y - ah

    def kv(cv, x, y, label, value):
        cv.setFont("Helvetica-Bold", 8.5)
        cv.setFillColor(DARK)
        cv.drawString(x, y, label)
        cv.setFont("Helvetica", 8.5)
        cv.setFillColor(_rc.HexColor("#3A3A4A"))
        cv.drawString(x + 52*_mm, y, value)
        return y - 6*_mm

    def mandant_box(cv, x, y, w):
        """Bloc mandant — retourne y après le bloc"""
        lines = []
        if mandant_type == "morale":
            lines.append(("bold", mandant_societe))
            sub = mandant_forme
            if mandant_capital: sub += f" \u2014 Capital {mandant_capital} \u20ac"
            if mandant_siren: sub += f" \u2014 SIREN {mandant_siren}"
            lines.append(("normal", sub))
            if mandant_repr: lines.append(("normal", f"Repr\u00e9sentant : {mandant_repr}"))
            if mandant_adresse: lines.append(("normal", f"{mandant_adresse}, {mandant_cp} {mandant_ville}"))
            if mandant_nom: lines.append(("normal", f"Contact : {mandant_nom}"))
        else:
            lines.append(("bold", mandant_nom))
            if mandant_adresse: lines.append(("normal", f"{mandant_adresse}, {mandant_cp} {mandant_ville}"))

        n_lines = len(lines)
        bh = (n_lines * 6 + 16) * _mm / 3.78 + 14
        bh = max(bh, 20*_mm)
        cv.setFillColor(GRIS)
        cv.roundRect(x, y - bh, w, bh, 3, fill=1, stroke=0)
        cv.setFillColor(TEAL)
        cv.setFont("Helvetica-Bold", 7.5)
        cv.drawString(x + 5, y - 5*_mm, "LE MANDANT")
        ty = y - 9.5*_mm
        for style, txt in lines:
            if style == "bold":
                cv.setFont("Helvetica-Bold", 9.5)
                cv.setFillColor(DARK)
            else:
                cv.setFont("Helvetica", 8.5)
                cv.setFillColor(_rc.HexColor("#3A3A4A"))
            cv.drawString(x + 5, ty, txt)
            ty -= 5.5*_mm
        cv.setFillColor(DARK)
        cv.setFont("Helvetica-Oblique", 7.5)
        cv.drawString(x + 5, y - bh + 3*_mm, "Ci-apr\u00e8s \u00ab le MANDANT \u00bb, D'UNE PART,")
        return y - bh - 5*_mm

    def mandataire_box(cv, x, y, w):
        """Bloc mandataire — retourne y après le bloc"""
        lines_m = [
            ("bold", f"barbier immobilier \u2014 {MAND['societe']} ({MAND['forme']}, capital {MAND['capital']} \u20ac)"),
            ("normal", f"{MAND['adresse']}, {MAND['cp']} {MAND['ville']}  \u00b7  {MAND['tel']}  \u00b7  {MAND['email']}"),
            ("normal", f"{MAND['rcs']}  \u00b7  {MAND['tva']}"),
            ("normal", f"Carte pro : {MAND['cpi']} d\u00e9livr\u00e9e par {MAND['cpi_delivre']}"),
            ("normal", f"RC Pro : {MAND['rcpro']}"),
            ("normal", f"Garantie : {MAND['garantie']}"),
            ("normal", f"S\u00e9questre : {MAND['sequestre']}"),
            ("bold_teal", f"Repr\u00e9sent\u00e9e par {negociatrice}, salari\u00e9(e) habilit\u00e9(e)"),
        ]
        bh = len(lines_m) * 5.2*_mm + 14*_mm
        cv.setFillColor(_rc.HexColor("#EAF3F7"))
        cv.roundRect(x, y - bh, w, bh, 3, fill=1, stroke=0)
        cv.setFillColor(TEAL)
        cv.setFont("Helvetica-Bold", 7.5)
        cv.drawString(x + 5, y - 5*_mm, "LE MANDATAIRE \u2014 barbier immobilier")
        ty = y - 9.5*_mm
        for style, txt in lines_m:
            if style == "bold":
                cv.setFont("Helvetica-Bold", 8.5)
                cv.setFillColor(DARK)
            elif style == "bold_teal":
                cv.setFont("Helvetica-Bold", 8.5)
                cv.setFillColor(TEAL)
            else:
                cv.setFont("Helvetica", 8)
                cv.setFillColor(_rc.HexColor("#3A3A4A"))
            cv.drawString(x + 5, ty, txt)
            ty -= 5.2*_mm
        cv.setFillColor(DARK)
        cv.setFont("Helvetica-Oblique", 7.5)
        cv.drawString(x + 5, y - bh + 3*_mm, "Ci-apr\u00e8s \u00ab le MANDATAIRE \u00bb, D'AUTRE PART,")
        return y - bh - 5*_mm

    # ── PAGE 1 ────────────────────────────────────────────────────────────
    draw_header(c)
    y = PH - HEADER_H - 10*_mm

    # Titre
    c.setFillColor(DARK)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(ML, y, f"MANDAT {type_mandat.upper()} DE VENTE")
    y -= 7*_mm
    c.setFillColor(TEAL)
    c.setFont("Helvetica", 9)
    c.drawString(ML, y, f"N\u00b0\u00a0{num_mandat}  \u00b7  Sign\u00e9 le {date_sig}  \u00b7  Dur\u00e9e\u00a0:\u00a0{duree_mois}\u00a0mois")
    y -= 3*_mm
    c.setStrokeColor(ORANGE); c.setLineWidth(2.5)
    c.line(ML, y, ML + CW, y)
    y -= 9*_mm

    # Entre les soussignés
    c.setFillColor(DARK)
    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(ML, y, "ENTRE LES SOUSSIGN\u00c9S")
    y -= 3*_mm
    hline(c, ML, y, CW, TEAL, 0.5)
    y -= 7*_mm

    y = mandant_box(c, ML, y, CW)
    y = mandataire_box(c, ML, y, CW)
    y -= 2*_mm

    # Il a été convenu
    c.setFillColor(DARK)
    c.setFont("Helvetica-BoldOblique", 9)
    c.drawString(ML, y, "Il a \u00e9t\u00e9 convenu et arr\u00eat\u00e9 ce qui suit")
    hline(c, ML, y - 2*_mm, CW, ORANGE, 1)
    y -= 8*_mm
    y = draw_para(c,
        f"Par les pr\u00e9sentes, <b>le MANDANT conf\u00e8re au MANDATAIRE, qui l'accepte, le mandat {type_mandat.lower()} "
        f"de vendre le bien ci-apr\u00e8s d\u00e9sign\u00e9 aux prix, charges et conditions convenus ci-dessous.</b>",
        ML, y, CW)
    y -= 8*_mm

    # ── DÉSIGNATION DU BIEN ───────────────────────────────────────────────
    y = sec_box(c, ML, y, CW, "D\u00c9SIGNATION DU BIEN \u00c0 VENDRE")
    y = kv(c, ML, y, "Adresse :", bien_adresse)
    y -= 2*_mm
    if bien_desc:
        c.setFont("Helvetica-Bold", 8.5)
        c.setFillColor(DARK)
        c.drawString(ML, y, "Description :")
        y -= 5*_mm
        y = draw_para(c, bien_desc, ML + 4, y, CW - 4, size=8.5)
        y -= 2*_mm
    y = kv(c, ML, y, "\u00c9tat d'occupation :", bien_occup)
    y -= 6*_mm

    # ── PRIX & HONORAIRES ─────────────────────────────────────────────────
    if y < FOOTER_H + 55*_mm:
        new_page(c); y = PH - HEADER_H - 12*_mm

    y = sec_box(c, ML, y, CW, "PRIX DE VENTE \u2014 HONORAIRES DU MANDATAIRE")

    # Tableau prix
    col_w = (CW - 4) / 3
    rows = [
        ("Prix net vendeur", prix_net),
        ("Prix de vente TTC", prix_vente),
        (f"Honoraires HT ({hon_charge})", honoraires),
    ]
    for label, val in rows:
        c.setFillColor(GRIS)
        c.roundRect(ML, y - 7*_mm, CW, 7*_mm, 2, fill=1, stroke=0)
        c.setFillColor(DARK)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(ML + 5, y - 4.5*_mm, label)
        c.setFont("Helvetica-Bold", 10)
        c.setFillColor(TEAL)
        c.drawRightString(ML + CW - 5, y - 4.5*_mm, val)
        y -= 8*_mm
    y -= 2*_mm
    y = draw_para(c,
        "<b>Le prix sera r\u00e9gl\u00e9 comptant au plus tard le jour de la signature de l\u2019acte d\u00e9finitif de vente.</b> "
        "Le MANDANT est inform\u00e9 qu\u2019il pourra le cas \u00e9ch\u00e9ant \u00eatre assujetti \u00e0 l\u2019imp\u00f4t sur les plus-values immobili\u00e8res.",
        ML, y, CW, size=8.5)
    y -= 8*_mm

    # ── DURÉE ─────────────────────────────────────────────────────────────
    if y < FOOTER_H + 40*_mm:
        new_page(c); y = PH - HEADER_H - 12*_mm

    y = sec_box(c, ML, y, CW, "DUR\u00c9E DU MANDAT")
    y = draw_para(c,
        f"Le pr\u00e9sent mandat est consenti pour une dur\u00e9e de <b>{duree_mois} mois</b> "
        f"\u00e0 compter de sa signature, soit \u00e0 partir du <b>{date_sig}</b>. "
        "Il se renouvelle par tacite reconduction par p\u00e9riodes d\u2019un mois, "
        "sauf d\u00e9nonciation par l\u2019une des parties par lettre recommand\u00e9e avec accus\u00e9 de r\u00e9ception, "
        "adress\u00e9e au moins 15 jours avant l\u2019expiration de la p\u00e9riode en cours.",
        ML, y, CW, size=8.5)
    y -= 8*_mm

    # ── PAGE 2 — CONDITIONS GÉNÉRALES ─────────────────────────────────────
    draw_footer(c, page_num[0])
    c.showPage()
    page_num[0] += 1
    draw_header(c)
    y = PH - HEADER_H - 12*_mm

    y = sec_box(c, ML, y, CW, "CONDITIONS G\u00c9N\u00c9RALES DU MANDAT")
    y = draw_para(c,
        "Le MANDANT d\u00e9clare avoir la capacit\u00e9 juridique de disposer desdits biens et ne faire l\u2019objet "
        "d\u2019aucune mesure restreignant sa capacit\u00e9 \u00e0 agir (tutelle, curatelle, etc.).",
        ML, y, CW, size=8.5)
    y -= 4*_mm
    y = draw_para(c,
        "Le MANDANT s\u2019engage \u00e0 remettre au MANDATAIRE dans les 8 jours suivant la signature du pr\u00e9sent mandat "
        "tous les documents n\u00e9cessaires \u00e0 l\u2019ex\u00e9cution de son mandat : titre de propri\u00e9t\u00e9, diagnostics, "
        "certificats et justificatifs rendus obligatoires.",
        ML, y, CW, size=8.5)
    y -= 4*_mm
    y = draw_para(c,
        "Le MANDANT s\u2019interdit, pendant la dur\u00e9e du mandat et durant les 12 mois suivant sa r\u00e9vocation ou son expiration, "
        "de traiter directement ou indirectement avec une personne pr\u00e9sent\u00e9e par le MANDATAIRE. "
        "<b>En cas de manquement, le MANDANT s\u2019oblige \u00e0 verser au MANDATAIRE une indemnit\u00e9 "
        "\u00e9gale au montant total TTC de la r\u00e9mun\u00e9ration pr\u00e9vue, \u00e0 titre d\u2019indemnit\u00e9 forfaitaire et d\u00e9finitive.</b>",
        ML, y, CW, size=8.5)
    y -= 8*_mm

    y = sec_box(c, ML, y, CW, "ACTIONS COMMERCIALES DU MANDATAIRE")
    y = draw_para(c,
        "Le MANDATAIRE s\u2019engage \u00e0 r\u00e9aliser \u00e0 ses frais les actions de communication suivantes : "
        "diffusion sur les portails immobiliers professionnels sp\u00e9cialis\u00e9s, pr\u00e9sentation aux acquitteurs "
        "du r\u00e9seau Barbier Immobilier, communication digitale et print adapt\u00e9e au bien.",
        ML, y, CW, size=8.5)
    y -= 8*_mm

    y = sec_box(c, ML, y, CW, "INFORMATIONS TRACFIN")
    y = draw_para(c,
        "Le MANDATAIRE informe le MANDANT qu\u2019il est tenu de se conformer aux dispositions de l\u2019article L.\u00a0562-1 "
        "du code mon\u00e9taire et financier, relatives au traitement du renseignement et \u00e0 l\u2019action contre "
        "les circuits financiers clandestins d\u00e9di\u00e9es \u00e0 la lutte contre le blanchiment d\u2019argent.",
        ML, y, CW, size=8.5)
    y -= 8*_mm

    y = sec_box(c, ML, y, CW, "ENGAGEMENT DE NON-DISCRIMINATION")
    y = draw_para(c,
        "Les parties prennent l\u2019engagement expr\u00e8s de n\u2019opposer \u00e0 aucun candidat \u00e0 l\u2019acquisition "
        "des pr\u00e9sents biens un refus fond\u00e9 sur un motif discriminatoire au sens de la l\u00e9gislation en vigueur.",
        ML, y, CW, size=8.5)
    y -= 8*_mm

    y = sec_box(c, ML, y, CW, "DONN\u00c9ES PERSONNELLES")
    y = draw_para(c,
        "Le MANDANT est inform\u00e9 que les donn\u00e9es \u00e0 caract\u00e8re personnel le concernant seront trait\u00e9es "
        "pour l\u2019ex\u00e9cution du contrat et conserv\u00e9es pendant la dur\u00e9e l\u00e9gale applicable. "
        "\u2610\u00a0<b>En cochant cette case, le MANDANT accepte express\u00e9ment le traitement de ses donn\u00e9es.</b>",
        ML, y, CW, size=8.5)
    y -= 8*_mm

    y = sec_box(c, ML, y, CW, "\u00c9LECTION DE DOMICILE")
    y = draw_para(c,
        "Les parties soussign\u00e9es font \u00e9lection de domicile chacune \u00e0 leur adresse respective "
        "stipul\u00e9e en t\u00eate du pr\u00e9sent mandat.",
        ML, y, CW, size=8.5)
    y -= 12*_mm

    # ── SIGNATURES ─────────────────────────────────────────────────────────
    # On les colle en bas de page 2, en les forçant à rester ensemble
    SIG_H = 38*_mm
    if y < FOOTER_H + SIG_H + 10*_mm:
        draw_footer(c, page_num[0])
        c.showPage()
        page_num[0] += 1
        draw_header(c)
        y = PH - HEADER_H - 20*_mm

    hline(c, ML, y, CW, GRIS2, 1)
    y -= 8*_mm

    sig_w = (CW - 8*_mm) / 2

    # Bloc mandant
    c.setFillColor(GRIS)
    c.roundRect(ML, y - SIG_H, sig_w, SIG_H, 3, fill=1, stroke=0)
    c.setFillColor(TEAL)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(ML + 5, y - 6*_mm, "LE MANDANT")
    c.setFont("Helvetica-Bold", 9.5)
    c.setFillColor(DARK)
    c.drawString(ML + 5, y - 12*_mm, mandant_nom or mandant_societe)
    if mandant_adresse:
        c.setFont("Helvetica", 8)
        c.setFillColor(_rc.HexColor("#3A3A4A"))
        c.drawString(ML + 5, y - 17*_mm, f"{mandant_adresse}, {mandant_cp} {mandant_ville}")
    c.setFillColor(_rc.HexColor("#888888"))
    c.setFont("Helvetica", 7.5)
    c.drawString(ML + 5, y - SIG_H + 6*_mm, "Lu et approuv\u00e9 \u2014 Signature :")

    # Bloc mandataire
    bx2 = ML + sig_w + 8*_mm
    c.setFillColor(_rc.HexColor("#EAF3F7"))
    c.roundRect(bx2, y - SIG_H, sig_w, SIG_H, 3, fill=1, stroke=0)
    c.setFillColor(TEAL)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(bx2 + 5, y - 6*_mm, "LE MANDATAIRE")
    c.setFont("Helvetica-Bold", 9.5)
    c.setFillColor(DARK)
    c.drawString(bx2 + 5, y - 12*_mm, "barbier immobilier")
    c.setFont("Helvetica", 8.5)
    c.setFillColor(TEAL)
    c.drawString(bx2 + 5, y - 17*_mm, negociatrice)
    c.setFillColor(_rc.HexColor("#888888"))
    c.setFont("Helvetica", 7.5)
    c.drawString(bx2 + 5, y - SIG_H + 6*_mm, "Signature :")

    draw_footer(c, page_num[0])
    c.save()
    buf.seek(0)
    from flask import send_file as _sfm
    fname = f"Mandat_{type_mandat}_{num_mandat}.pdf"
    return _sfm(buf, mimetype="application/pdf", as_attachment=True, download_name=fname)

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

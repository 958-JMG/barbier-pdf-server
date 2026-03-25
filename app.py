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
                           leading=leading, textColor=color)
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
    return jsonify({"service": "Barbier PDF Generator", "status": "ok", "version": "4.2"})


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
                    row[key] = float(str(val).replace(' ', '').replace(' ', '').replace(' ', ''))
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
    """Logo petit cartouche blanc haut droite — commun à toutes les pages."""
    try:
        w = 18*_mm; ratio = 662/488; h = w*ratio
        pad = 2*_mm
        x = _W - 14*_mm - w; y = _H - 11*_mm - h - pad
        c.setFillColor(_BLANC)
        c.roundRect(x-pad, y-pad, w+2*pad, h+2*pad, 2*_mm, fill=1, stroke=0)
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

def _fetch_cadastre_image(ref_cadastrale, adresse="", ville=""):
    """
    Récupère une image du plan cadastral via IGN WMTS tiles + apicarto pour centrage.
    ref_cadastrale : ex "56034 AM 0355" ou "56034AM0355"
    Retourne une PIL.Image ou None.
    """
    import math as _m2, re as _re2
    try:
        # Parser la référence cadastrale
        ref_clean = ref_cadastrale.replace(" ","").upper()
        # Format : 56034AM0355
        m = _re2.match(r"(\d{5})([A-Z]{2})(\d{3,4})", ref_clean)
        if not m:
            return None
        code_insee, section, numero = m.group(1), m.group(2), m.group(3).zfill(4)

        # 1. Obtenir les coordonnées du centre de la parcelle via apicarto
        api_url = f"https://apicarto.ign.fr/api/cadastre/parcelle?code_insee={code_insee}&section={section}&numero={numero}"
        req = _ur.Request(api_url, headers={"User-Agent": "BarbierImmo/1.0"})
        with _ur.urlopen(req, timeout=10) as r:
            data = _json.load(r)

        features = data.get("features", [])
        if not features:
            return None

        coords = features[0]["geometry"]["coordinates"][0][0]
        lons = [c[0] for c in coords]; lats = [c[1] for c in coords]
        lat = sum(lats)/len(lats); lon = sum(lons)/len(lons)

        # 2. Récupérer les tiles IGN plan cadastral (WMTS PM)
        zoom = 19
        n = 2**zoom
        cx = int((lon+180)/360*n)
        cy = int((1 - _m2.log(_m2.tan(_m2.radians(lat))+1/_m2.cos(_m2.radians(lat)))/_m2.pi)/2*n)

        tiles_grid = 3
        rows = []
        for row in range(tiles_grid):
            ri = []
            for col in range(tiles_grid):
                tx = cx - tiles_grid//2 + col
                ty = cy - tiles_grid//2 + row
                tile_url = (
                    f"https://data.geopf.fr/wmts?SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile"
                    f"&LAYER=CADASTRALPARCELS.PARCELLAIRE_EXPRESS&STYLE=normal"
                    f"&FORMAT=image/png&TILEMATRIXSET=PM"
                    f"&TILEMATRIX={zoom}&TILEROW={ty}&TILECOL={tx}"
                )
                req2 = _ur.Request(tile_url, headers={"User-Agent": "BarbierImmo/1.0"})
                with _ur.urlopen(req2, timeout=10) as r2:
                    tile = _PILImage.open(_BytesIO(r2.read())).convert("RGB")
                ri.append(tile)
            rows.append(ri)

        tw, th = 256, 256
        result = _PILImage.new("RGB", (tw*tiles_grid, th*tiles_grid), (255,255,255))
        for row in range(tiles_grid):
            for col in range(tiles_grid):
                result.paste(rows[row][col], (col*tw, row*th))

        # Ajouter marqueur orange au centre
        from PIL import ImageDraw as _ID
        draw = _ID.Draw(result)
        cx_img = tw*tiles_grid//2; cy_img = th*tiles_grid//2
        r_marker = 10
        draw.ellipse([cx_img-r_marker, cy_img-r_marker, cx_img+r_marker, cy_img+r_marker],
                     fill=(240,121,91), outline=(255,255,255), width=3)

        # Recadrer sur la zone utile (crop central 70%)
        w, h = result.size
        margin_x = int(w * 0.15); margin_y = int(h * 0.15)
        result = result.crop((margin_x, margin_y, w-margin_x, h-margin_y))

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
    c.setFillColor(_BLEU); c.rect(0, _H*0.48, _W, _H*0.52, fill=1, stroke=0)
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
    # Location : loyer_mensuel présent (même si prix aussi renseigné)
    is_location = bool(loyer_m)
    if is_location and not prix:
        prix = 0  # on n'affiche pas le prix de vente sur une location
    if is_location:
        val_affiche  = loyer_m
        label_prix   = "LOYER MENSUEL HT"
        suffix_val   = "HT/mois"
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
                c.drawString(14*_mm, _H-98*_mm, f"soit {int(pm2_an):,} € HT/m²/an".replace(",", "\u202f"))
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
    c.setFillColor(_BLANC); c.rect(0, 0, _W, _H*0.48, fill=1, stroke=0)
    ph = _H*0.48-22*_mm
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
    # Logo en overlay au-dessus de tout (bandeau bleu + photo)
    _logo(c, _W-52*_mm, _H-18*_mm, w=34*_mm)
    c.setFillColor(_GTEXTE); c.setFont("Helvetica", 7.5)
    c.drawString(14*_mm, 13*_mm, f"Dossier préparé par  {_safe(d.get('negociateur'),'Barbier Immobilier')}  ·  Réf. {_safe(d.get('reference'))}")
    _footer(c, 1)

def _page2(c, d):
    _header(c, f"{_safe(d.get('type_bien'))} — {_safe(d.get('adresse'))}, {_safe(d.get('ville'))}")
    _sec(c, "Présentation du bien", 14*_mm, _H-32*_mm)
    desc = _safe(d.get("description"), "Description non disponible.")
    p = _Para(desc, _PS("b", fontName="Helvetica", fontSize=9.5, textColor=_GTEXTE, leading=15))
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
    # Compléter avec valeurs par défaut si API indisponible
    defaults = [
        ("Parking",      "Stationnement disponible", "#1B3A5C"),
        ("Transport",    "Gare SNCF / Bus",          "#0D5570"),
        ("Restauration", "Cafés & restaurants",      "#E8472A"),
        ("Commerce",     "Services de proximite",    "#3A1B5C"),
        ("Formation",    "Etablissements proches",   "#5C3A1B"),
        ("Dynamisme",    "Zone active",              "#1B5C3A"),
    ]
    for d_lbl, d_val, d_col in defaults:
        if len(results) >= 6: break
        if not any(r[0] == d_lbl for r in results):
            results.append((d_lbl, d_val, d_col))
    return results[:6]


# ── PICTO DYNAMIQUE (base64) ──────────────────────────────────────────────────
_PICTO_DYNAMIQUE_B64 = "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAEbUlEQVR4nK2WW4iVVRTHf+ucY9aUWTnmZIZmhdENQ4wuppToqGiQZvXQzegC2UO9FUX20kNGaWVFdlVLBAkRswuGNGbZQ2n5YNhAkgliRCjalJc5vx6+9TWfJ4keZsNhr7PP3vu/1n+t9d8H/mOokXObOl5tq67361BrOU9Vt6sbc55a/b2/wCI/g9VutTPXO/P74HJPfwGW0V2ldqXdyLlLHV/d9z+cr6v1/wRUG+oQ9Sd1Rq5P79cI85JGy9o4dbP6ofqNOjn31apnWqOtFNxIdY36lNqIyoZ6RPSm3QZMB84H9gIC7cAW4PuIOJIAUZ4pQSLCtBsRcUx9DrgUuAiYexwl6kjgYWAq8BOwBxgMnAo0gDZgOPBGRCzOM6cB9wKbI2JrCVoGoE4CPgDeBx6rUrJI3aouUEeVPdfiUId6WVL7uvqQuk1dqu5Uh7WmRn1SfTPtWi0pmAXcANwB/Aa8CuxRr0hnTs6cvA2MAa4GBgCTgPnJxl/A4Uoh9SbobcArWaVRFkgnsAR4FBgCvAiMAPZFxDHgmDocGJS53AY8ApwNLAJ+BKZFxP4ErAFNYAawPyK2tVK1UZ2lbqlQ91Xa9ZzvVNel3al+oa4re7KksuXMR+qt6i3qWICGOgb4EzhcoWQysC/vGQD0AlOAdWotIj4FPq0A1YFmFksti2UkcCGwGlgLrAS+K0PvABYC6/OOS4HutI9kAV0PbI2IZhWoBCjbIekEuB/oyvWjGRCNiOhWHwEuBt5NL0cDa9PzGnA6sBtYqvYAvwJrI2LZCdSmLJabgHmV9TiO97QbWdKL1Nc4wVAvUW9Xd1Qkr8xZPc/fZGpwrq9WZ5cRmqpRq+RhAdClrsjImsAh4GdgU0SsUmdRKFGf91Dm70FgRVW9ylEDyLw0I6KpXgI8AVwHjKLoz6DI8+vADHUEcC3wXjLUm7k8qnbkuRWtYFDIVRW8CUwDRkdET1I1LyJ2JjUTgeXAOxTydiDz1QTq6exbwAbgHHVqRCylr5CoKnxZZROB5Xl4INCdub2PQlHaM7rF6ZDJ0lFgMYXYj6JQozktlPPPE5PcDwQuADYCDwDrI6KZajMbWAa8DLwWET3ZDr3FFS5MoKEUr8oBiryfkNJIT8cCByLiD/VGYG461MiLdgN7ImKhekoCDAKWUsjbNcDQiNih3lyNrBWwzN8UYJN6JXAoInaqJ+X79y3wPLBEnQPcAwwDzgMeiog1WUCr1JkU6vQvwDKHZf4mAJ8Ad1M0flAI99D0vofiZZkLXJ6ULQe+VC8G6sAZLXceH2Elf6dSPLa7MtKZ2ZMN4HNgZUQ8k1R3AeMpCuyljHx3RDyrVoFsmfuKJikYRPHe3R0Ru3LPBuCzBKsDLwDjgO3A3oj4Ky88UgaRc1TsRn6nfIBrefBpitJ+XP1Y/Rr4gaLB78qK/AX4nUJl5qezPaQ4U1Rn6cDBXDtYcShj7nvLzlYnqFfb9//zLPXctIdlX9bVIbl2pjoo7Y6cT1bb027PquZvxEhzVOc8u6cAAAAASUVORK5CYII="

def _draw_poi_icon(c, cat, cx, cy, r, col):
    """Dessine une icône vectorielle simple selon la catégorie POI."""
    import unicodedata as _ud
    cat_up = cat.upper()
    c.setFillColor(_BLANC); c.setStrokeColor(_BLANC); c.setLineWidth(0.5)

    if "PARKING" in cat_up or "STATIONNEMENT" in cat_up:
        # Lettre P
        c.setFont("Helvetica-Bold", r*1.4); c.setFillColor(_BLANC)
        c.drawCentredString(cx, cy - r*0.45, "P")

    elif "TRANSPORT" in cat_up or "GARE" in cat_up or "BUS" in cat_up:
        # Bus simplifié : rectangle arrondi
        w=r*1.1; h=r*0.8
        c.roundRect(cx-w/2, cy-h/2, w, h, 1*_mm, fill=1, stroke=0)
        c.setFillColor(col)
        c.roundRect(cx-w/2+1, cy-h/2+1, w-2, h-2, 0.8*_mm, fill=1, stroke=0)
        c.setFillColor(_BLANC)
        # Roues
        c.circle(cx-w/3, cy-h/2-1, 1.2, fill=1, stroke=0)
        c.circle(cx+w/3, cy-h/2-1, 1.2, fill=1, stroke=0)

    elif "RESTAURATION" in cat_up or "CAFE" in cat_up or "RESTAURANT" in cat_up:
        # Fourchette + couteau simplifié = deux traits verticaux
        c.setLineWidth(1.2)
        c.line(cx-2, cy-r*0.8, cx-2, cy+r*0.8)
        c.line(cx+2, cy-r*0.8, cx+2, cy+r*0.8)
        # Petit arc = cuillère
        p = c.beginPath()
        p.moveTo(cx-2, cy); p.curveTo(cx-2, cy+r*0.4, cx+2, cy+r*0.4, cx+2, cy)
        c.drawPath(p, fill=0, stroke=1)

    elif "COMMERCE" in cat_up or "MAGASIN" in cat_up or "SUPERMARCHÉ" in cat_up:
        # Sachet shopping
        w=r*1.1; h=r*0.9; hy=cy-h/2
        c.roundRect(cx-w/2, hy, w, h, 1*_mm, fill=1, stroke=0)
        # Anse
        c.setFillColor(col)
        p2 = c.beginPath()
        p2.moveTo(cx-w/4, hy+h); p2.curveTo(cx-w/4, hy+h+r*0.6, cx+w/4, hy+h+r*0.6, cx+w/4, hy+h)
        c.drawPath(p2, fill=0, stroke=1)
        c.setFillColor(_BLANC)

    elif "FORMATION" in cat_up or "ECOLE" in cat_up or "UNIVERSITÉ" in cat_up:
        # Chapeau de diplômé simplifié
        w=r*1.2
        c.rect(cx-w/2, cy-r*0.2, w, r*0.35, fill=1, stroke=0)
        # Triangle chapeau
        p3 = c.beginPath()
        p3.moveTo(cx-w/2-2, cy+r*0.15)
        p3.lineTo(cx, cy+r*0.9)
        p3.lineTo(cx+w/2+2, cy+r*0.15)
        p3.close()
        c.drawPath(p3, fill=1, stroke=0)

    elif "BANQUE" in cat_up or "SERVICE" in cat_up:
        # Symbole € simplifié
        c.setFont("Helvetica-Bold", r*1.5); c.setFillColor(_BLANC)
        c.drawCentredString(cx, cy - r*0.5, "€")

    elif "HOTEL" in cat_up or "HÉBERGEMENT" in cat_up:
        # Lit simplifié
        w=r*1.2; h=r*0.5
        c.roundRect(cx-w/2, cy-h/2, w, h, 1*_mm, fill=1, stroke=0)
        # Tête de lit
        c.rect(cx-w/2, cy+h/2-1, w*0.35, r*0.45, fill=1, stroke=0)

    elif "DYNAMISME" in cat_up or "ZONE" in cat_up:
        # Graphique croissant (flèche vers le haut)
        try:
            import base64 as _b64, io as _io
            from reportlab.lib.utils import ImageReader as _IR
            img_data = _b64.b64decode(_PICTO_DYNAMIQUE_B64)
            img_obj = _IR(_io.BytesIO(img_data))
            sz = r * 1.6
            c.drawImage(img_obj, cx-sz/2, cy-sz/2, sz, sz, mask="auto")
        except Exception:
            # Fallback flèche
            p4 = c.beginPath()
            p4.moveTo(cx, cy+r*0.9); p4.lineTo(cx-r*0.4, cy+r*0.2)
            p4.lineTo(cx-r*0.15, cy+r*0.2); p4.lineTo(cx-r*0.15, cy-r*0.8)
            p4.lineTo(cx+r*0.15, cy-r*0.8); p4.lineTo(cx+r*0.15, cy+r*0.2)
            p4.lineTo(cx+r*0.4, cy+r*0.2); p4.close()
            c.drawPath(p4, fill=1, stroke=0)
    else:
        # Fallback : point blanc
        c.circle(cx, cy, r*0.4, fill=1, stroke=0)


def _draw_poi_card(c, bx, by, bw, bh, label, valeur, color_hex):
    """Dessine un bloc POI avec pastille colorée + icône vectorielle + texte."""
    import unicodedata as _ud
    from reportlab.lib import colors as _rc
    def _safe_str(s):
        try:
            str(s).encode('latin-1'); return str(s)
        except:
            return _ud.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode('ascii')

    col = _rc.HexColor(color_hex)
    # Fond gris clair
    c.setFillColor(_GRIS)
    c.roundRect(bx, by, bw, bh, 2*_mm, fill=1, stroke=0)
    # Pastille colorée à gauche
    dot_x = bx + 5.5*_mm; dot_y = by + bh/2
    c.setFillColor(col)
    c.circle(dot_x, dot_y, 3.5*_mm, fill=1, stroke=0)
    # Icône vectorielle dans la pastille
    _draw_poi_icon(c, label, dot_x, dot_y, 3.5*_mm, col)
    # Texte
    txt_x = bx + 12*_mm
    c.setFillColor(col)
    c.setFont("Helvetica-Bold", 7.5)
    c.drawString(txt_x, by + bh - 5*_mm, _safe_str(label).upper())
    c.setFillColor(_GTEXTE)
    c.setFont("Helvetica", 7)
    max_w = bw - 14*_mm
    txt = _safe_str(valeur)
    while c.stringWidth(txt, "Helvetica", 7) > max_w and len(txt) > 4:
        txt = txt[:-2] + "..."
    c.drawString(txt_x, by + 2.5*_mm, txt)


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
    p = _Para(texte, _PS("b", fontName="Helvetica", fontSize=10, textColor=_GTEXTE, leading=16))
    _, ph = p.wrap(_W-28*_mm, 9999); p.drawOn(c, 14*_mm, _H-38*_mm-ph)
    qbot = _H-38*_mm-ph-12*_mm

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
    poi_blocks = []
    if lat and lon:
        try:
            poi_blocks = _get_poi_blocks_osm(lat, lon, radius=500)
        except Exception:
            pass

    if not poi_blocks:
        poi_blocks = [
            ("Parking",      "Stationnement disponible", "#1B3A5C"),
            ("Transport",    "Gare SNCF / Bus",          "#0D5570"),
            ("Restauration", "Cafes & restaurants",      "#E8472A"),
            ("Commerce",     "Services de proximite",    "#3A1B5C"),
            ("Formation",    "Etablissements proches",   "#5C3A1B"),
            ("Dynamisme",    "Zone active",              "#1B5C3A"),
        ]

    # ── Zone 1 : POI quartier (Overpass — ce qui existe autour) ────────────
    _sec(c, "Environnement du quartier", 14*_mm, my - 4*_mm)
    pt_y = my - 12*_mm
    ncols = 3; card_w = (_W-28*_mm - (ncols-1)*4*_mm)/ncols; card_h = 13*_mm
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
            taxe_fmt = f"{int(float(str(taxe).replace(' ',''))) :,}".replace(","," ") + " EUR/an"
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

    # Plan cadastral IGN
    ref_cad = d.get("ref_cadastrale","")
    if ref_cad and len(ref_cad) >= 6:
        try:
            cad_img = _fetch_cadastre_image(ref_cad, d.get("adresse",""), d.get("ville",""))
            if cad_img:
                cad_y = pt_y - 28*_mm - 40*_mm
                cad_w = _W - 28*_mm; cad_h = 38*_mm
                _sec(c, "Plan cadastral", 14*_mm, cad_y + cad_h + 6*_mm)
                buf_cad = _BytesIO(); cad_img.save(buf_cad, "PNG"); buf_cad.seek(0)
                from reportlab.lib.utils import ImageReader as _IRC
                c.saveState()
                p_cad = c.beginPath(); p_cad.roundRect(14*_mm, cad_y, cad_w, cad_h, 2*_mm)
                c.clipPath(p_cad, stroke=0, fill=0)
                c.drawImage(_IRC(buf_cad), 14*_mm, cad_y, cad_w, cad_h, mask="auto")
                c.restoreState()
                c.setStrokeColor(_colors.HexColor("#CCCCCC")); c.setLineWidth(0.5)
                c.roundRect(14*_mm, cad_y, cad_w, cad_h, 2*_mm, fill=0, stroke=1)
                c.setFillColor(_colors.HexColor("#999999")); c.setFont("Helvetica", 5.5)
                c.drawRightString(_W-14*_mm, cad_y+1.5*_mm, "© IGN Géoportail — Plan cadastral")
        except Exception:
            pass

    _footer(c, 3)

def _page4(c, comparables, d):
    _header(c, "Biens comparables"); _sec(c,"Analyse des biens comparables",14*_mm,_H-32*_mm)
    intro = _Para("Sélection des transactions les plus récentes permettant de positionner ce bien dans son marché local.",
        _PS("sm",fontName="Helvetica",fontSize=9,textColor=_GTEXTE,leading=13))
    _,ih = intro.wrap(_W-28*_mm,9999); intro.drawOn(c,14*_mm,_H-40*_mm-ih)
    ct=_H-42*_mm-ih-6*_mm; ch=50*_mm
    if not comparables:
        c.setFillColor(_GRIS); c.roundRect(14*_mm,ct-ch,_W-28*_mm,ch,3*_mm,fill=1,stroke=0)
        c.setFillColor(_colors.HexColor("#AAAAAA")); c.setFont("Helvetica-Oblique",9)
        c.drawCentredString(_W/2,ct-ch/2,"Aucun comparable disponible — relancer la recherche dans 01_Biens")
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
            pl=[float(str(x.get("Prix",0)).replace(" ","").replace("\u202f","")) for x in comparables if x.get("Prix")]
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

def _page5(c, d):
    is_loc = bool(d.get("loyer_mensuel"))
    loyer_m = float(str(d.get("loyer_mensuel") or 0).replace(" ",""))
    surf = d.get("surface")

    if is_loc:
        # ── LOCATION : afficher fourchette loyer annuel au m² ──────────────
        _header(c,"Notre positionnement locatif"); _sec(c,"Loyer de marché",14*_mm,_H-32*_mm)
        surf_f = float(str(surf or 0).replace(" ","")) if surf else 0
        loyer_an_actuel = loyer_m * 12
        loyer_m2_actuel = loyer_an_actuel / surf_f if surf_f else 0
        pm = int(loyer_an_actuel * 0.90) if loyer_an_actuel else 0
        pv = int(loyer_an_actuel)
        px = int(loyer_an_actuel * 1.10)

        def _pfmt_loyer(v):
            if not v: return "—"
            try: return f"{int(v):,}".replace(",", " ") + " €/an"
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
            c.setFillColor(_GTEXTE); c.setFont("Helvetica",8.5)
            c.drawCentredString(_W/2,by2-42*_mm,
                f"Loyer mensuel : {int(loyer_m):,} € HT/mois  ·  soit {int(loyer_m2_actuel):,} €/m²/an  ·  Surface : {_safe(surf)} m²".replace(","," "))
        ay=by2-54*_mm; _sec(c,"Analyse & positionnement",14*_mm,ay); cw2=(_W-28*_mm-6*_mm)/2
        c.setFillColor(_colors.HexColor("#E8F4F8")); c.roundRect(14*_mm,ay-52*_mm,cw2,50*_mm,2*_mm,fill=1,stroke=0)
        c.setFillColor(_BLEU); c.setFont("Helvetica-Bold",8.5); c.drawString(18*_mm,ay-7*_mm,"ATOUTS DU BIEN")
        for i,a in enumerate(["Emplacement commercial stratégique",f"Surface : {_safe(surf)} m²","Visibilité et accessibilité","Secteur à forte demande locative"]):
            c.setFillColor(_GTEXTE); c.setFont("Helvetica",8.5); c.drawString(18*_mm,ay-16*_mm-i*10*_mm,f"·  {a}")
        c.setFillColor(_colors.HexColor("#FFF8F0")); c.roundRect(14*_mm+cw2+6*_mm,ay-52*_mm,cw2,50*_mm,2*_mm,fill=1,stroke=0)
        c.setFillColor(_ORANGE); c.setFont("Helvetica-Bold",8.5); c.drawString(18*_mm+cw2+6*_mm,ay-7*_mm,"POSITIONNEMENT LOYER")
        loyer_expl = [
            "Le loyer affiché est positionné",
            f"a {int(loyer_m2_actuel):.0f} EUR/m2/an, cohérent" if loyer_m2_actuel else "en cohérence avec le marché",
            "avec le marché local des locaux",
            "commerciaux du secteur.",
            "",
            "Les DVF renseignent les ventes,",
            "pas les loyers. Notre estimation",
            "s'appuie sur les baux en cours.",
        ]
        for i,line in enumerate(loyer_expl):
            c.setFillColor(_GTEXTE); c.setFont("Helvetica",7.5)
            c.drawString(18*_mm+cw2+6*_mm, ay-16*_mm-i*7*_mm, line)

    else:
        # ── VENTE : afficher fourchette valeur vénale ────────────────────────
        _header(c,"Notre estimation de valeur"); _sec(c,"Positionnement prix",14*_mm,_H-32*_mm)
        pm=d.get("prix_estime_min") or d.get("prix"); px=d.get("prix_estime_max") or d.get("prix")
        pv=d.get("prix_retenu") or d.get("prix")
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
    c.setFillColor(_colors.HexColor("#E8F4F8")); c.roundRect(14*_mm,ay-52*_mm,cw2,50*_mm,2*_mm,fill=1,stroke=0)
    c.setFillColor(_BLEU); c.setFont("Helvetica-Bold",8.5); c.drawString(18*_mm,ay-7*_mm,"▸ ATOUTS DU BIEN")
    for i,a in enumerate(["Emplacement commercial stratégique",f"Surface adaptée ({_safe(surf)} m²)","Potentiel de développement","Secteur à forte demande"]):
        c.setFillColor(_GTEXTE); c.setFont("Helvetica",8.5); c.drawString(18*_mm,ay-16*_mm-i*10*_mm,f"·  {a}")
    # Bloc explication DVF vs estimation
    c.setFillColor(_colors.HexColor("#FFF8F0")); c.roundRect(14*_mm+cw2+6*_mm,ay-52*_mm,cw2,50*_mm,2*_mm,fill=1,stroke=0)
    c.setFillColor(_ORANGE); c.setFont("Helvetica-Bold",8.5); c.drawString(18*_mm+cw2+6*_mm,ay-7*_mm,"▸ POURQUOI CET ÉCART AVEC LES DVF ?")
    expl = [
        "Les DVF (données officielles) recensent",
        "toutes les ventes de locaux commerciaux",
        "dans la commune, quelle que soit leur",
        "localisation ou configuration.",
        "",
        "Notre estimation intègre les spécificités",
        "de ce bien : visibilité, état, emplacement",
        "précis et potentiel locatif réel.",
    ]
    for i,line in enumerate(expl):
        c.setFillColor(_GTEXTE); c.setFont("Helvetica",7.5)
        c.drawString(18*_mm+cw2+6*_mm, ay-16*_mm-i*7*_mm, line)
    # Taxe foncière si disponible
    taxe = d.get("taxe_fonciere") or d.get("taxe") or 0
    if taxe:
        try:
            taxe_fmt = f"{int(float(str(taxe).replace(' ',''))) :,}".replace(",","\u202f") + " €/an"
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
        }

        comparables = data.get("comparables", [])

        # Auto-fetch DVF directement (sans HTTP interne)
        if not comparables:
            try:
                dvf_comps, dvf_pm2, dvf_stats = _run_dvf(
                    ville   = data.get("ville", "Vannes"),
                    code_postal = data.get("code_postal", "56000"),
                    surface = float(data.get("surface") or 0),
                    type_bien   = data.get("type_bien", "Local commercial"),
                    limit   = 4
                )
                comparables = dvf_comps

                # Calculer les fourchettes de prix depuis DVF si absentes
                surface_val = float(data.get("surface") or 0)
                prix_v = d.get("prix") or 0
                loyer_m = data.get("loyer_mensuel") or 0

                if dvf_pm2 > 0 and surface_val > 0:
                    # Marché locatif : on calcule sur le loyer/m²
                    if loyer_m:
                        loyer_m2_actuel = (loyer_m * 12) / surface_val
                        # Fourchette ±15% autour du loyer actuel pondéré par le marché
                        pm2_ref = dvf_pm2 if dvf_pm2 > 50 else loyer_m2_actuel
                        d["prix_estime_min"] = int(pm2_ref * 0.88 * surface_val)
                        d["prix_estime_max"] = int(pm2_ref * 1.12 * surface_val)
                        d["prix_retenu"]     = int(pm2_ref * surface_val)
                        if not d.get("prix"):
                            d["prix"] = d["prix_retenu"]
                    elif prix_v:
                        # Bien à vendre : fourchette ±10% autour du DVF
                        pm2_vente = prix_v / surface_val
                        pm2_ref = (pm2_vente + dvf_pm2) / 2
                        d["prix_estime_min"] = int(pm2_ref * 0.90 * surface_val)
                        d["prix_estime_max"] = int(pm2_ref * 1.10 * surface_val)
                        d["prix_retenu"]     = int(pm2_ref * surface_val)
                        d["prix"] = prix_v  # garder le prix affiché réel
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
            try: return f"{int(float(str(v).replace(' ',''))) :,}".replace(",", "\u202f") + " €"
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

        prix_str = f"{int(float(str(prix).replace(' ',''))):,} €".replace(",","\u202f") if prix else ""
        loyer_str = f"{int(float(str(loyer).replace(' ',''))):,} € HT/mois".replace(",","\u202f") if loyer else ""
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
    results = []
    for annee in ["2024", "2023", "2022"]:
        if len(results) >= limit * 2:
            break
        try:
            csv_url = f"https://files.data.gouv.fr/geo-dvf/latest/csv/{annee}/communes/{dept}/{code_commune}.csv"
            with _ur.urlopen(_ur.Request(csv_url, headers={"User-Agent": "Barbier-Immobilier/1.0"}), timeout=15) as r:
                raw = r.read().decode("utf-8", errors="ignore")
            reader = _csv2.DictReader(_io2.StringIO(raw))
            commercial_kw = ["commercial", "industriel", "bureau", "activité"]
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
                if not any(kw in type_l for kw in commercial_kw):
                    continue
                # Filtrer par surface similaire (±70%)
                if surface and surface > 0:
                    if abs(s - surface) / max(surface, 1) > 0.70:
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

    # 3. Stats
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
                        "description": f"{row.get('type_local','?')} — {round(prix):,} € — {row.get('date_mutation','')} — {surf} m²".replace(",", " ")
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
                                "description": (ad.get("description") or "")[:200] or f"Annonce active — {round(prix):,} €".replace(",", " ")
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
    s = s.replace("\u202f", " ").replace("\u00a0", " ")
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

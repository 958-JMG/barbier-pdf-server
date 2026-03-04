#!/usr/bin/env python3
"""
Barbier Immobilier — PDF Generator v3.0
Railway Flask app — routes /generate-pdf-by-ref et /generate-pdf
Version fusionnée : générateur PDF intégré
"""

import io, os, math, base64, re
import requests
from flask import Flask, request, jsonify, send_file
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
TEAL         = colors.HexColor("#1B6B7B")
TEAL_DARK    = colors.HexColor("#145260")
TEAL_LIGHT   = colors.HexColor("#EBF4F6")
ORANGE       = colors.HexColor("#E8632A")
ORANGE_LIGHT = colors.HexColor("#FDF2EC")
GRAY_DARK    = colors.HexColor("#1F2937")
GRAY_MID     = colors.HexColor("#6B7280")
GRAY_LIGHT   = colors.HexColor("#F3F4F6")
GRAY_BORDER  = colors.HexColor("#D1D5DB")
WHITE        = colors.white

PAGE_W, PAGE_H = A4
ML = 20*mm; MR = 20*mm; MT = 22*mm; MB = 16*mm
CW = PAGE_W - ML - MR
LOGO_B64 = os.environ.get("LOGO_B64", "")

def fmt(val):
    return f"{val:,.0f}".replace(",", " ") + " €"


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

def get_osm_map(address, out_w=840, out_h=340, zoom=16):
    """Carte centrée exactement sur le point géocodé."""
    try:
        headers = {"User-Agent": "BarbierImmobilier/1.0"}
        r = requests.get("https://nominatim.openstreetmap.org/search",
                         params={"q": address, "format": "json", "limit": 1},
                         headers=headers, timeout=10)
        res = r.json()
        if not res: return None
        lat = float(res[0]["lat"])
        lon = float(res[0]["lon"])

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
        c.drawImage(LOGO_PATH, ML, y - logo_h, width=logo_w, height=logo_h,
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
    c.drawRightString(PAGE_W - MR, y - 25, f"Réf. {d['reference']}  ·  {d['ville']}  ·  {d['nom_client']}")
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

    # ── SECTION 02 — LOCALISATION ───────────────────────────────────────────
    y = sec_title(c, ML, y, "02 — Localisation")

    map_h = 88*mm
    map_buf = get_osm_map(d["adresse"], out_w=840, out_h=340, zoom=16)

    if map_buf:
        c.drawImage(rl_canvas.ImageReader(map_buf), ML, y - map_h,
                    width=CW, height=map_h, preserveAspectRatio=False)
    else:
        rrect(c, ML, y - map_h, CW, map_h, fill=GRAY_LIGHT, stroke=GRAY_BORDER)
        c.setFillColor(GRAY_MID); c.setFont("Helvetica", 9)
        c.drawCentredString(ML + CW/2, y - map_h/2, "Carte de localisation indisponible")

    # Légende sous la carte
    y -= map_h + 4
    c.saveState()
    c.setFillColor(TEAL); c.setFont("Helvetica-Bold", 7.5)
    c.drawString(ML, y, f"\u25a0  {d['adresse']}")
    c.restoreState()
    y -= 14

    # ── SECTION 03 — ESTIMATION DE VALEUR ───────────────────────────────────
    y = sec_title(c, ML, y, "03 — Estimation de valeur")

    card_gap = 6
    card_w = (CW - 2*card_gap) / 3
    card_h = 58

    cards = [
        ("VALEUR BASSE",   d["prix_min"],    False),
        ("VALEUR RETENUE", d["prix_retenu"],  True),
        ("VALEUR HAUTE",   d["prix_max"],    False),
    ]

    for i, (lbl, prix, rec) in enumerate(cards):
        cx = ML + i * (card_w + card_gap)
        cy = y - card_h

        if rec:
            rrect(c, cx, cy, card_w, card_h, r=5, fill=TEAL, stroke=TEAL_DARK, sw=1.5)
            tc, pc = WHITE, WHITE
        else:
            rrect(c, cx, cy, card_w, card_h, r=5, fill=WHITE, stroke=GRAY_BORDER, sw=0.8)
            tc, pc = GRAY_MID, GRAY_DARK

        # Badge RECOMMANDÉ (en haut, dans la carte)
        if rec:
            bw, bh = 72, 13
            bx = cx + (card_w - bw)/2
            by = cy + card_h - bh - 5
            rrect(c, bx, by, bw, bh, r=6, fill=ORANGE)
            c.saveState()
            c.setFillColor(WHITE); c.setFont("Helvetica-Bold", 6)
            c.drawCentredString(cx + card_w/2, by + 3, "\u2605  RECOMMAND\u00c9")
            c.restoreState()

        # Label
        label_y = cy + card_h - (rec and 30 or 14)
        c.saveState()
        c.setFillColor(tc); c.setFont("Helvetica", 6.5)
        c.drawCentredString(cx + card_w/2, label_y, lbl)
        c.restoreState()

        # Prix — centré verticalement dans la carte
        prix_str = fmt(prix)
        # Centre vertical = cy + card_h/2, on retire demi-hauteur de la fonte (~8pt)
        prix_y = cy + card_h/2 - (rec and 19 or 18)
        c.saveState()
        c.setFillColor(pc); c.setFont("Helvetica-Bold", rec and 15 or 13)
        c.drawCentredString(cx + card_w/2, prix_y, prix_str)
        c.restoreState()

    y -= card_h + 10

    # Bandeau prix conseillé
    b_h = 24
    rrect(c, ML, y - b_h, CW, b_h, r=4, fill=ORANGE_LIGHT, stroke=ORANGE, sw=0.8)
    c.saveState()
    c.setFillColor(ORANGE);    c.setFont("Helvetica-Bold", 7.5)
    c.drawString(ML + 10, y - b_h/2 - 3, "PRIX DE MISE EN MARCH\u00c9 CONSEILL\u00c9")
    c.setFillColor(GRAY_DARK); c.setFont("Helvetica-Bold", 12)
    c.drawRightString(PAGE_W - MR - 10, y - b_h/2 - 4, fmt(d["prix_retenu"]))
    c.restoreState()


# ══════════════════════════════════════════════════════════════════════════════
#  PAGE 2 — Analyse + Synthèse + Signatures (tout tient sur la page)
# ══════════════════════════════════════════════════════════════════════════════

def page2(c, d, logo_buf=None):
    y = PAGE_H - MT

    c.saveState()
    c.setFillColor(TEAL)
    c.rect(0, 0, 5, PAGE_H, fill=1, stroke=0)
    c.restoreState()

    # Section 04 — Analyse de marché
    y = sec_title(c, ML, y, "04 — Analyse de marché & Avis professionnel")

    # Filtrer le texte GPT : garder uniquement les lignes > 60 chars
    avis_raw = (d.get("Avis de valeur") or "Avis de valeur à compléter.").strip()
    lignes_sub = [l for l in avis_raw.split("\n") if len(l.strip()) > 60]
    avis_clean = " ".join(lignes_sub)
    # Pas de troncature — hauteur dynamique
    if not avis_clean:
        avis_clean = "Avis de valeur à compléter."

    style_avis = ParagraphStyle("av", fontName="Helvetica", fontSize=8,
                                leading=12, textColor=GRAY_DARK)
    p_avis = Paragraph(avis_clean, style_avis)
    _, avis_h = p_avis.wrap(CW - 16, 9999)
    avis_box_h = avis_h + 24

    rrect(c, ML, y - avis_box_h, CW, avis_box_h, r=4, fill=TEAL_LIGHT, stroke=TEAL, sw=0.5)
    p_avis.drawOn(c, ML + 8, y - avis_box_h + 10)

    y -= avis_box_h + 14

    # Synthèse des valeurs
    y = sec_title(c, ML, y, "Synthèse des valeurs")

    rows = [
        ("Valeur minimale",   fmt(d.get("Prix estime min")),    False),
        ("Valeur maximale",   fmt(d.get("Prix estime max")),    False),
        ("Valeur retenue",    fmt(d.get("Prix retenu")),         True),
        ("Prix sans décote",  fmt(d.get("Prix sans décote")),   False),
        ("Prix avec décote",  fmt(d.get("Prix avec décote")),   False),
    ]

    rh = 18
    for i, (lbl, val, highlight) in enumerate(rows):
        ry = y - i * rh
        if highlight:
            c.saveState()
            c.setFillColor(TEAL)
            c.rect(ML, ry - rh, CW, rh, fill=1, stroke=0)
            c.setFillColor(WHITE); c.setFont("Helvetica-Bold", 8)
            c.drawString(ML + 8, ry - rh + 5, lbl)
            c.drawRightString(PAGE_W - MR - 8, ry - rh + 5, val)
            c.restoreState()
        else:
            bg = GRAY_LIGHT if i % 2 == 0 else WHITE
            c.saveState()
            c.setFillColor(bg)
            c.rect(ML, ry - rh, CW, rh, fill=1, stroke=0)
            c.setFillColor(GRAY_DARK); c.setFont("Helvetica", 8)
            c.drawString(ML + 8, ry - rh + 5, lbl)
            c.setFont("Helvetica-Bold", 8)
            c.drawRightString(PAGE_W - MR - 8, ry - rh + 5, val)
            c.restoreState()

    c.saveState()
    c.setStrokeColor(GRAY_BORDER); c.setLineWidth(0.5)
    c.rect(ML, y - len(rows)*rh, CW, len(rows)*rh, fill=0, stroke=1)
    c.restoreState()

    y -= len(rows)*rh + 14

    # Méthodologie
    y = sec_title(c, ML, y, "Méthodologie d'évaluation")

    methodo = (
        "L'estimation est établie par comparaison avec les transactions récentes sur le marché local "
        "et les biens actuellement proposés à la vente. Les éléments pris en compte incluent la "
        "localisation, l'état général, la surface, la configuration et les tendances du marché "
        "immobilier commercial à Vannes et son agglomération."
    )
    style_m = ParagraphStyle("m", fontName="Helvetica", fontSize=7.5, leading=11, textColor=GRAY_MID)
    pm = Paragraph(methodo, style_m)
    pm.wrap(CW, 50)
    pm.drawOn(c, ML, y - pm.height)
    y -= pm.height + 14

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
    """Génère le PDF et retourne un buffer BytesIO."""
    # Charger le logo
    logo_buf = None
    if LOGO_B64:
        try:
            logo_buf = io.BytesIO(base64.b64decode(LOGO_B64))
        except:
            pass

    # Mapper les clés SeaTable (espaces) vers snake_case attendu par page1
    data = {
        "reference":       data.get("Reference", ""),
        "type_bien":       data.get("Type de bien", "—"),
        "surface":         data.get("Surface") or "—",
        "surface_terrain": data.get("Surface terrain"),
        "adresse":         data.get("Adresse", "—"),
        "ville":           data.get("Ville", ""),
        "code_postal":     data.get("Code postal", ""),
        "ref_cadastrale":  data.get("Référence cadastrale", "—"),
        "etat_bien":       data.get("Etat du bien", "—"),
        "negociateur":     data.get("Negociateur", "—"),
        "nom_client":      data.get("Nom client", "—"),
        "prix_min":        data.get("Prix estime min"),
        "prix_max":        data.get("Prix estime max"),
        "prix_retenu":     data.get("Prix retenu"),
        "prix_sans_decote":data.get("Prix sans décote"),
        "prix_avec_decote":data.get("Prix avec décote"),
        "avis_valeur":     data.get("Avis de valeur", ""),
        # Garder aussi les clés originales pour page2
        "Avis de valeur":  data.get("Avis de valeur", ""),
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
    return jsonify({"service": "Barbier PDF Generator", "status": "ok", "version": "3.0"})


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

        # 2. Générer le PDF
        pdf_buf = generate_pdf(row)
        app.logger.info(f"PDF généré: {reference}")

        # 3. Mettre à jour le statut SeaTable
        try:
            seatable_update_statut(reference, "PDF généré")
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

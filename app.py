#!/usr/bin/env python3
"""
Barbier Immobilier — PDF Generator v3.0
Railway Flask app — routes /generate-pdf-by-ref et /generate-pdf
Version fusionnée : générateur PDF intégré
"""

import io, os, math, base64, re
import requests
from flask import Flask, request, jsonify, send_file, Response
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



# ─────────────────────────────────────────────────────────────────
# DOSSIER DE VENTE
# ─────────────────────────────────────────────────────────────────


# ═══════════════════════════════════════════════════════
# DOSSIER DE VENTE — fonctions et route
# ═══════════════════════════════════════════════════════

def _st_token():
    """Auth SeaTable — retourne (access_token, uuid)"""
    r2 = requests.get("https://cloud.seatable.io/api/v2.1/dtable/app-access-token/",
        headers={"Authorization": f"Token {SEATABLE_TOKEN}"}, timeout=10)
    tok = r2.json()
    return tok["access_token"], tok["dtable_uuid"]

"""Dossier vente Barbier — v3"""
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from PIL import Image
import io, math, urllib.request

W, H = A4

# ── Couleurs exactes logo Barbier ──
BLEU   = colors.HexColor("#16708B")
ORANGE = colors.HexColor("#F0795B")
BLEU_F = colors.HexColor("#0D4F62")
GRIS   = colors.HexColor("#F4F6F8")
GTEXTE = colors.HexColor("#333333")
BLANC  = colors.white

LOGO_PATH = "/home/claude/logo_barbier.png"

# ── Pictos dessinés en pur ReportLab (cercle teal + lettre blanche) ──
PICTOS = {
    "surface":   ("S", "Surface"),
    "type":      ("T", "Type"),
    "adresse":   ("A", "Adresse"),
    "ville":     ("V", "Ville"),
    "terrain":   ("⬜", "Terrain"),
    "annee":     ("C", "Construction"),
    "ca":        ("€", "CA HT"),
    "loyer":     ("L", "Loyer"),
    "activite":  ("✦", "Activité"),
    "pieces":    ("P", "Pièces"),
    "dpe":       ("D", "DPE"),
    "chauffage": ("🔥", "Chauffage"),
}

# Symboles lisibles sans police spéciale
ICONS = {
    "surface":   "m²",
    "type":      "▣",
    "adresse":   "◎",
    "ville":     "◉",
    "terrain":   "▢",
    "annee":     "◷",
    "ca":        "€",
    "loyer":     "€",
    "activite":  "★",
    "pieces":    "#",
    "dpe":       "D",
}

def draw_logo(c, x, y, w=38*mm):
    try:
        logo = ImageReader(LOGO_PATH)
        ratio = 662/488
        c.drawImage(logo, x, y, width=w, height=w*ratio, mask='auto')
    except:
        c.setFillColor(BLEU); c.setFont("Helvetica-Bold",10)
        c.drawString(x, y+4*mm, "barbier immobilier")

def draw_logo_on_white(c, x, y, w=34*mm):
    pad = 3*mm
    ratio = 662/488
    h_logo = w*ratio
    c.setFillColor(BLANC)
    c.roundRect(x-pad, y-pad, w+2*pad, h_logo+2*pad, 3*mm, fill=1, stroke=0)
    draw_logo(c, x, y, w)

def draw_footer(c, n):
    c.setFillColor(BLEU_F)
    c.rect(0, 0, W, 9*mm, fill=1, stroke=0)
    c.setFillColor(BLANC)
    c.setFont("Helvetica", 6.5)
    c.drawString(14*mm, 3.5*mm,
        "Barbier Immobilier — 2 place Albert Einstein, 56000 Vannes — 02.97.47.11.11 — barbierimmobilier.com")
    c.drawRightString(W-14*mm, 3.5*mm, f"{n} / 6")

def draw_header(c, sub=""):
    c.setFillColor(BLEU)
    c.rect(0, H-11*mm, W, 11*mm, fill=1, stroke=0)
    c.setFillColor(BLANC); c.setFont("Helvetica-Bold", 8.5)
    c.drawString(14*mm, H-7.5*mm, f"DOSSIER DE PRÉSENTATION  ›  {sub.upper()}")

def draw_section_title(c, text, x, y):
    c.setFillColor(ORANGE)
    c.rect(x, y+3.5*mm, 3*mm, 7*mm, fill=1, stroke=0)
    c.setFillColor(BLEU_F); c.setFont("Helvetica-Bold", 13)
    c.drawString(x+7*mm, y+4.5*mm, text)

def safe(v, fb="—"):
    return fb if (v is None or v=="" or v==0) else str(v)

def pfmt(v):
    if not v: return "—"
    try: return f"{int(float(str(v).replace(' ',''))):,}".replace(",", " ") + " €"
    except: return str(v)

def pm2(p, s):
    try: return f"{int(float(str(p).replace(' ',''))/float(str(s).replace(' ',''))):,}".replace(",", " ")+" €/m²"
    except: return "—"

# ── Carte OSM ──
def lat_lon_to_tile(lat, lon, zoom):
    n = 2**zoom
    x = int((lon+180)/360*n)
    y = int((1-math.log(math.tan(math.radians(lat))+1/math.cos(math.radians(lat)))/math.pi)/2*n)
    return x, y

def geocode_osm(adresse, ville):
    """Géocode une adresse via Nominatim"""
    q = urllib.parse.quote_plus(f"{adresse}, {ville}, France")
    url = f"https://nominatim.openstreetmap.org/search?q={q}&format=json&limit=1"
    req = urllib.request.Request(url, headers={"User-Agent": "BarbierImmo/1.0 contact@958.fr"})
    with urllib.request.urlopen(req, timeout=8) as r:
        data = json.load(r)
    if data:
        return float(data[0]["lat"]), float(data[0]["lon"])
    # Fallback : centre Vannes
    return 47.6580, -2.7600

def get_osm_map_image(lat, lon, zoom=16, tiles=3):
    cx, cy = lat_lon_to_tile(lat, lon, zoom)
    half = tiles//2
    rows = []
    for row in range(tiles):
        row_imgs = []
        for col in range(tiles):
            tx, ty = cx-half+col, cy-half+row
            url = f"https://tile.openstreetmap.org/{zoom}/{tx}/{ty}.png"
            req = urllib.request.Request(url, headers={"User-Agent": "BarbierImmo/1.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                tile = Image.open(io.BytesIO(r.read())).convert("RGB")
            row_imgs.append(tile)
        rows.append(row_imgs)
    tw, th = rows[0][0].width, rows[0][0].height
    result = Image.new("RGB", (tw*tiles, th*tiles))
    for row in range(tiles):
        for col in range(tiles):
            result.paste(rows[row][col], (col*tw, row*th))
    return result

import urllib.parse, json

# ═══════════════════════════════════════
# PAGE 1 — COUVERTURE
# ═══════════════════════════════════════
def page_couverture(c, d):
    # Fond teal haut 52%
    c.setFillColor(BLEU)
    c.rect(0, H*0.48, W, H*0.52, fill=1, stroke=0)

    # Logo en haut à droite
    draw_logo_on_white(c, W-52*mm, H-52*mm, w=34*mm)



    # Titre
    c.setFillColor(BLANC); c.setFont("Helvetica-Bold", 30)
    c.drawString(14*mm, H-42*mm, safe(d.get("type_bien"), "Bien immobilier"))
    c.setFont("Helvetica", 15)
    c.drawString(14*mm, H-53*mm, safe(d.get("adresse")))
    c.drawString(14*mm, H-62*mm, f"{safe(d.get('code_postal'))} {safe(d.get('ville'))}")
    c.setFillColor(ORANGE)
    c.rect(14*mm, H-65.5*mm, 50*mm, 2.5*mm, fill=1, stroke=0)

    # Prix
    c.setFillColor(BLANC); c.setFont("Helvetica", 9)
    c.drawString(14*mm, H-74*mm, "PRIX DE PRÉSENTATION")
    prix = d.get("prix") or d.get("prix_retenu") or d.get("prix_estime_max")
    surface = d.get("surface")
    c.setFont("Helvetica-Bold", 34)
    c.drawString(14*mm, H-91*mm, pfmt(prix))
    if prix and surface:
        c.setFont("Helvetica", 10)
        c.setFillColor(colors.HexColor("#FFFFFFBB"))
        c.drawString(14*mm, H-98*mm, f"soit {pm2(prix,surface)}")

    # ── Blocs carac : fond BLANC, texte foncé — premium ──
    carac = [("SURFACE", f"{safe(surface)} m²"), ("TYPE", safe(d.get("type_bien","—"))[:12])]
    if d.get("surface_terrain"): carac.append(("TERRAIN", f"{safe(d.get('surface_terrain'))} m²"))
    if d.get("activite"): carac.append(("ACTIVITÉ", safe(d.get("activite"))[:12]))
    carac = carac[:4]

    bw = (W-28*mm)/len(carac) - 2*mm
    bh = 22*mm
    by = H*0.49 + 1*mm

    for i, (label, val) in enumerate(carac):
        bx = 14*mm + i*(bw+2*mm)
        c.setFillColor(colors.HexColor("#00000022"))
        c.roundRect(bx+0.5*mm, by-0.5*mm, bw, bh, 2*mm, fill=1, stroke=0)
        c.setFillColor(BLANC)
        c.roundRect(bx, by, bw, bh, 2*mm, fill=1, stroke=0)
        c.setFillColor(ORANGE)
        c.rect(bx+2*mm, by+bh-2*mm, bw-4*mm, 2*mm, fill=1, stroke=0)
        c.setFillColor(colors.HexColor("#888888"))
        c.setFont("Helvetica", 7)
        c.drawCentredString(bx+bw/2, by+bh-7*mm, label)
        # Valeur : taille auto pour éviter troncature
        c.setFillColor(BLEU_F)
        for fsz in [13, 11, 9, 8]:
            c.setFont("Helvetica-Bold", fsz)
            if c.stringWidth(val, "Helvetica-Bold", fsz) < bw - 6*mm:
                break
        c.drawCentredString(bx+bw/2, by+5*mm, val)

    # Zone blanche bas
    c.setFillColor(BLANC)
    c.rect(0, 0, W, H*0.48, fill=1, stroke=0)

    # Photo placeholder
    ph = H*0.48 - 22*mm
    c.setFillColor(GRIS); c.setStrokeColor(colors.HexColor("#DDDDDD")); c.setLineWidth(1)
    c.roundRect(14*mm, 20*mm, W-28*mm, ph, 3*mm, fill=1, stroke=1)
    c.setFillColor(colors.HexColor("#BBBBBB")); c.setFont("Helvetica", 10)
    c.drawCentredString(W/2, 20*mm+ph/2+3*mm, "[ Photo principale du bien ]")
    c.setFont("Helvetica", 8); c.setFillColor(colors.HexColor("#AAAAAA"))
    c.drawCentredString(W/2, 20*mm+ph/2-6*mm, "Intégrée automatiquement depuis SeaTable")

    c.setFillColor(GTEXTE); c.setFont("Helvetica", 7.5)
    c.drawString(14*mm, 13*mm,
        f"Dossier préparé par  {safe(d.get('negociateur'), 'Barbier Immobilier')}  ·  Réf. {safe(d.get('reference'))}")
    draw_footer(c, 1)

# ═══════════════════════════════════════
# PAGE 2 — BIEN + CARACTÉRISTIQUES AVEC PICTOS
# ═══════════════════════════════════════
# Mapping picto key → fichier PNG blanc
PICTO_FILES = {
    "m²":  "/home/claude/picto_surface_white.png",  # surface m²
    "▣":   "/home/claude/picto_type_white.png",     # types/formes
    "◎":   "/home/claude/picto_lieu_white.png",     # pin lieu/adresse
    "◉":   "/home/claude/picto_ville_white.png",    # ville/château
    "▢":   "/home/claude/picto_surface_white.png",  # terrain
    "◷":   "/home/claude/picto_type_white.png",     # année
    "€":   "/home/claude/picto_surface_white.png",  # CA/loyer → surface m²
    "★":   "/home/claude/picto_type_white.png",     # activité
    "#":   "/home/claude/picto_type_white.png",
}

def draw_pill_picto(c, x, y, picto, label, value, w=57*mm, h=16*mm):
    """Pastille avec vrai picto PNG à gauche"""
    c.setFillColor(GRIS)
    c.roundRect(x, y, w, h, 2*mm, fill=1, stroke=0)
    r = 5.5*mm
    cx_p = x + r + 2*mm
    cy_p = y + h/2
    # Cercle teal
    c.setFillColor(BLEU)
    c.circle(cx_p, cy_p, r, fill=1, stroke=0)
    # Picto PNG blanc centré dans le cercle
    picto_path = PICTO_FILES.get(picto)
    if picto_path:
        try:
            from reportlab.lib.utils import ImageReader
            ico = ImageReader(picto_path)
            ico_size = r * 1.3
            c.drawImage(ico, cx_p - ico_size/2, cy_p - ico_size/2,
                        width=ico_size, height=ico_size, mask='auto')
        except:
            c.setFillColor(BLANC); c.setFont("Helvetica-Bold", 9)
            c.drawCentredString(cx_p, cy_p-3*mm, picto)
    else:
        c.setFillColor(BLANC); c.setFont("Helvetica-Bold", 9)
        c.drawCentredString(cx_p, cy_p-3*mm, picto)
    # Label + valeur
    c.setFillColor(colors.HexColor("#888888")); c.setFont("Helvetica", 6.5)
    c.drawString(x+r*2+5*mm, y+h-4.5*mm, label.upper())
    c.setFillColor(BLEU_F); c.setFont("Helvetica-Bold", 9.5)
    c.drawString(x+r*2+5*mm, y+3.5*mm, str(value))

def page_bien(c, d):
    draw_header(c, f"{safe(d.get('type_bien'))} — {safe(d.get('adresse'))}, {safe(d.get('ville'))}")
    draw_section_title(c, "Présentation du bien", 14*mm, H-32*mm)

    desc = safe(d.get("description"), "Description non disponible.")
    style = ParagraphStyle("b", fontName="Helvetica", fontSize=9.5, textColor=GTEXTE, leading=15)
    p = Paragraph(desc, style)
    _, ph = p.wrap(W-28*mm, 9999)
    p.drawOn(c, 14*mm, H-38*mm-ph)
    bottom = H-38*mm-ph-14*mm

    draw_section_title(c, "Caractéristiques", 14*mm, bottom-2*mm)

    # picto: surface=m², type=▣, adresse/lieu=◎, ville=◉
    pills = []
    pills.append(("m²", "Surface habitable", f"{safe(d.get('surface'))} m²"))
    pills.append(("▣",  "Type de bien",       safe(d.get("type_bien"))))
    pills.append(("◎",  "Adresse",            safe(d.get("adresse"))))
    pills.append(("◉",  "Ville",              safe(d.get("ville"))))
    if d.get("surface_terrain"): pills.append(("m²", "Surface terrain",   f"{safe(d.get('surface_terrain'))} m²"))
    if d.get("annee_construct"): pills.append(("▣",  "Année construction", safe(d.get("annee_construct"))))
    if d.get("ca_ht"):           pills.append(("m²", "CA HT annuel",      pfmt(d.get("ca_ht"))))
    if d.get("loyer_annuel"):    pills.append(("m²", "Loyer annuel",      pfmt(d.get("loyer_annuel"))))
    if d.get("activite"):        pills.append(("▣",  "Activité",          safe(d.get("activite"))))

    pw, ph2, pgx, pgy = 57*mm, 16*mm, 3*mm, 3*mm
    cols = 3
    sy = bottom-20*mm
    for i, (ico, lbl, val) in enumerate(pills):
        col = i % cols; row2 = i // cols
        draw_pill_picto(c, 14*mm+col*(pw+pgx), sy-row2*(ph2+pgy), ico, lbl, val, pw, ph2)

    pb = sy-((len(pills)-1)//cols)*(ph2+pgy)-ph2-14*mm
    draw_section_title(c, "Photos du bien", 14*mm, pb)
    pw3 = (W-28*mm-6*mm)/3; ph3 = 36*mm
    for i in range(3):
        px = 14*mm+i*(pw3+3*mm); py = pb-12*mm-ph3
        c.setFillColor(GRIS); c.setStrokeColor(colors.HexColor("#DDDDDD"))
        c.roundRect(px, py, pw3, ph3, 2*mm, fill=1, stroke=1)
        c.setFillColor(colors.HexColor("#BBBBBB")); c.setFont("Helvetica", 8)
        c.drawCentredString(px+pw3/2, py+ph3/2, f"Photo {i+2}")
    draw_footer(c, 2)

# ═══════════════════════════════════════
# PAGE 3 — QUARTIER + CARTE OSM RÉELLE
# ═══════════════════════════════════════
def page_quartier(c, d):
    draw_header(c, "Quartier & environnement")
    draw_section_title(c, "Le quartier", 14*mm, H-32*mm)

    texte = safe(d.get("texte_quartier"), "Secteur dynamique de Vannes, bien desservi.")
    style = ParagraphStyle("b", fontName="Helvetica", fontSize=10, textColor=GTEXTE, leading=16)
    p = Paragraph(texte, style)
    _, ph = p.wrap(W-28*mm, 9999)
    p.drawOn(c, 14*mm, H-38*mm-ph)
    q_bottom = H-38*mm-ph-12*mm

    draw_section_title(c, "Localisation", 14*mm, q_bottom-2*mm)
    map_h = 70*mm
    map_y = q_bottom-14*mm-map_h
    map_x = 14*mm
    map_w = W-28*mm

    # ── Carte OSM réelle ──
    try:
        lat, lon = geocode_osm(safe(d.get("adresse"), "centre"), safe(d.get("ville"), "Vannes"))
        osm_img = get_osm_map_image(lat, lon, zoom=16, tiles=3)
        # Recadrer au centre pour le ratio PDF
        iw, ih = osm_img.size
        target_ratio = map_w / map_h
        if iw/ih > target_ratio:
            new_w = int(ih * target_ratio)
            left = (iw-new_w)//2
            osm_img = osm_img.crop((left, 0, left+new_w, ih))
        else:
            new_h = int(iw / target_ratio)
            top = (ih-new_h)//2
            osm_img = osm_img.crop((0, top, iw, top+new_h))
        # Convertir en ImageReader pour ReportLab
        buf = io.BytesIO()
        osm_img.save(buf, format="PNG")
        buf.seek(0)
        map_reader = ImageReader(buf)
        c.drawImage(map_reader, map_x, map_y, width=map_w, height=map_h)
        # Pin orange centré
        pin_x = map_x + map_w/2
        pin_y = map_y + map_h/2
        c.setFillColor(ORANGE)
        c.circle(pin_x, pin_y, 4*mm, fill=1, stroke=0)
        c.setFillColor(BLANC); c.setFont("Helvetica-Bold", 9)
        c.drawCentredString(pin_x, pin_y-3*mm, "✦")
        # Bulle adresse
        adr = f"{safe(d.get('adresse'))}, {safe(d.get('ville'))}"
        bw_b = len(adr)*4.2+12
        c.setFillColor(BLANC); c.setStrokeColor(colors.HexColor("#AAAAAA"))
        c.setLineWidth(0.5)
        c.roundRect(pin_x-bw_b/2, pin_y+6*mm, bw_b, 9*mm, 1.5*mm, fill=1, stroke=1)
        c.setFillColor(BLEU_F); c.setFont("Helvetica-Bold", 7)
        c.drawCentredString(pin_x, pin_y+10.5*mm, adr)
        # Bandeau crédit OSM
        c.setFillColor(colors.HexColor("#FFFFFF99"))
        c.rect(map_x, map_y, map_w, 5*mm, fill=1, stroke=0)
        c.setFillColor(colors.HexColor("#555555")); c.setFont("Helvetica", 5.5)
        c.drawRightString(map_x+map_w-2*mm, map_y+1*mm, "© OpenStreetMap contributors")
    except Exception as e:
        # Fallback si OSM échoue
        c.setFillColor(colors.HexColor("#E8F0F4"))
        c.roundRect(map_x, map_y, map_w, map_h, 3*mm, fill=1, stroke=0)
        c.setFillColor(colors.HexColor("#AAAAAA")); c.setFont("Helvetica", 9)
        c.drawCentredString(W/2, map_y+map_h/2, f"Carte non disponible — {e}")

    # Arrondi sur la carte
    c.setStrokeColor(colors.HexColor("#CCCCCC")); c.setLineWidth(1)
    c.roundRect(map_x, map_y, map_w, map_h, 3*mm, fill=0, stroke=1)

    # Points clés
    draw_section_title(c, "Points clés à proximité", 14*mm, map_y-10*mm)
    pts = [
        ("◉", "Commerces", "Services de proximité"),
        ("◎", "Transports", "Gare SNCF / Rocade"),
        ("★", "Dynamisme", "Zone commerciale active"),
        ("▣", "Parking", "Stationnement aisé"),
        ("◷", "Établissements", "Écoles & formations"),
        ("€",  "Réseau", "Secteur porteur"),
    ]
    pw2 = (W-28*mm-10*mm)/3
    for i, (ico, lbl, val) in enumerate(pts):
        col = i%3; row2 = i//3
        bx = 14*mm+col*(pw2+5*mm)
        by = map_y-22*mm-row2*13*mm
        c.setFillColor(GRIS); c.roundRect(bx, by, pw2, 11*mm, 2*mm, fill=1, stroke=0)
        c.setFillColor(BLEU); c.setFont("Helvetica-Bold", 8)
        c.drawString(bx+3*mm, by+6.5*mm, f"{ico}  {lbl}")
        c.setFillColor(GTEXTE); c.setFont("Helvetica", 7)
        c.drawString(bx+3*mm, by+2*mm, val)
    draw_footer(c, 3)

# ═══════════════════════════════════════
# PAGE 4 — COMPARABLES
# ═══════════════════════════════════════
def page_comparables(c, comparables, d):
    draw_header(c, "Biens comparables")
    draw_section_title(c, "Analyse des biens comparables", 14*mm, H-32*mm)

    style_sm = ParagraphStyle("sm", fontName="Helvetica", fontSize=9, textColor=GTEXTE, leading=13)
    intro = Paragraph("Sélection de biens comparables permettant de positionner ce bien dans son marché local.", style_sm)
    _, ih = intro.wrap(W-28*mm, 9999)
    intro.drawOn(c, 14*mm, H-40*mm-ih)

    ct = H-42*mm-ih-6*mm; ch = 44*mm
    nb = min(len(comparables) if comparables else 3, 3)
    cw = (W-28*mm-(nb-1)*4*mm)/nb if nb>0 else W-28*mm

    if not comparables:
        c.setFillColor(GRIS); c.roundRect(14*mm, ct-ch, W-28*mm, ch, 3*mm, fill=1, stroke=0)
        c.setFillColor(colors.HexColor("#AAAAAA")); c.setFont("Helvetica-Oblique", 9)
        c.drawCentredString(W/2, ct-ch/2, "Aucun comparable saisi — ajouter dans 06_Comparables")
    else:
        for i, comp in enumerate(comparables[:3]):
            cx2 = 14*mm+i*(cw+4*mm); cy2 = ct-ch
            pc = comp.get("Prix",0); sc2 = comp.get("Surface",0)
            st = comp.get("Statut","—")
            c.setFillColor(GRIS); c.roundRect(cx2, cy2, cw, ch, 3*mm, fill=1, stroke=0)
            sc_col = BLEU if st=="Vendu" else ORANGE
            c.setFillColor(sc_col); c.roundRect(cx2+cw-24*mm, cy2+ch-8*mm, 22*mm, 6.5*mm, 1*mm, fill=1, stroke=0)
            c.setFillColor(BLANC); c.setFont("Helvetica-Bold", 6.5)
            c.drawCentredString(cx2+cw-13*mm, cy2+ch-5*mm, str(st).upper())
            c.setFillColor(BLEU); c.circle(cx2+8*mm, cy2+ch-7.5*mm, 5.5*mm, fill=1, stroke=0)
            c.setFillColor(BLANC); c.setFont("Helvetica-Bold", 9)
            c.drawCentredString(cx2+8*mm, cy2+ch-9.5*mm, str(i+1))
            c.setFillColor(BLEU_F); c.setFont("Helvetica-Bold", 8.5)
            c.drawString(cx2+3*mm, cy2+ch-17*mm, str(comp.get("Adresse","—"))[:26])
            c.setFillColor(GTEXTE); c.setFont("Helvetica", 7.5)
            c.drawString(cx2+3*mm, cy2+ch-23*mm, str(comp.get("Ville","")))
            c.setFillColor(ORANGE); c.setFont("Helvetica-Bold", 13)
            c.drawString(cx2+3*mm, cy2+ch-33*mm, pfmt(pc))
            c.setFillColor(GTEXTE); c.setFont("Helvetica", 7.5)
            c.drawString(cx2+3*mm, cy2+ch-39*mm, pm2(pc,sc2))
            c.setStrokeColor(colors.HexColor("#DDDDDD")); c.setLineWidth(0.5)
            c.line(cx2+3*mm, cy2+ch-41*mm, cx2+cw-3*mm, cy2+ch-41*mm)
            c.setFillColor(GTEXTE); c.setFont("Helvetica", 7)
            c.drawString(cx2+3*mm, cy2+5*mm, f"{safe(sc2)} m²  ·  {safe(comp.get('Nb pieces','—'))} pces  ·  {safe(comp.get('Date'))}")

    sy = ct-ch-14*mm
    draw_section_title(c, "Synthèse marché", 14*mm, sy+2*mm)
    if comparables:
        try:
            pl = [float(str(x.get("Prix",0)).replace(" ","")) for x in comparables if x.get("Prix")]
            sl = [float(str(x.get("Surface",0)).replace(" ","")) for x in comparables if x.get("Surface")]
            mp = int(sum(pl)/len(pl)) if pl else 0
            mm2 = int(sum(p/s for p,s in zip(pl,sl))/len(pl)) if (pl and sl) else 0
        except: mp=mm2=0
        vs = [pfmt(mp), f"{mm2:,} €/m²".replace(",","") if mm2 else "—", str(len(comparables))]
    else:
        vs = ["—","—","0"]
    ls = ["Prix moyen constaté","Prix moyen au m²","Nb références"]
    mw = (W-28*mm-8*mm)/3
    for i, (l,v) in enumerate(zip(ls,vs)):
        mx2 = 14*mm+i*(mw+4*mm); my2 = sy-18*mm
        c.setFillColor(BLEU); c.roundRect(mx2, my2, mw, 16*mm, 2*mm, fill=1, stroke=0)
        c.setFillColor(BLANC); c.setFont("Helvetica-Bold", 11)
        c.drawCentredString(mx2+mw/2, my2+9*mm, v)
        c.setFont("Helvetica", 7); c.drawCentredString(mx2+mw/2, my2+4*mm, l)
    c.setFillColor(colors.HexColor("#999999")); c.setFont("Helvetica-Oblique", 7)
    c.drawString(14*mm, sy-22*mm, "Sources : biens saisis par le mandataire depuis les portails immobiliers (SeLoger, LeBonCoin, etc.)")
    draw_footer(c, 4)

# ═══════════════════════════════════════
# PAGE 5 — ESTIMATION
# ═══════════════════════════════════════
def page_estimation(c, d):
    draw_header(c, "Notre estimation de valeur")
    draw_section_title(c, "Positionnement prix", 14*mm, H-32*mm)
    pm = d.get("prix_estime_min") or d.get("prix")
    px = d.get("prix_estime_max") or d.get("prix")
    pv = d.get("prix_retenu") or d.get("prix")
    surface = d.get("surface")
    by2 = H-82*mm; sw = (W-28*mm)/3
    lbls = [("Fourchette basse", pfmt(pm), "Conditions défavorables"),
            ("Valeur estimée",   pfmt(pv), "Recommandée"),
            ("Fourchette haute", pfmt(px), "Marché porteur")]
    scols = [colors.HexColor("#7BAFC4"), BLEU_F, BLEU]
    for i, ((t,p,n),col) in enumerate(zip(lbls,scols)):
        sx2 = 14*mm+i*sw; sh2 = 34*mm if i==1 else 27*mm
        sy2 = by2-sh2+(6*mm if i==1 else 0)
        c.setFillColor(col); c.roundRect(sx2, sy2, sw-2*mm, sh2, 2*mm if i==1 else 1.5*mm, fill=1, stroke=0)
        c.setFillColor(BLANC); c.setFont("Helvetica", 7)
        c.drawCentredString(sx2+sw/2, sy2+sh2-8*mm, t.upper())
        c.setFont("Helvetica-Bold", 14 if i==1 else 11)
        c.drawCentredString(sx2+sw/2, sy2+sh2-20*mm, p)
        c.setFont("Helvetica", 6.5)
        c.drawCentredString(sx2+sw/2, sy2+5*mm, n)
    # Triangle
    tri_x = 14*mm+sw+sw/2; tri_y = by2-27*mm-4*mm
    tp = c.beginPath()
    tp.moveTo(tri_x, tri_y); tp.lineTo(tri_x-4*mm, tri_y-5*mm); tp.lineTo(tri_x+4*mm, tri_y-5*mm); tp.close()
    c.setFillColor(ORANGE); c.drawPath(tp, fill=1, stroke=0)
    if pv and surface:
        c.setFillColor(GTEXTE); c.setFont("Helvetica", 8.5)
        c.drawCentredString(W/2, by2-42*mm, f"Valeur estimée au m² : {pm2(pv,surface)}  ·  Surface : {safe(surface)} m²")
    ay = by2-54*mm
    draw_section_title(c, "Analyse", 14*mm, ay)
    cw2 = (W-28*mm-6*mm)/2
    c.setFillColor(colors.HexColor("#E8F4F8")); c.roundRect(14*mm, ay-52*mm, cw2, 50*mm, 2*mm, fill=1, stroke=0)
    c.setFillColor(BLEU); c.setFont("Helvetica-Bold", 8.5)
    c.drawString(18*mm, ay-7*mm, "▸ ATOUTS DU BIEN")
    for i, a in enumerate(["Emplacement commercial stratégique", f"Surface adaptée ({safe(surface)} m²)",
                             "Potentiel de développement", "Secteur à forte demande"]):
        c.setFillColor(GTEXTE); c.setFont("Helvetica", 8.5)
        c.drawString(18*mm, ay-16*mm-i*10*mm, f"·  {a}")
    c.setFillColor(colors.HexColor("#FDF0E8")); c.roundRect(14*mm+cw2+6*mm, ay-52*mm, cw2, 50*mm, 2*mm, fill=1, stroke=0)
    c.setFillColor(ORANGE); c.setFont("Helvetica-Bold", 8.5)
    c.drawString(18*mm+cw2+6*mm, ay-7*mm, "▸ POINTS DE VIGILANCE")
    for i, v in enumerate(["Vérifier l'état technique", "Analyser charges et fiscalité", "Confirmer la conformité"]):
        c.setFillColor(GTEXTE); c.setFont("Helvetica", 8.5)
        c.drawString(18*mm+cw2+6*mm, ay-16*mm-i*10*mm, f"·  {v}")
    disc = Paragraph("Estimation indicative établie sur la base des caractéristiques du bien et des comparables du marché local. Ne constitue pas une expertise immobilière au sens légal.",
        ParagraphStyle("d", fontName="Helvetica-Oblique", fontSize=6.5, textColor=colors.HexColor("#AAAAAA"), leading=9))
    _, dp = disc.wrap(W-28*mm, 9999)
    disc.drawOn(c, 14*mm, ay-55*mm-dp)
    draw_footer(c, 5)

# ═══════════════════════════════════════
# PAGE 6 — AGENCE
# ═══════════════════════════════════════
def page_agence(c):
    c.setFillColor(BLEU); c.rect(0, H*0.5, W, H*0.5, fill=1, stroke=0)
    c.setFillColor(BLANC); c.rect(0, 0, W, H*0.5, fill=1, stroke=0)
    draw_logo_on_white(c, W-54*mm, H-56*mm, w=36*mm)
    c.setFillColor(BLANC); c.setFont("Helvetica", 11)
    c.drawString(14*mm, H-20*mm, "VOTRE PARTENAIRE EN IMMOBILIER COMMERCIAL")
    c.setFont("Helvetica-Bold", 28)
    c.drawString(14*mm, H-38*mm, "Barbier Immobilier")
    c.setFont("Helvetica", 14); c.setFillColor(colors.HexColor("#FFFFFFCC"))
    c.drawString(14*mm, H-50*mm, "Votre projet devient le nôtre")
    c.setFillColor(ORANGE); c.rect(14*mm, H-54*mm, 50*mm, 2.5*mm, fill=1, stroke=0)
    stats = [("33 ans","d'expertise locale"),("+5 000","clients accompagnés"),("3 métiers","vente · location · cession")]
    sw2 = (W-28*mm)/3
    for i,(num,lbl) in enumerate(stats):
        sx3 = 14*mm+i*sw2; sy3 = H*0.52+2*mm
        c.setFillColor(BLANC); c.setFont("Helvetica-Bold", 20); c.drawString(sx3+3*mm, sy3+12*mm, num)
        c.setFont("Helvetica", 9); c.setFillColor(colors.HexColor("#FFFFFFBB")); c.drawString(sx3+3*mm, sy3+6*mm, lbl)
    services = [("Estimation & Valorisation","Analyse précise de la valeur vénale basée sur les données du marché local et notre expertise terrain."),
                ("Vente & Transaction","Diffusion multi-portails, sélection d'acquéreurs qualifiés, négociation et suivi jusqu'à la signature."),
                ("Location Commerciale","Recherche de locataires, rédaction des baux, gestion locative et suivi des relations."),
                ("Cession d'Entreprise","Accompagnement expert pour la cession ou reprise de fonds de commerce et de sociétés.")]
    sws = (W-28*mm-8*mm)/2; shs = 32*mm; sy_s = H*0.48-4*mm
    for i,(title,desc) in enumerate(services):
        col = i%2; row2 = i//2
        sx4 = 14*mm+col*(sws+8*mm); sy4 = sy_s-row2*(shs+5*mm)
        c.setFillColor(GRIS); c.roundRect(sx4, sy4-shs, sws, shs, 2*mm, fill=1, stroke=0)
        c.setFillColor(ORANGE); c.rect(sx4, sy4-shs, 3*mm, shs, fill=1, stroke=0)
        c.setFillColor(BLEU_F); c.setFont("Helvetica-Bold", 10); c.drawString(sx4+6*mm, sy4-8*mm, title)
        p = Paragraph(desc, ParagraphStyle("ds", fontName="Helvetica", fontSize=8.5, textColor=GTEXTE, leading=12))
        _, ph2 = p.wrap(sws-10*mm, 9999); p.drawOn(c, sx4+6*mm, sy4-shs+5*mm)
    c.setFillColor(BLEU_F); c.roundRect(14*mm, 14*mm, W-28*mm, 20*mm, 2*mm, fill=1, stroke=0)
    c.setFillColor(BLANC); c.setFont("Helvetica-Bold", 10)
    c.drawString(20*mm, 28*mm, "2 place Albert Einstein, 56000 Vannes")
    c.setFont("Helvetica", 9)
    c.drawString(20*mm, 21*mm, "02.97.47.11.11  ·  contact@barbierimmobilier.com  ·  barbierimmobilier.com")
    draw_footer(c, 6)

# ═══════════════════════════════════════
# MAIN
# ═══════════════════════════════════════
def generate_pdf(d, comparables=[]):
    buf = io.BytesIO()
    cv = canvas.Canvas(buf, pagesize=A4)
    cv.setTitle(f"Dossier — {d.get('reference','')}")
    page_couverture(cv, d); cv.showPage()
    page_bien(cv, d);        cv.showPage()
    page_quartier(cv, d);    cv.showPage()
    page_comparables(cv, comparables, d); cv.showPage()
    page_estimation(cv, d);  cv.showPage()
    page_agence(cv);          cv.showPage()
    cv.save(); buf.seek(0)
    return buf.read()



@app.route("/dossier-vente", methods=["GET", "POST"])
def dossier_vente():
    try:
        data = request.get_json(silent=True) or {}
        row_id    = request.args.get("row_id")    or data.get("row_id")
        reference = request.args.get("reference") or data.get("reference")
        if not row_id and not reference:
            return jsonify({"error": "row_id ou reference requis"}), 400

        at, uuid = _st_token()

        # Récupérer la ligne avec noms lisibles
        params = urllib.parse.urlencode({"table_name": "01_Biens", "convert_keys": "true", "limit": 300})
        req2 = urllib.request.Request(
            f"https://cloud.seatable.io/api-gateway/api/v2/dtables/{uuid}/rows/?{params}",
            headers={"Authorization": f"Token {at}"})
        with urllib.request.urlopen(req2) as resp:
            rows = json.load(resp)["rows"]

        row = next((r2 for r2 in rows if
            (row_id and r2.get("_id") == row_id) or
            (reference and r2.get("Reference") == reference)), None)
        if not row:
            return jsonify({"error": "Bien non trouvé"}), 404

        ref = row.get("Reference", "")

        # Comparables
        try:
            comp_rows = []
            sql = f"SELECT * FROM `06_Comparables` WHERE `Reference bien` = '{ref}' LIMIT 3"
            payload2 = json.dumps({"sql": sql}).encode()
            req3 = urllib.request.Request(
                f"https://cloud.seatable.io/api-gateway/api/v2/dtables/{uuid}/sql",
                data=payload2, method="POST",
                headers={"Authorization": f"Token {at}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req3) as resp2:
                comp_rows = json.load(resp2).get("results", [])
        except:
            comp_rows = []

        # Texte quartier
        texte_q = row.get("Texte quartier") or ""
        if not texte_q:
            try:
                texte_q = gpt_texte_quartier(
                    adresse=row.get("Adresse",""),
                    ville=row.get("Ville","Vannes"),
                    type_bien=row.get("Type de bien","bien commercial"),
                    surface=row.get("Surface","")
                )
            except:
                texte_q = "Secteur dynamique de Vannes, bien desservi et facilement accessible."

        d = {
            "reference":       ref,
            "type_bien":       row.get("Type de bien",""),
            "adresse":         row.get("Adresse",""),
            "code_postal":     row.get("Code postal","56000"),
            "ville":           row.get("Ville","Vannes"),
            "surface":         row.get("Surface"),
            "surface_terrain": row.get("Surface terrain"),
            "prix":            row.get("Prix"),
            "prix_estime_min": row.get("Prix estime min"),
            "prix_estime_max": row.get("Prix estime max"),
            "prix_retenu":     row.get("Prix retenu"),
            "negociateur":     row.get("Negociateur","Barbier Immobilier"),
            "description":     row.get("Description courte",""),
            "annee_construct": row.get("Annee construction"),
            "activite":        row.get("Activite"),
            "ca_ht":           row.get("CA HT annuel"),
            "loyer_annuel":    row.get("Loyer annuel"),
            "texte_quartier":  texte_q,
        }

        pdf_bytes = generate_pdf(d, comp_rows)
        filename  = f"dossier-vente-{ref}.pdf"
        return Response(pdf_bytes, mimetype="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={filename}"})

    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500

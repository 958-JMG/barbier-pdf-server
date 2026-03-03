#!/usr/bin/env python3
"""
Générateur PDF — Avis de Valeur PRO
Barbier Immobilier / Système Barbier 2.0
Usage: python3 generate_avis_valeur.py <row_id> <output_path>
       python3 generate_avis_valeur.py --test   (données fictives)
"""

import sys
import os
import json
import requests
from datetime import datetime
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm, cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether, PageBreak
)
from reportlab.platypus import Image as RLImage
from reportlab.pdfgen import canvas

# ─── PALETTE BARBIER ────────────────────────────────────────────────────────
NAVY       = colors.HexColor('#1A2B4A')   # bleu marine principal
GOLD       = colors.HexColor('#B8971F')   # or/doré accent
LIGHT_GREY = colors.HexColor('#F5F5F5')   # fond tableaux
MID_GREY   = colors.HexColor('#888888')   # texte secondaire
WHITE      = colors.white
BLACK      = colors.HexColor('#1C1C1C')

# ─── SEATABLE CONFIG ────────────────────────────────────────────────────────
SEATABLE_APP_TOKEN = "4fcb9688f14c8c6b076a5612c0dbadc0d7e7cf41"
SEATABLE_SERVER    = "https://cloud.seatable.io"
SEATABLE_TABLE     = "01_Biens"

# ─── DONNÉES DE TEST ────────────────────────────────────────────────────────
TEST_DATA = {
    "Reference": "BAR-00042",
    "Type de bien": "Local commercial",
    "Surface": 185,
    "Adresse": "12 Rue du Morbihan",
    "Ville": "Vannes",
    "Code postal": "56000",
    "Prix": 385000,
    "Negociateur": "Maïwen Le Gall",
    "Prix sans décote": 410000,
    "Prix avec décote": 365000,
    "Prix estime min": 350000,
    "Prix estime max": 430000,
    "Prix retenu": 390000,
    "Etat du bien": "Bon état",
    "Avis de valeur": """**SYNTHÈSE DE L'ANALYSE**

L'analyse comparative de marché réalisée sur la commune de Vannes et ses environs immédiats révèle un marché de l'immobilier commercial en tension modérée sur le segment des locaux en pied d'immeuble de centre-ville.

**Références DVF retenues :**
- Local 170 m² — Rue de la République, Vannes — vendu 395 000 € (2 323 €/m²) — janv. 2024
- Local 200 m² — Avenue Victor Hugo, Vannes — vendu 420 000 € (2 100 €/m²) — mars 2023
- Local 160 m² — Place des Lices, Vannes — vendu 340 000 € (2 125 €/m²) — oct. 2023

**Fourchette de valeur estimée :** 350 000 € – 430 000 €
Valeur médiane ressortant des comparables : 2 150 €/m² × 185 m² = **397 750 €**

**Facteurs différenciants :**
✓ Emplacement n°1 bis — flux piéton satisfaisant
✓ Double vitrine — atout commercial significatif
△ Hauteur sous plafond limitée (2,70 m) — légère décote sur segment stockage
△ Absence de parking privatif dédié

**Conclusion :** Dans le contexte actuel du marché vannetais, la valeur vénale de ce bien est estimée à **390 000 €**, représentant un positionnement cohérent avec les transactions récentes et les caractéristiques intrinsèques du bien.

Cette estimation est établie conformément aux normes MRICS et aux recommandations de la Charte de l'Expertise en Évaluation Immobilière.""",
    "Notes internes": "Client pressé de vendre. Délai souhaité : 3 mois maximum.",
    "Date": datetime.now().strftime("%d/%m/%Y"),
}


def fetch_seatable_row(row_id: str) -> dict:
    """Récupère les données d'une ligne SeaTable via l'API Gateway v5.3+."""
    auth_resp = requests.get(
        f"{SEATABLE_SERVER}/api/v2.1/dtable/app-access-token/",
        headers={"Authorization": f"Token {SEATABLE_APP_TOKEN}"},
        timeout=10
    )
    auth_resp.raise_for_status()
    auth_data = auth_resp.json()
    access_token = auth_data["access_token"]
    dtable_uuid  = auth_data["dtable_uuid"]
    headers = {"Authorization": f"Token {access_token}"}

    # Métadonnées pour décoder single-select
    meta = requests.get(
        f"{SEATABLE_SERVER}/api-gateway/api/v2/dtables/{dtable_uuid}/metadata/",
        headers=headers, timeout=10
    ).json()

    col_map = {}
    options_map = {}
    for t in meta.get("metadata", {}).get("tables", []):
        if t["name"] == SEATABLE_TABLE:
            for col in t["columns"]:
                col_map[col["key"]] = col["name"]
                if col["type"] == "single-select":
                    data = col.get("data") or {}
                    options_map[col["name"]] = {str(o["id"]): o["name"] for o in data.get("options", [])}

    # Récupérer la ligne cible
    rows = requests.get(
        f"{SEATABLE_SERVER}/api-gateway/api/v2/dtables/{dtable_uuid}/rows/",
        headers=headers,
        params={"table_name": SEATABLE_TABLE, "limit": 200},
        timeout=15
    ).json().get("rows", [])

    target_raw = next((r for r in rows if r.get("_id") == row_id), None)
    if not target_raw:
        raise ValueError(f"Aucune ligne trouvée pour row_id={row_id}")

    # Décoder single-select IDs -> texte
    decoded = {}
    for k, v in target_raw.items():
        col_name = col_map.get(k, k)
        if col_name in options_map and v is not None:
            decoded[col_name] = options_map[col_name].get(str(v), v)
        else:
            decoded[col_name] = v
    return decoded


def format_price(value) -> str:
    """Formate un nombre en prix €."""
    if value is None:
        return "N/D"
    try:
        return f"{int(float(value)):,} €".replace(",", " ")
    except (ValueError, TypeError):
        return str(value)


def format_surface(value) -> str:
    if value is None:
        return "N/D"
    try:
        return f"{int(float(value))} m²"
    except (ValueError, TypeError):
        return str(value)


# ─── HEADER / FOOTER via canvas ─────────────────────────────────────────────
class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []
        self.bien_ref = kwargs.get("bien_ref", "")

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_number(num_pages)
            super().showPage()
        super().save()

    def draw_page_number(self, page_count):
        page_num = self._saved_page_states.index(
            {k: v for k, v in self.__dict__.items() if k in self._saved_page_states[0]}
        ) + 1 if hasattr(self, '_saved_page_states') else 1

        w, h = A4
        # Ligne de pied
        self.setStrokeColor(GOLD)
        self.setLineWidth(0.5)
        self.line(15*mm, 18*mm, w - 15*mm, 18*mm)

        self.setFont("Helvetica", 7)
        self.setFillColor(MID_GREY)
        self.drawString(15*mm, 12*mm, "Barbier Immobilier — Réseau TerraLink — SIREN 123 456 789")
        self.drawString(15*mm, 8*mm,  "Document confidentiel — Avis de valeur non opposable — Établi conformément aux normes MRICS")

        self.setFont("Helvetica-Bold", 7)
        self.setFillColor(NAVY)
        self.drawRightString(w - 15*mm, 10*mm, f"Page {self._pageNumber} / {page_count}")


def build_pdf(data: dict, output_path: str):
    """Génère le PDF Avis de Valeur PRO."""
    w, h = A4
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=18*mm,
        rightMargin=18*mm,
        topMargin=22*mm,
        bottomMargin=28*mm,
        title=f"Avis de Valeur — {data.get('Reference', '')}",
        author="Barbier Immobilier",
        subject="Avis de Valeur Professionnel MRICS",
    )

    styles = getSampleStyleSheet()

    # ── Styles personnalisés ──
    def S(name, **kw):
        return ParagraphStyle(name, **kw)

    sTitle = S("sTitle",
               fontName="Helvetica-Bold", fontSize=22,
               textColor=WHITE, alignment=TA_LEFT, leading=28)

    sSubTitle = S("sSubTitle",
                  fontName="Helvetica", fontSize=10,
                  textColor=GOLD, alignment=TA_LEFT, leading=14)

    sRef = S("sRef",
             fontName="Helvetica-Bold", fontSize=11,
             textColor=GOLD, alignment=TA_LEFT)

    sSection = S("sSection",
                 fontName="Helvetica-Bold", fontSize=10,
                 textColor=NAVY, spaceBefore=10, spaceAfter=4,
                 borderPad=4)

    sNormal = S("sNormal",
                fontName="Helvetica", fontSize=9,
                textColor=BLACK, leading=13, alignment=TA_JUSTIFY)

    sSmall = S("sSmall",
               fontName="Helvetica", fontSize=8,
               textColor=MID_GREY, leading=11)

    sPrice = S("sPrice",
               fontName="Helvetica-Bold", fontSize=18,
               textColor=NAVY, alignment=TA_CENTER, leading=24)

    sPriceLabel = S("sPriceLabel",
                    fontName="Helvetica", fontSize=8,
                    textColor=MID_GREY, alignment=TA_CENTER)

    sDisclaimer = S("sDisclaimer",
                    fontName="Helvetica-Oblique", fontSize=7.5,
                    textColor=MID_GREY, alignment=TA_CENTER, leading=10)

    story = []

    # ═══════════════════════════════════════════════════════════
    # BANDEAU HEADER — fond marine
    # ═══════════════════════════════════════════════════════════
    col_w = (w - 36*mm)
    header_data = [[
        Paragraph("AVIS DE VALEUR PROFESSIONNEL", sTitle),
        Paragraph(
            f"Réf. {data.get('Reference','—')} &nbsp;|&nbsp; "
            f"{datetime.now().strftime('%d %B %Y').capitalize()}",
            sRef
        )
    ]]
    header_table = Table(header_data, colWidths=[col_w * 0.65, col_w * 0.35])
    header_table.setStyle(TableStyle([
        ('BACKGROUND',  (0,0), (-1,-1), NAVY),
        ('VALIGN',      (0,0), (-1,-1), 'MIDDLE'),
        ('LEFTPADDING', (0,0), (-1,-1), 10),
        ('RIGHTPADDING',(0,0), (-1,-1), 10),
        ('TOPPADDING',  (0,0), (-1,-1), 14),
        ('BOTTOMPADDING',(0,0),(-1,-1), 14),
        ('ALIGN',       (1,0), (1,0), 'RIGHT'),
    ]))
    story.append(header_table)

    # Sous-titre agence
    sub_data = [[
        Paragraph("Barbier Immobilier — Réseau TerraLink — Expert Immobilier Commercial", sSubTitle),
        Paragraph("Document établi conformément aux normes MRICS", S("x", fontName="Helvetica-Oblique", fontSize=8, textColor=GOLD, alignment=TA_RIGHT))
    ]]
    sub_table = Table(sub_data, colWidths=[col_w * 0.65, col_w * 0.35])
    sub_table.setStyle(TableStyle([
        ('BACKGROUND',   (0,0), (-1,-1), colors.HexColor('#0D1E35')),
        ('LEFTPADDING',  (0,0), (-1,-1), 10),
        ('RIGHTPADDING', (0,0), (-1,-1), 10),
        ('TOPPADDING',   (0,0), (-1,-1), 6),
        ('BOTTOMPADDING',(0,0), (-1,-1), 6),
        ('VALIGN',       (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(sub_table)
    story.append(Spacer(1, 6*mm))

    # ═══════════════════════════════════════════════════════════
    # SECTION 1 — IDENTIFICATION DU BIEN
    # ═══════════════════════════════════════════════════════════
    def section_title(text):
        t = Table([[Paragraph(f"&nbsp; {text}", S("st",
               fontName="Helvetica-Bold", fontSize=9.5,
               textColor=WHITE))]],
               colWidths=[col_w])
        t.setStyle(TableStyle([
            ('BACKGROUND',   (0,0),(-1,-1), NAVY),
            ('TOPPADDING',   (0,0),(-1,-1), 5),
            ('BOTTOMPADDING',(0,0),(-1,-1), 5),
            ('LEFTPADDING',  (0,0),(-1,-1), 8),
        ]))
        return t

    story.append(section_title("1.  IDENTIFICATION DU BIEN"))
    story.append(Spacer(1, 3*mm))

    adresse_complete = f"{data.get('Adresse','—')}, {data.get('Code postal','')} {data.get('Ville','')}"
    id_rows = [
        ["Type de bien",  data.get("Type de bien", "—"),    "Référence",    data.get("Reference", "—")],
        ["Adresse",       adresse_complete,                  "Surface",      format_surface(data.get("Surface"))],
        ["État du bien",  data.get("Etat du bien", "—"),    "Négociateur",  data.get("Negociateur", "—")],
    ]
    cw1, cw2 = col_w * 0.18, col_w * 0.32
    id_table = Table(id_rows, colWidths=[cw1, cw2, cw1, cw2])
    id_table.setStyle(TableStyle([
        ('BACKGROUND',    (0,0), (0,-1), LIGHT_GREY),
        ('BACKGROUND',    (2,0), (2,-1), LIGHT_GREY),
        ('FONTNAME',      (0,0), (0,-1), 'Helvetica-Bold'),
        ('FONTNAME',      (2,0), (2,-1), 'Helvetica-Bold'),
        ('FONTNAME',      (1,0), (1,-1), 'Helvetica'),
        ('FONTNAME',      (3,0), (3,-1), 'Helvetica'),
        ('FONTSIZE',      (0,0), (-1,-1), 8.5),
        ('TEXTCOLOR',     (0,0), (0,-1), NAVY),
        ('TEXTCOLOR',     (2,0), (2,-1), NAVY),
        ('GRID',          (0,0), (-1,-1), 0.3, colors.HexColor('#DDDDDD')),
        ('TOPPADDING',    (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('LEFTPADDING',   (0,0), (-1,-1), 7),
        ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(id_table)
    story.append(Spacer(1, 5*mm))

    # ═══════════════════════════════════════════════════════════
    # SECTION 2 — SYNTHÈSE DES VALEURS
    # ═══════════════════════════════════════════════════════════
    story.append(section_title("2.  SYNTHÈSE DES VALEURS ESTIMÉES"))
    story.append(Spacer(1, 4*mm))

    # Cartes de prix
    price_items = [
        ("Valeur basse",   data.get("Prix estime min"),   LIGHT_GREY, NAVY),
        ("Valeur retenue", data.get("Prix retenu"),        NAVY,       WHITE),
        ("Valeur haute",   data.get("Prix estime max"),    LIGHT_GREY, NAVY),
    ]

    def price_card(label, value, bg, tc):
        lbl_style = S("pl", fontName="Helvetica", fontSize=7.5, textColor=MID_GREY if bg != NAVY else colors.HexColor('#AABBCC'), alignment=TA_CENTER)
        val_style = S("pv", fontName="Helvetica-Bold", fontSize=16 if bg == NAVY else 13, textColor=tc, alignment=TA_CENTER, leading=20)
        inner = Table([
            [Paragraph(label, lbl_style)],
            [Paragraph(format_price(value), val_style)],
        ], colWidths=[col_w/3 - 4*mm])
        inner.setStyle(TableStyle([
            ('BACKGROUND',   (0,0),(-1,-1), bg),
            ('TOPPADDING',   (0,0),(-1,-1), 8),
            ('BOTTOMPADDING',(0,0),(-1,-1), 8),
            ('LEFTPADDING',  (0,0),(-1,-1), 4),
            ('RIGHTPADDING', (0,0),(-1,-1), 4),
            ('BOX',          (0,0),(-1,-1), 1 if bg == NAVY else 0.3, GOLD if bg == NAVY else colors.HexColor('#DDDDDD')),
        ]))
        return inner

    cards_row = [[price_card(*p) for p in price_items]]
    cards_table = Table(cards_row, colWidths=[col_w/3]*3, hAlign='CENTER')
    cards_table.setStyle(TableStyle([
        ('ALIGN',   (0,0),(-1,-1), 'CENTER'),
        ('VALIGN',  (0,0),(-1,-1), 'MIDDLE'),
        ('LEFTPADDING',  (0,0),(-1,-1), 2),
        ('RIGHTPADDING', (0,0),(-1,-1), 2),
    ]))
    story.append(cards_table)
    story.append(Spacer(1, 4*mm))

    # Ligne décote
    decote_rows = [
        ["", "Prix", "Base de calcul"],
        ["Prix sans décote (valeur intrinsèque)", format_price(data.get("Prix sans décote")), "Valeur vénale libre de tout occupant"],
        ["Prix avec décote (valeur occupée)",     format_price(data.get("Prix avec décote")), "Abattement occupation / travaux estimés"],
        ["Prix affiché recommandé",               format_price(data.get("Prix")),             "Prix de mise en marché conseillé"],
    ]
    dec_table = Table(decote_rows, colWidths=[col_w*0.45, col_w*0.25, col_w*0.30])
    dec_table.setStyle(TableStyle([
        ('BACKGROUND',    (0,0), (-1,0),  NAVY),
        ('TEXTCOLOR',     (0,0), (-1,0),  WHITE),
        ('FONTNAME',      (0,0), (-1,0),  'Helvetica-Bold'),
        ('FONTSIZE',      (0,0), (-1,-1), 8.5),
        ('BACKGROUND',    (0,3), (-1,3),  colors.HexColor('#EEF2F8')),
        ('FONTNAME',      (0,3), (-1,3),  'Helvetica-Bold'),
        ('TEXTCOLOR',     (0,3), (-1,3),  NAVY),
        ('GRID',          (0,0), (-1,-1), 0.3, colors.HexColor('#CCCCCC')),
        ('TOPPADDING',    (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('LEFTPADDING',   (0,0), (-1,-1), 8),
        ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
        ('ALIGN',         (1,0), (1,-1),  'CENTER'),
    ]))
    story.append(dec_table)
    story.append(Spacer(1, 5*mm))

    # ═══════════════════════════════════════════════════════════
    # SECTION 3 — ANALYSE DE MARCHÉ & MOTIVATION
    # ═══════════════════════════════════════════════════════════
    story.append(section_title("3.  ANALYSE DE MARCHÉ ET MOTIVATION DE L'ESTIMATION"))
    story.append(Spacer(1, 3*mm))

    import re
    avis_text = data.get("Avis de valeur", "Analyse non disponible.")
    # Convertir markdown **gras** -> <b>gras</b> proprement
    def md_to_rl(text):
        # Remplacer ** par balises b alternées
        parts = text.split("**")
        result = []
        for i, part in enumerate(parts):
            if i % 2 == 1:
                result.append(f"<b>{part}</b>")
            else:
                result.append(part)
        cleaned = "".join(result)
        # Sauts de ligne
        cleaned = cleaned.replace("\n\n", "<br/><br/>").replace("\n", "<br/>")
        # Caractères spéciaux XML
        cleaned = cleaned.replace("&", "&amp;").replace("&amp;amp;", "&amp;")
        # Remettre les balises HTML
        cleaned = cleaned.replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")
        cleaned = cleaned.replace("&lt;br/&gt;", "<br/>")
        return cleaned
    avis_clean = md_to_rl(avis_text)

    # Boîte encadrée
    avis_inner = Table([[Paragraph(avis_clean, S("av",
        fontName="Helvetica", fontSize=8.5, leading=13,
        textColor=BLACK, alignment=TA_JUSTIFY))
    ]], colWidths=[col_w - 8*mm])
    avis_inner.setStyle(TableStyle([
        ('BACKGROUND',   (0,0),(-1,-1), colors.HexColor('#FAFAFA')),
        ('BOX',          (0,0),(-1,-1), 0.5, colors.HexColor('#CCCCCC')),
        ('LEFTPADDING',  (0,0),(-1,-1), 10),
        ('RIGHTPADDING', (0,0),(-1,-1), 10),
        ('TOPPADDING',   (0,0),(-1,-1), 10),
        ('BOTTOMPADDING',(0,0),(-1,-1), 10),
    ]))
    story.append(avis_inner)
    story.append(Spacer(1, 5*mm))

    # ═══════════════════════════════════════════════════════════
    # SECTION 4 — SIGNATURE & VALIDATION
    # ═══════════════════════════════════════════════════════════
    story.append(section_title("4.  VALIDATION ET SIGNATURE"))
    story.append(Spacer(1, 4*mm))

    sig_rows = [
        ["Établi par :", data.get("Negociateur", "—"),   "Date :", datetime.now().strftime("%d/%m/%Y")],
        ["Approuvé par :", "Laurent Baradu — Dirigeant", "Signature :", ""],
    ]
    sig_table = Table(sig_rows, colWidths=[cw1, cw2, cw1, cw2])
    sig_table.setStyle(TableStyle([
        ('BACKGROUND',    (0,0), (0,-1), LIGHT_GREY),
        ('BACKGROUND',    (2,0), (2,-1), LIGHT_GREY),
        ('FONTNAME',      (0,0), (0,-1), 'Helvetica-Bold'),
        ('FONTNAME',      (2,0), (2,-1), 'Helvetica-Bold'),
        ('FONTSIZE',      (0,0), (-1,-1), 8.5),
        ('TEXTCOLOR',     (0,0), (0,-1), NAVY),
        ('TEXTCOLOR',     (2,0), (2,-1), NAVY),
        ('GRID',          (0,0), (-1,-1), 0.3, colors.HexColor('#DDDDDD')),
        ('TOPPADDING',    (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('LEFTPADDING',   (0,0), (-1,-1), 7),
        ('ROWBACKGROUNDS',(0,1),(-1,1),  [colors.HexColor('#F0F4FA')]),
        ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(sig_table)

    # Zone signature manuscrite
    story.append(Spacer(1, 4*mm))
    sign_box = Table([["Zone de signature :"]], colWidths=[col_w])
    sign_box.setStyle(TableStyle([
        ('FONTNAME',     (0,0),(-1,-1), 'Helvetica-Oblique'),
        ('FONTSIZE',     (0,0),(-1,-1), 7.5),
        ('TEXTCOLOR',    (0,0),(-1,-1), MID_GREY),
        ('BOX',          (0,0),(-1,-1), 0.3, colors.HexColor('#CCCCCC')),
        ('TOPPADDING',   (0,0),(-1,-1), 30),
        ('BOTTOMPADDING',(0,0),(-1,-1), 30),
        ('LEFTPADDING',  (0,0),(-1,-1), 10),
    ]))
    story.append(sign_box)
    story.append(Spacer(1, 4*mm))

    # ═══════════════════════════════════════════════════════════
    # DISCLAIMER MRICS
    # ═══════════════════════════════════════════════════════════
    story.append(HRFlowable(width=col_w, thickness=0.5, color=GOLD))
    story.append(Spacer(1, 2*mm))
    disclaimer = (
        "Cet avis de valeur est établi conformément aux normes MRICS et à la Charte de l'Expertise en Évaluation Immobilière (5e édition). "
        "Il est fondé sur les informations communiquées par le client et les données de marché disponibles à la date d'établissement. "
        "Il ne constitue pas une évaluation formelle au sens juridique et n'est pas opposable aux tiers. "
        "Barbier Immobilier décline toute responsabilité quant à l'utilisation qui pourrait en être faite en dehors du cadre pour lequel il a été établi. "
        "Document confidentiel — Reproduction interdite sans autorisation écrite de Barbier Immobilier."
    )
    story.append(Paragraph(disclaimer, sDisclaimer))

    # ─── BUILD ──────────────────────────────────────────────────
    doc.build(story)
    print(f"✓ PDF généré : {output_path}")


def main():
    if len(sys.argv) < 2 or sys.argv[1] == "--test":
        print("⚙  Mode test — données fictives")
        output = "/home/claude/avis_valeur_test.pdf"
        build_pdf(TEST_DATA, output)
    elif len(sys.argv) >= 3:
        row_id     = sys.argv[1]
        output     = sys.argv[2]
        print(f"⚙  Récupération SeaTable row_id={row_id}")
        data = fetch_seatable_row(row_id)
        data["Date"] = datetime.now().strftime("%d/%m/%Y")
        build_pdf(data, output)
    else:
        print("Usage: python3 generate_avis_valeur.py <row_id> <output.pdf>")
        print("       python3 generate_avis_valeur.py --test")
        sys.exit(1)


if __name__ == "__main__":
    main()

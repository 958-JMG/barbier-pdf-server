"""
Serveur Flask — Générateur PDF Avis de Valeur PRO
Barbier Immobilier / Système Barbier 2.0
"""
from flask import Flask, request, send_file, jsonify, redirect
import tempfile, os, traceback, requests
from datetime import datetime
from generate_avis_valeur import build_pdf, fetch_seatable_row

app = Flask(__name__)

SECRET_TOKEN = os.environ.get("SECRET_TOKEN", "barbier2024secret")
APP_TOKEN = os.environ.get("SEATABLE_APP_TOKEN", "4fcb9688f14c8c6b076a5612c0dbadc0d7e7cf41")


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "Barbier PDF Generator", "version": "1.1"})


@app.route("/valider-bien", methods=["GET"])
def valider_bien():
    reference = request.args.get("reference", "")
    if not reference:
        return "<h2>Erreur : référence manquante</h2>", 400

    try:
        # Token SeaTable
        tr = requests.get(
            "https://cloud.seatable.io/api/v2.1/dtable/app-access-token/",
            headers={"Authorization": f"Token {APP_TOKEN}"},
            timeout=10
        ).json()
        at = tr["access_token"]
        uuid = tr["dtable_uuid"]

        # Chercher _id via Reference
        sql_r = requests.post(
            f"https://cloud.seatable.io/api-gateway/api/v2/dtables/{uuid}/sql",
            headers={"Authorization": f"Token {at}", "Content-Type": "application/json"},
            json={"sql": f"SELECT _id FROM `01_Biens` WHERE `Reference` = '{reference}' LIMIT 1"},
            timeout=10
        ).json()
        row_id = sql_r.get("results", [{}])[0].get("_id")
        if not row_id:
            return f"<h2>Bien {reference} introuvable</h2>", 404

        # Mettre à jour statut → À publier
        requests.put(
            f"https://cloud.seatable.io/api-gateway/api/v2/dtables/{uuid}/rows/",
            headers={"Authorization": f"Token {at}", "Content-Type": "application/json"},
            json={"table_name": "01_Biens", "updates": [{"row_id": row_id, "row": {"Statut annonce": "À publier"}}]},
            timeout=10
        )

        # Page de confirmation
        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
body{{font-family:Arial,sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;background:#f0f4f8}}
.box{{background:white;border-radius:16px;padding:50px;text-align:center;box-shadow:0 4px 20px rgba(0,0,0,0.1);max-width:480px}}
.icon{{font-size:70px;margin-bottom:20px}}
h1{{color:#2d7d46;font-size:26px}}
p{{color:#555;font-size:16px}}
</style></head>
<body><div class="box">
<div class="icon">✅</div>
<h1>Fiche validée !</h1>
<p>La fiche <b>{reference}</b> est maintenant <b>À publier</b>.</p>
<p style="margin-top:20px;color:#888;font-size:14px">La génération des annonces démarre automatiquement.</p>
</div></body></html>"""
        return html, 200

    except Exception as e:
        traceback.print_exc()
        return f"<h2>Erreur : {str(e)}</h2>", 500


@app.route("/valider-avis", methods=["GET"])
def valider_avis():
    reference = request.args.get("ref", request.args.get("reference", ""))
    if not reference:
        return "<h2>Erreur : référence manquante</h2>", 400
    try:
        tr = requests.get("https://cloud.seatable.io/api/v2.1/dtable/app-access-token/",
            headers={"Authorization": f"Token {APP_TOKEN}"}, timeout=10).json()
        at = tr["access_token"]
        uuid = tr["dtable_uuid"]
        sql_r = requests.post(f"https://cloud.seatable.io/api-gateway/api/v2/dtables/{uuid}/sql",
            headers={"Authorization": f"Token {at}", "Content-Type": "application/json"},
            json={"sql": f"SELECT _id FROM `01_Biens` WHERE `Reference` = '{reference}' LIMIT 1"},
            timeout=10).json()
        row_id = sql_r.get("results", [{}])[0].get("_id")
        if not row_id:
            return f"<h2>Bien {reference} introuvable</h2>", 404
        requests.put(f"https://cloud.seatable.io/api-gateway/api/v2/dtables/{uuid}/rows/",
            headers={"Authorization": f"Token {at}", "Content-Type": "application/json"},
            json={"table_name": "01_Biens", "updates": [{"row_id": row_id, "row": {
                "Statut avis valeur": "Validé mandataire",
                "Valide par negociateur": True
            }}]}, timeout=10)
        return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>body{{font-family:Arial,sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;background:#f0f4f8}}
.box{{background:white;border-radius:16px;padding:50px;text-align:center;box-shadow:0 4px 20px rgba(0,0,0,0.1);max-width:480px}}
.icon{{font-size:70px}}.h1{{color:#2d7d46;font-size:26px}}p{{color:#555}}</style></head>
<body><div class="box"><div class="icon">✅</div><h1 style="color:#2d7d46">Avis validé !</h1>
<p>La fiche <b>{reference}</b> est validée.</p>
<p style="color:#888;font-size:14px">Le PDF va être généré automatiquement.</p>
</div></body></html>""", 200
    except Exception as e:
        traceback.print_exc()
        return f"<h2>Erreur : {str(e)}</h2>", 500


@app.route("/generate-pdf", methods=["POST"])
def generate_pdf():
    token = request.headers.get("X-Secret-Token")
    if token != SECRET_TOKEN:
        return jsonify({"error": "Non autorisé"}), 401

    payload = request.get_json(force=True)
    if not payload:
        return jsonify({"error": "Body JSON manquant"}), 400

    row_id = payload.get("row_id")
    if not row_id:
        return jsonify({"error": "row_id manquant"}), 400

    try:
        data = fetch_seatable_row(row_id)
        data["Date"] = datetime.now().strftime("%d/%m/%Y")

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            tmp_path = f.name

        build_pdf(data, tmp_path)

        ref = data.get("Reference", "sans-ref")
        filename = f"AvisValeur_{ref}.pdf"

        return send_file(
            tmp_path,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

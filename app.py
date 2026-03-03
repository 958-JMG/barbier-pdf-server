"""
Serveur Flask — Générateur PDF Avis de Valeur PRO
Barbier Immobilier / Système Barbier 2.0
"""
from flask import Flask, request, send_file, jsonify
import tempfile, os, traceback
from datetime import datetime
from generate_avis_valeur import build_pdf, fetch_seatable_row

app = Flask(__name__)

# Token secret partagé avec n8n (à définir dans les variables d'env Railway)
SECRET_TOKEN = os.environ.get("SECRET_TOKEN", "barbier2024secret")


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "Barbier PDF Generator", "version": "1.0"})


@app.route("/generate-pdf", methods=["POST"])
def generate_pdf():
    # Vérification token
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
        # Récupération des données SeaTable
        data = fetch_seatable_row(row_id)
        data["Date"] = datetime.now().strftime("%d/%m/%Y")

        # Génération du PDF dans un fichier temporaire
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

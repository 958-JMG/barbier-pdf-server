# barbier-pdf-server

Générateur PDF "Avis de Valeur PRO" — Barbier Immobilier  
Système Barbier 2.0 — 9•58 Consulting

## Déploiement Railway

1. Pusher ce repo sur GitHub
2. Aller sur [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Sélectionner ce repo
4. Dans **Variables** (onglet Settings), ajouter :
   - `SECRET_TOKEN` = une valeur secrète de ton choix (ex: `BarbierPDF2024!`)
5. Railway déploie automatiquement → tu récupères l'URL publique

## Endpoint

```
POST https://ton-app.railway.app/generate-pdf
Headers:
  Content-Type: application/json
  X-Secret-Token: <SECRET_TOKEN>
Body:
  { "row_id": "<_id de la ligne SeaTable>" }
Response:
  Fichier PDF binaire
```

## Test santé

```
GET https://ton-app.railway.app/
→ { "status": "ok" }
```
 
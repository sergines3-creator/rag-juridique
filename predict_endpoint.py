import os
import json
from flask import Blueprint, request, jsonify
from supabase import create_client
from anthropic import Anthropic
from sentence_transformers import SentenceTransformer

from prediction.risk_analyzer import analyze_risk
from prediction.success_estimator import estimate_success

supabase  = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
anthropic = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

_embedder = None
def get_embedder():
    global _embedder
    if _embedder is None:
        _embedder = SentenceTransformer("paraphrase-multilingual-mpnet-base-v2")
    return _embedder

predict_bp = Blueprint("predict", __name__)


@predict_bp.route("/api/predict", methods=["POST"])
def predict():
    """
    Body JSON attendu :
    {
        "query":   "Litige commercial entre deux sociétés OHADA",
        "domaine": "commercial",
        "top_k":   8
    }
    """
    data = request.get_json(silent=True)
    if not data or not data.get("query"):
        return jsonify({"error": "Champ 'query' requis."}), 400

    query   = data["query"].strip()
    domaine = data.get("domaine", "autre").strip().lower()
    top_k   = int(data.get("top_k", 8))

    chunks          = _rag_search(query, top_k)
    risk            = analyze_risk(chunks, domaine=domaine)
    success         = estimate_success(chunks)
    recommendations = _claude_synthesis(query, domaine, risk, success, chunks)

    return jsonify({
        "query":           query,
        "domaine":         domaine,
        "risk":            risk,
        "success":         success,
        "recommendations": recommendations,
        "chunks_used":     len(chunks)
    })


def _rag_search(query: str, top_k: int) -> list:
    embedder  = get_embedder()
    embedding = embedder.encode(query).tolist()

    try:
        result = supabase.rpc("match_chunks", {
            "query_embedding": embedding,
            "match_threshold": 0.65,
            "match_count":     top_k
        }).execute()

        chunks = []
        for row in (result.data or []):
            chunks.append({
                "content":    row.get("contenu", ""),
                "similarity": row.get("similarity", 0.5),
                "metadata": {
                    "source":  row.get("source", "inconnue"),
                    "domaine": row.get("domaine", ""),
                    "date":    row.get("date_dec", ""),
                    "issue":   row.get("issue", ""),
                }
            })
        return chunks

    except Exception as e:
        print(f"[RAG] Erreur Supabase : {e}")
        return []


def _claude_synthesis(query, domaine, risk, success, chunks) -> dict:
    context_snippets = "\n".join(
        f"- [{c['metadata'].get('source','?')}] {c['content'][:200]}..."
        for c in chunks[:5]
    )

    prompt = f"""Tu es Cabinet Boubou, assistant juridique expert en droit OHADA, CEMAC et camerounais.

## Dossier soumis
Requête : {query}
Domaine juridique : {domaine}

## Résultats d'analyse prédictive
Score de risque : {risk['score']}/100 (niveau : {risk['level']})
Probabilité de succès estimée : {round(success['probability'] * 100)}% (confiance : {success['confidence']})
Précédents analysés : {success['total_count']} dont {success['favorable_count']} favorables

Facteurs de risque identifiés :
{chr(10).join(f"• {f}" for f in risk['risk_factors']) or "Aucun"}

Points rassurants :
{chr(10).join(f"• {f}" for f in risk['safe_factors']) or "Aucun"}

## Extraits de jurisprudence similaire
{context_snippets or "Aucun précédent disponible."}

## Instructions
Produis des recommandations juridiques structurées en JSON strict (sans markdown, sans backticks).
Le JSON doit avoir exactement cette structure :
{{
  "actions_prioritaires": ["action 1", "action 2", "action 3"],
  "points_vigilance": ["point 1", "point 2"],
  "prochaines_etapes": ["étape 1", "étape 2", "étape 3"],
  "alternatives": ["alternative 1"],
  "synthese": "Un paragraphe de synthèse pour l'avocat (3-4 phrases)."
}}

Sois précis, actionnable, ancré dans le droit OHADA/camerounais."""

    try:
        response = anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)

    except json.JSONDecodeError:
        return {
            "actions_prioritaires": [],
            "points_vigilance":     [],
            "prochaines_etapes":    [],
            "alternatives":         [],
            "synthese":             raw if 'raw' in dir() else "Erreur de synthèse."
        }
    except Exception as e:
        print(f"[Claude] Erreur : {e}")
        return {
            "actions_prioritaires": [],
            "points_vigilance":     [str(e)],
            "prochaines_etapes":    [],
            "alternatives":         [],
            "synthese":             "Service temporairement indisponible."
        }
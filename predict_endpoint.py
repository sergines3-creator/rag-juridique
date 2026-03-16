import os
import json
from flask import Blueprint, request, jsonify
from supabase import create_client
from anthropic import Anthropic

from prediction.risk_analyzer import analyze_risk
from prediction.success_estimator import estimate_success

supabase  = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
anthropic = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

predict_bp = Blueprint("predict", __name__)

MOTS_VIDES = [
    "quel", "quels", "quelle", "quelles", "dans", "pour", "avec", "sont",
    "comment", "selon", "quand", "cette", "leurs", "leur", "conditions",
    "les", "des", "une", "est", "par", "sur", "qui", "que", "quoi",
    "entre", "plus", "tout", "aussi", "mais", "donc", "alors", "ainsi"
]


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
    """
    Recherche hybride : textuelle ilike sur la table chunks.
    Compatible Railway sans sentence-transformers.
    """
    tous_chunks = []
    ids_vus = set()

    def ajouter(data):
        for row in data:
            cle = str(row.get("document_id", "")) + "-" + str(row.get("page_numero", ""))
            if cle not in ids_vus:
                ids_vus.add(cle)
                meta = row.get("metadata") or {}
                tous_chunks.append({
                    "content":    row.get("contenu", ""),
                    "similarity": 0.75,
                    "metadata": {
                        "source":  meta.get("source", "inconnue"),
                        "domaine": meta.get("domaine", ""),
                        "date":    meta.get("date", ""),
                        "issue":   meta.get("issue", ""),
                    }
                })

    try:
        # Niveau 1 — recherche phrase complète
        result = supabase.table("chunks").select(
            "contenu, page_numero, document_id, metadata"
        ).ilike("contenu", f"%{query.lower()}%").limit(top_k).execute()
        ajouter(result.data)

        # Niveau 2 — recherche par mots longs si pas de résultats
        if not tous_chunks:
            mots = [m for m in query.lower().split() if len(m) > 4 and m not in MOTS_VIDES]
            for mot in mots[:5]:
                result = supabase.table("chunks").select(
                    "contenu, page_numero, document_id, metadata"
                ).ilike("contenu", f"%{mot}%").limit(5).execute()
                ajouter(result.data)

    except Exception as e:
        print(f"[RAG] Erreur recherche : {e}")

    return tous_chunks[:top_k]


def _claude_synthesis(query, domaine, risk, success, chunks) -> dict:
    context_snippets = "\n".join(
        f"- [{c['metadata'].get('source', '?')}] {c['content'][:200]}..."
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
            "synthese":             raw if 'raw' in locals() else "Erreur de synthèse."
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
"""
success_estimator.py
Estime la probabilité de succès d'une procédure juridique
en analysant les chunks RAG retournés par Supabase pgvector.
"""

import math
from typing import List, Dict, Any


def estimate_success(chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Analyse les chunks RAG et retourne une estimation de succès.

    Args:
        chunks: Liste de chunks retournés par Supabase pgvector.
                Chaque chunk doit avoir :
                  - content     : str
                  - similarity  : float (0.0 - 1.0)
                  - metadata    : dict avec clés :
                      issue     : "favorable" | "defavorable" | "partiel" | None
                      source    : "OHADA" | "CEMAC" | "droit_cm" | ...
                      domaine   : "commercial" | "penal" | "social" | ...
                      date      : "YYYY-MM-DD" | None

    Returns:
        {
            "probability":     float,   # 0.0 - 1.0
            "confidence":      str,     # "haute" | "moyenne" | "faible"
            "favorable_count": int,
            "total_count":     int,
            "weighted_score":  float,
            "explanation":     str
        }
    """
    if not chunks:
        return _empty_result("Aucun précédent juridique trouvé pour ce type de dossier.")

    scored = _score_chunks(chunks)

    total_weight   = sum(s["weight"] for s in scored)
    favorable_w    = sum(s["weight"] for s in scored if s["issue"] == "favorable")
    partiel_w      = sum(s["weight"] for s in scored if s["issue"] == "partiel")
    defavorable_w  = sum(s["weight"] for s in scored if s["issue"] == "defavorable")

    favorable_count   = sum(1 for s in scored if s["issue"] == "favorable")
    defavorable_count = sum(1 for s in scored if s["issue"] == "defavorable")
    unknown_count     = sum(1 for s in scored if s["issue"] == "unknown")
    total_count       = len(scored)

    if total_weight == 0:
        return _empty_result("Issues non renseignées — impossible d'estimer la probabilité.")

    # Score pondéré : favorable = 1.0, partiel = 0.5, défavorable = 0.0
    weighted_score = (favorable_w + 0.5 * partiel_w) / total_weight

    # Pénalité si peu de précédents ou beaucoup d'issues inconnues
    confidence, penalty = _compute_confidence(total_count, unknown_count)
    adjusted_score = max(0.0, min(1.0, weighted_score - penalty))

    explanation = _build_explanation(
        adjusted_score, favorable_count, defavorable_count,
        total_count, confidence, scored
    )

    return {
        "probability":     round(adjusted_score, 3),
        "confidence":      confidence,
        "favorable_count": favorable_count,
        "defavorable_count": defavorable_count,
        "total_count":     total_count,
        "weighted_score":  round(weighted_score, 3),
        "explanation":     explanation
    }


# ── Helpers ────────────────────────────────────────────────────────────────────

def _score_chunks(chunks: List[Dict]) -> List[Dict]:
    """Normalise et pondère chaque chunk par sa similarité vectorielle."""
    scored = []
    for c in chunks:
        meta      = c.get("metadata") or {}
        raw_issue = (meta.get("issue") or "").strip().lower()
        similarity = float(c.get("similarity", 0.5))

        # Normalise l'issue
        if raw_issue in ("favorable", "positif", "gagne"):
            issue = "favorable"
        elif raw_issue in ("defavorable", "negatif", "perdu", "défavorable"):
            issue = "defavorable"
        elif raw_issue in ("partiel", "mixte", "partielle"):
            issue = "partiel"
        else:
            issue = "unknown"

        # Poids = similarité vectorielle (plus proche = plus de poids)
        scored.append({
            "issue":      issue,
            "weight":     similarity,
            "source":     meta.get("source", "inconnue"),
            "domaine":    meta.get("domaine", ""),
            "date":       meta.get("date", ""),
            "similarity": similarity
        })
    return scored


def _compute_confidence(total: int, unknown: int) -> tuple:
    """Retourne (niveau_confiance, pénalité_score)."""
    if total >= 8 and unknown <= 2:
        return "haute", 0.0
    elif total >= 4 and unknown <= total * 0.5:
        return "moyenne", 0.05
    else:
        return "faible", 0.12


def _build_explanation(score, fav, defav, total, confidence, scored) -> str:
    pct = round(score * 100)
    sources = list({s["source"] for s in scored if s["source"] != "inconnue"})
    src_str = ", ".join(sources) if sources else "sources variées"

    level = "élevée" if pct >= 65 else "modérée" if pct >= 40 else "faible"

    return (
        f"Sur {total} précédent(s) similaire(s) ({src_str}), "
        f"{fav} issue(s) favorable(s) et {defav} défavorable(s) ont été identifiées. "
        f"Probabilité de succès estimée : {pct}% ({level}). "
        f"Niveau de confiance : {confidence}."
    )


def _empty_result(explanation: str) -> Dict:
    return {
        "probability":       0.5,
        "confidence":        "faible",
        "favorable_count":   0,
        "defavorable_count": 0,
        "total_count":       0,
        "weighted_score":    0.5,
        "explanation":       explanation
    }
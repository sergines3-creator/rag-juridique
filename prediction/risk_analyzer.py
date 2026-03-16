"""
risk_analyzer.py
Calcule un score de risque juridique (0-100) à partir des chunks RAG.
Plus le score est élevé, plus le dossier est risqué.
"""

from typing import List, Dict, Any


# ── Pondérations par source (fiabilité juridique) ──────────────────────────────
SOURCE_WEIGHTS = {
    "ohada":    1.0,   # Référence supranationale — poids maximal
    "cemac":    0.95,
    "droit_cm": 0.85,
    "autre":    0.6,
    "inconnue": 0.5,
}

# ── Facteurs de risque par domaine ─────────────────────────────────────────────
DOMAIN_RISK_FACTOR = {
    "penal":      1.3,   # Risque intrinsèquement plus élevé
    "commercial": 1.0,
    "social":     0.95,
    "civil":      0.9,
    "fiscal":     1.1,
    "foncier":    1.15,
    "autre":      1.0,
}


def analyze_risk(chunks: List[Dict[str, Any]], domaine: str = "autre") -> Dict[str, Any]:
    """
    Calcule un score de risque pour un dossier.

    Args:
        chunks  : Chunks RAG (même format que success_estimator)
        domaine : Domaine juridique principal du dossier

    Returns:
        {
            "score":          int,    # 0 (sans risque) → 100 (très risqué)
            "level":          str,    # "faible" | "modéré" | "élevé" | "critique"
            "risk_factors":   list,   # Points de vigilance identifiés
            "safe_factors":   list,   # Points rassurants
            "recommendation": str
        }
    """
    if not chunks:
        return _default_risk(domaine)

    scored = _score_chunks(chunks)
    domain_factor = DOMAIN_RISK_FACTOR.get(domaine.lower(), 1.0)

    # ── Calcul du score brut ────────────────────────────────────────────────────
    total_weight    = sum(s["weight"] for s in scored)
    defavorable_w   = sum(s["weight"] for s in scored if s["issue"] == "defavorable")
    unknown_w       = sum(s["weight"] for s in scored if s["issue"] == "unknown")

    if total_weight == 0:
        base_score = 50.0
    else:
        # Issues défavorables → risque direct
        # Issues inconnues    → risque partiel (incertitude)
        base_score = (
            (defavorable_w / total_weight) * 80
            + (unknown_w   / total_weight) * 30
        )

    # Applique le facteur domaine
    raw_score = min(100.0, base_score * domain_factor)

    # ── Bonus/malus contextuels ─────────────────────────────────────────────────
    adjustments, risk_factors, safe_factors = _contextual_analysis(scored, domaine)
    final_score = max(0, min(100, int(raw_score + adjustments)))

    level = _score_to_level(final_score)
    recommendation = _build_recommendation(final_score, level, risk_factors, safe_factors)

    return {
        "score":          final_score,
        "level":          level,
        "risk_factors":   risk_factors,
        "safe_factors":   safe_factors,
        "recommendation": recommendation
    }


# ── Helpers ────────────────────────────────────────────────────────────────────

def _score_chunks(chunks: List[Dict]) -> List[Dict]:
    scored = []
    for c in chunks:
        meta       = c.get("metadata") or {}
        raw_issue  = (meta.get("issue") or "").strip().lower()
        raw_source = (meta.get("source") or "inconnue").strip().lower()
        similarity = float(c.get("similarity", 0.5))

        if raw_issue in ("favorable", "positif", "gagne"):
            issue = "favorable"
        elif raw_issue in ("defavorable", "negatif", "perdu", "défavorable"):
            issue = "defavorable"
        elif raw_issue in ("partiel", "mixte"):
            issue = "partiel"
        else:
            issue = "unknown"

        source_key = raw_source if raw_source in SOURCE_WEIGHTS else "inconnue"
        source_w   = SOURCE_WEIGHTS[source_key]

        scored.append({
            "issue":    issue,
            "weight":   similarity * source_w,
            "source":   raw_source,
            "domaine":  meta.get("domaine", ""),
            "date":     meta.get("date", ""),
        })
    return scored


def _contextual_analysis(scored, domaine):
    """Identifie des facteurs de risque et de sécurité contextuels."""
    adjustments  = 0
    risk_factors = []
    safe_factors = []

    defav = [s for s in scored if s["issue"] == "defavorable"]
    fav   = [s for s in scored if s["issue"] == "favorable"]

    # Majorité de précédents défavorables
    if len(defav) > len(fav) * 1.5:
        adjustments += 8
        risk_factors.append("Majorité de précédents défavorables dans la jurisprudence similaire.")

    # Peu de précédents disponibles
    if len(scored) < 3:
        adjustments += 5
        risk_factors.append("Peu de précédents disponibles — incertitude élevée.")

    # Source OHADA présente → légèrement rassurant (droit codifié)
    ohada_chunks = [s for s in scored if "ohada" in s["source"]]
    if ohada_chunks:
        adjustments -= 3
        safe_factors.append("Jurisprudence OHADA disponible — cadre juridique codifié.")

    # Domaine pénal → risque supplémentaire signalé
    if domaine.lower() == "penal":
        risk_factors.append("Matière pénale : enjeux sur la liberté individuelle à considérer.")

    # Bonne proportion de favorables
    if len(fav) >= 3 and len(fav) > len(defav):
        adjustments -= 5
        safe_factors.append(f"{len(fav)} précédents favorables similaires identifiés.")

    return adjustments, risk_factors, safe_factors


def _score_to_level(score: int) -> str:
    if score < 25:  return "faible"
    if score < 50:  return "modéré"
    if score < 75:  return "élevé"
    return "critique"


def _build_recommendation(score, level, risk_factors, safe_factors) -> str:
    if level == "faible":
        base = "Le dossier présente un profil de risque faible. Procédure envisageable avec une préparation standard."
    elif level == "modéré":
        base = "Risque modéré. Une analyse approfondie des points de vigilance est recommandée avant d'engager la procédure."
    elif level == "élevé":
        base = "Risque élevé. Envisager une stratégie défensive solide ou une voie alternative (médiation, transaction)."
    else:
        base = "Risque critique. Déconseillé d'engager la procédure sans renforcement majeur du dossier."

    if risk_factors:
        base += f" Points de vigilance : {risk_factors[0]}"

    return base


def _default_risk(domaine: str) -> Dict:
    factor = DOMAIN_RISK_FACTOR.get(domaine.lower(), 1.0)
    score  = int(50 * factor)
    return {
        "score":          min(100, score),
        "level":          _score_to_level(score),
        "risk_factors":   ["Aucun précédent trouvé — évaluation par défaut."],
        "safe_factors":   [],
        "recommendation": "Données insuffisantes. Enrichir le dossier avant toute décision."
    }
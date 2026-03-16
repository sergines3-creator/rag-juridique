"""
audit_logger.py
Système de logs d'audit pour Cabinet Boubou
Trace chaque action sensible : accès, upload, génération, suppression
"""

import os
import json
from datetime import datetime
from functools import wraps
from flask import request, g
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

supabase = create_client(
    os.environ.get("SUPABASE_URL"),
    os.environ.get("SUPABASE_KEY")
)


# ── Actions auditées ───────────────────────────────────────
ACTION_LOGIN          = "login"
ACTION_LOGIN_ECHEC    = "login_echec"
ACTION_QUESTION       = "question_posee"
ACTION_UPLOAD         = "document_uploade"
ACTION_SUPPRESSION    = "document_supprime"
ACTION_GENERATION     = "document_genere"
ACTION_EXPORT_PDF     = "export_pdf"
ACTION_VEILLE         = "veille_synchronisee"
ACTION_PREDICT        = "prediction_lancee"


def log_audit(action: str, details: dict = None, succes: bool = True):
    """
    Enregistre une action dans la table audit_logs.

    Args:
        action  : type d'action (utilise les constantes ci-dessus)
        details : informations complémentaires (dict)
        succes  : True si l'action a réussi, False sinon
    """
    try:
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)
        user_agent = request.headers.get("User-Agent", "")[:200]

        entree = {
            "action":     action,
            "succes":     succes,
            "ip":         ip,
            "user_agent": user_agent,
            "details":    json.dumps(details or {}, ensure_ascii=False),
            "timestamp":  datetime.utcnow().isoformat()
        }

        supabase.table("audit_logs").insert(entree).execute()

    except Exception as e:
        print(f"[AUDIT] Erreur enregistrement log : {e}")


def auditer(action: str, extraire_details=None):
    """
    Décorateur pour auditer automatiquement une route Flask.

    Usage :
        @auditer(ACTION_UPLOAD, lambda: {"fichier": request.files.get("fichier", {}).filename})
        def upload_document():
            ...
    """
    def decorateur(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            details = {}
            if extraire_details:
                try:
                    details = extraire_details()
                except Exception:
                    pass

            try:
                resultat = f(*args, **kwargs)
                # Détermine le succès selon le code HTTP
                code = resultat[1] if isinstance(resultat, tuple) else 200
                succes = code < 400
                log_audit(action, details, succes=succes)
                return resultat
            except Exception as e:
                log_audit(action, {**details, "erreur": str(e)[:200]}, succes=False)
                raise

        return wrapper
    return decorateur


def get_logs(limite: int = 50, action_filtre: str = None) -> list:
    """
    Récupère les derniers logs d'audit.

    Args:
        limite        : nombre de logs à récupérer
        action_filtre : filtrer par type d'action
    """
    try:
        query = supabase.table("audit_logs").select("*").order(
            "timestamp", desc=True
        ).limit(limite)

        if action_filtre:
            query = query.eq("action", action_filtre)

        result = query.execute()
        return result.data or []

    except Exception as e:
        print(f"[AUDIT] Erreur récupération logs : {e}")
        return []
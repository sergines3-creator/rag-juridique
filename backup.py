"""
backup.py
Système de backup automatique chiffré pour Cabinet Boubou
- Exporte documents, chunks, jurisprudence_predict en JSON
- Chiffre avec Fernet
- Stocke dans Supabase Storage (bucket: backups)
- Envoie par email à Maître Boubou
"""

import os
import json
import gzip
import smtplib
import schedule
import time
import threading
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from supabase import create_client
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY")
BACKUP_EMAIL = os.environ.get("BACKUP_EMAIL")
SMTP_EMAIL = os.environ.get("SMTP_EMAIL")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def exporter_donnees() -> dict:
    """Exporte toutes les tables importantes depuis Supabase."""
    print("[BACKUP] Export des données...")
    data = {
        "version": "1.0",
        "date": datetime.utcnow().isoformat(),
        "tables": {}
    }

    tables = ["documents", "chunks", "jurisprudence_predict", "audit_logs"]

    for table in tables:
        try:
            result = supabase.table(table).select("*").limit(10000).execute()
            data["tables"][table] = result.data or []
            print(f"[BACKUP] {table} : {len(data['tables'][table])} lignes")
        except Exception as e:
            print(f"[BACKUP] Erreur export {table} : {e}")
            data["tables"][table] = []

    return data


def chiffrer_backup(data: dict) -> bytes:
    """Sérialise, compresse et chiffre les données."""
    json_bytes = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
    compressed = gzip.compress(json_bytes)
    f = Fernet(ENCRYPTION_KEY.encode())
    return f.encrypt(compressed)


def stocker_supabase(backup_chiffre: bytes, nom_fichier: str) -> bool:
    """Stocke le backup chiffré dans Supabase Storage."""
    try:
        supabase.storage.from_("backups").upload(
            path=nom_fichier,
            file=backup_chiffre,
            file_options={"content-type": "application/octet-stream"}
        )
        print(f"[BACKUP] Stocké dans Supabase Storage : {nom_fichier}")
        return True
    except Exception as e:
        print(f"[BACKUP] Erreur stockage Supabase : {e}")
        return False


def envoyer_email(backup_chiffre: bytes, nom_fichier: str) -> bool:
    """Envoie le backup par email via Outlook SMTP."""
    if not all([BACKUP_EMAIL, SMTP_EMAIL, SMTP_PASSWORD]):
        print("[BACKUP] Variables email non configurées — email ignoré")
        return False

    try:
        msg = MIMEMultipart()
        msg["From"] = SMTP_EMAIL
        msg["To"] = BACKUP_EMAIL
        msg["Subject"] = f"Cabinet Boubou — Backup hebdomadaire {datetime.now().strftime('%d/%m/%Y')}"

        corps = f"""Bonjour Maître Boubou,

Veuillez trouver ci-joint le backup chiffré hebdomadaire de votre système Cabinet Boubou.

Date : {datetime.now().strftime('%d/%m/%Y à %H:%M')}
Fichier : {nom_fichier}

Ce fichier est chiffré avec votre clé de sécurité — conservez-le précieusement.

byNexFlow — Cabinet Boubou Intelligence Juridique
"""
        msg.attach(MIMEText(corps, "plain"))

        part = MIMEBase("application", "octet-stream")
        part.set_payload(backup_chiffre)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f"attachment; filename={nom_fichier}")
        msg.attach(part)

        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.send_message(msg)

        print(f"[BACKUP] Email envoyé à {BACKUP_EMAIL}")
        return True

    except Exception as e:
        print(f"[BACKUP] Erreur envoi email : {e}")
        return False


def nettoyer_anciens_backups(conserver=8):
    """Supprime les backups de plus de 8 semaines dans Supabase Storage."""
    try:
        fichiers = supabase.storage.from_("backups").list()
        if len(fichiers) > conserver:
            a_supprimer = sorted(fichiers, key=lambda x: x["created_at"])[:-conserver]
            for f in a_supprimer:
                supabase.storage.from_("backups").remove([f["name"]])
                print(f"[BACKUP] Supprimé ancien backup : {f['name']}")
    except Exception as e:
        print(f"[BACKUP] Erreur nettoyage : {e}")


def lancer_backup() -> dict:
    """Lance le backup complet."""
    print("\n========================================")
    print("BACKUP CABINET BOUBOU")
    print(f"Date : {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print("========================================")

    nom_fichier = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.enc"

    try:
        data = exporter_donnees()
        backup_chiffre = chiffrer_backup(data)

        total_lignes = sum(len(v) for v in data["tables"].values())
        taille_ko = round(len(backup_chiffre) / 1024, 1)

        stocke = stocker_supabase(backup_chiffre, nom_fichier)
        email_envoye = envoyer_email(backup_chiffre, nom_fichier)
        nettoyer_anciens_backups()

        resultat = {
            "succes": True,
            "fichier": nom_fichier,
            "lignes_exportees": total_lignes,
            "taille_ko": taille_ko,
            "stocke_supabase": stocke,
            "email_envoye": email_envoye,
            "date": datetime.now().isoformat()
        }

        print(f"[BACKUP] Terminé — {total_lignes} lignes, {taille_ko} Ko")
        return resultat

    except Exception as e:
        print(f"[BACKUP] Erreur critique : {e}")
        return {"succes": False, "erreur": str(e)}


def demarrer_scheduler():
    """Démarre le scheduler hebdomadaire en arrière-plan."""
    schedule.every().monday.at("02:00").do(lancer_backup)
    print("[BACKUP] Scheduler démarré — backup tous les lundis à 02h00 UTC")

    def run():
        while True:
            schedule.run_pending()
            time.sleep(3600)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()


if __name__ == "__main__":
    lancer_backup()
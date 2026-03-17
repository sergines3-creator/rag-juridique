"""
encryption.py
Chiffrement/déchiffrement des documents sensibles avec Fernet (AES-128)
La clé ne vit que dans les variables d'environnement — jamais en base.
"""

import os
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY")

def get_fernet():
    if not ENCRYPTION_KEY:
        raise ValueError("ENCRYPTION_KEY manquante dans les variables d'environnement")
    return Fernet(ENCRYPTION_KEY.encode())

def chiffrer(texte: str) -> str:
    """
    Chiffre un texte en clair.
    Retourne une chaîne base64 préfixée par ENC: pour identifier les chunks chiffrés.
    """
    try:
        f = get_fernet()
        chiffre = f.encrypt(texte.encode("utf-8"))
        return "ENC:" + chiffre.decode("utf-8")
    except Exception as e:
        print(f"[ENCRYPTION] Erreur chiffrement : {e}")
        return texte

def dechiffrer(texte: str) -> str:
    """
    Déchiffre un texte chiffré.
    Si le texte n'est pas préfixé par ENC:, il est retourné tel quel.
    """
    try:
        if not texte.startswith("ENC:"):
            return texte
        f = get_fernet()
        donnees = texte[4:].encode("utf-8")
        return f.decrypt(donnees).decode("utf-8")
    except Exception as e:
        print(f"[ENCRYPTION] Erreur déchiffrement : {e}")
        return "[Document chiffré — clé invalide]"

def est_chiffre(texte: str) -> bool:
    """Vérifie si un chunk est chiffré."""
    return texte.startswith("ENC:")
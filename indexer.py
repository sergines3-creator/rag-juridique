import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import os
import fitz
from supabase import create_client

# ─── CONNEXION SUPABASE ───────────────────────────────────
supabase = create_client(
    "https://ylmlmoyhrngoxbhojlmt.supabase.co",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlsbWxtb3locm5nb3hiaG9qbG10Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzI1MjMxMzAsImV4cCI6MjA4ODA5OTEzMH0.64RvsKIFKX6Re0NprJThZEKoKh9yZ11YwKXPQ9s7RD0"
)

DOSSIER_DOCUMENTS = "documents"
CABINET = "Cabinet Maitre Boubou"

# ─── FONCTIONS ────────────────────────────────────────────

def document_existe(nom):
    try:
        result = supabase.table("documents").select("id").eq("nom", nom).execute()
        return len(result.data) > 0
    except Exception as e:
        print("Erreur verification:", str(e))
        return False

def extraire_texte_pdf(chemin_pdf):
    try:
        doc = fitz.open(chemin_pdf)
        pages = []
        for num, page in enumerate(doc):
            texte = page.get_text()
            if texte.strip():
                pages.append({"page": num + 1, "texte": texte})
        doc.close()
        return pages
    except Exception as e:
        print("Erreur extraction PDF:", str(e))
        return []

def decouper_en_chunks(pages, taille=500):
    chunks = []
    for page in pages:
        mots = page["texte"].split()
        for i in range(0, len(mots), taille):
            morceau = " ".join(mots[i:i + taille])
            if morceau.strip():
                chunks.append({"contenu": morceau, "page": page["page"]})
    return chunks

def indexer_pdf(chemin_pdf, nom):
    print("\n----------------------------------------")
    print("Fichier :", nom)

    if document_existe(nom):
        print("Deja indexe, on passe.")
        return

    # Extraction du texte
    pages = extraire_texte_pdf(chemin_pdf)
    if not pages:
        print("Aucun texte extrait, fichier ignore.")
        return
    print("Pages extraites :", len(pages))

    # Insertion du document
    try:
        doc_result = supabase.table("documents").insert({
            "nom": nom,
            "type": "loi",
            "cabinet": CABINET,
            "source": chemin_pdf
        }).execute()

        if not doc_result.data:
            print("Echec insertion document.")
            return

        document_id = doc_result.data[0]["id"]
        print("Document enregistre, id :", document_id)

    except Exception as e:
        print("Erreur insertion document:", str(e))
        return

    # Decoupage en chunks
    chunks = decouper_en_chunks(pages)
    print("Chunks a inserer :", len(chunks))

    # Insertion par lots de 50
    inseres = 0
    for i in range(0, len(chunks), 50):
        lot = chunks[i:i + 50]
        try:
            donnees = [{
                "document_id": document_id,
                "contenu": c["contenu"],
                "page_numero": c["page"]
            } for c in lot]
            supabase.table("chunks").insert(donnees).execute()
            inseres += len(lot)
            print("Progression :", inseres, "/", len(chunks), "chunks")
        except Exception as e:
            print("Erreur insertion chunks:", str(e))
            continue

    print("Indexation terminee :", nom)

# ─── LANCEMENT ────────────────────────────────────────────

if __name__ == "__main__":
    print("========================================")
    print("INDEXATION DES DOCUMENTS JURIDIQUES")
    print("Dossier :", DOSSIER_DOCUMENTS)
    print("========================================")

    if not os.path.exists(DOSSIER_DOCUMENTS):
        print("Dossier introuvable :", DOSSIER_DOCUMENTS)
        sys.exit(1)

    fichiers = [f for f in os.listdir(DOSSIER_DOCUMENTS) if f.endswith(".pdf")]

    if not fichiers:
        print("Aucun PDF trouve dans le dossier.")
        sys.exit(1)

    print("PDFs trouves :", len(fichiers))

    for fichier in fichiers:
        chemin = os.path.join(DOSSIER_DOCUMENTS, fichier)
        indexer_pdf(chemin, fichier)

    print("\n========================================")
    print("INDEXATION COMPLETE !")
    print("========================================")
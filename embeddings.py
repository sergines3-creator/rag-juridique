import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from supabase import create_client
from sentence_transformers import SentenceTransformer
import time

# ─── CONFIG ──────────────────────────────────────────────
SUPABASE_URL = "https://ylmlmoyhrngoxbhojlmt.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlsbWxtb3locm5nb3hiaG9qbG10Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzI1MjMxMzAsImV4cCI6MjA4ODA5OTEzMH0.64RvsKIFKX6Re0NprJThZEKoKh9yZ11YwKXPQ9s7RD0"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Modele multilingue qui comprend le francais juridique
print("Chargement du modele d embeddings...")
model = SentenceTransformer("paraphrase-multilingual-mpnet-base-v2")
print("Modele charge !")

def vectoriser_chunks():
    print("\n========================================")
    print("VECTORISATION DES CHUNKS")
    print("========================================")

    # Recuperer tous les chunks sans embedding
    try:
        result = supabase.table("chunks").select(
            "id, contenu"
        ).execute()

        chunks = result.data
        total = len(chunks)

        if total == 0:
            print("Tous les chunks sont deja vectorises !")
            return

        print(f"Chunks a vectoriser : {total}")

    except Exception as e:
        print("Erreur recuperation chunks:", str(e))
        return

    # Vectorisation par lots de 100
    traites = 0
    for i in range(0, total, 100):
        lot = chunks[i:i + 100]
        textes = [c["contenu"] for c in lot]

        try:
            # Generation des embeddings
            embeddings = model.encode(textes, show_progress_bar=False)

            # Mise a jour dans Supabase
            for j, chunk in enumerate(lot):
                embedding_liste = embeddings[j].tolist()
                supabase.table("chunks").update({
                    "embedding": embedding_liste
                }).eq("id", chunk["id"]).execute()

            traites += len(lot)
            print(f"Progression : {traites} / {total} chunks vectorises")

            # Pause pour eviter de surcharger Supabase
            time.sleep(0.5)

        except Exception as e:
            print(f"Erreur lot {i} :", str(e))
            continue

    print("\n========================================")
    print("VECTORISATION COMPLETE !")
    print(f"Total vectorise : {traites} chunks")
    print("========================================")

if __name__ == "__main__":
    vectoriser_chunks()
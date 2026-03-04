import os
from anthropic import Anthropic
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

MOTS_VIDES = [
    "quel", "quels", "quelle", "quelles", "dans", "pour", "avec", "sont",
    "comment", "selon", "quand", "cette", "leurs", "leur", "conditions",
    "les", "des", "une", "est", "par", "sur", "qui", "que", "quoi",
    "entre", "plus", "tout", "aussi", "mais", "donc", "alors", "ainsi"
]

def extraire_mots_cles(question):
    mots = question.lower().split()
    return [m for m in mots if len(m) > 3 and m not in MOTS_VIDES]

def rechercher_chunks(question, limite=10):
    tous_chunks = []
    ids_vus = set()

    def ajouter_chunks(data):
        for chunk in data:
            cle = f"{chunk['document_id']}-{chunk['page_numero']}"
            if cle not in ids_vus:
                ids_vus.add(cle)
                tous_chunks.append(chunk)

    # Niveau 1 — recherche question complète en minuscule
    result = supabase.table("chunks").select(
        "contenu, page_numero, document_id"
    ).ilike("contenu", f"%{question.lower()}%").limit(limite).execute()
    ajouter_chunks(result.data)

    # Niveau 2 — recherche question complète en majuscule
    if not tous_chunks:
        result = supabase.table("chunks").select(
            "contenu, page_numero, document_id"
        ).ilike("contenu", f"%{question.upper()}%").limit(limite).execute()
        ajouter_chunks(result.data)

    # Niveau 3 — recherche par mots longs
    if not tous_chunks:
        mots = [m for m in question.lower().split() if len(m) > 4]
        for mot in mots:
            result = supabase.table("chunks").select(
                "contenu, page_numero, document_id"
            ).ilike("contenu", f"%{mot}%").limit(5).execute()
            ajouter_chunks(result.data)

    # Niveau 4 — recherche par mots-clés juridiques filtrés
    if not tous_chunks:
        mots_cles = extraire_mots_cles(question)
        for mot in mots_cles[:5]:
            result = supabase.table("chunks").select(
                "contenu, page_numero, document_id"
            ).ilike("contenu", f"%{mot}%").limit(3).execute()
            ajouter_chunks(result.data)

    # Niveau 5 — recherche mot par mot sans filtre de longueur
    if not tous_chunks:
        for mot in question.lower().split():
            if len(mot) > 2:
                result = supabase.table("chunks").select(
                    "contenu, page_numero, document_id"
                ).ilike("contenu", f"%{mot}%").limit(3).execute()
                ajouter_chunks(result.data)

    return tous_chunks[:10]

def obtenir_nom_document(document_id):
    result = supabase.table("documents").select("nom").eq("id", document_id).execute()
    if result.data:
        return result.data[0]["nom"].replace(".pdf", "").replace("-", " ").replace("_", " ")
    return "Document inconnu"

def poser_question(question):
    print(f"\nQuestion : {question}")
    print("=" * 60)

    chunks = rechercher_chunks(question)

    if not chunks:
        print("Aucun passage pertinent trouve dans la base de donnees juridique.")
        print("Conseil : reformulez avec des termes juridiques plus specifiques.")
        return

    contexte = ""
    for i, chunk in enumerate(chunks, 1):
        nom_doc = obtenir_nom_document(chunk["document_id"])
        contexte += f"\n[Passage {i} — Source : {nom_doc}, Page {chunk['page_numero']}]\n{chunk['contenu']}\n"

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": f"""Tu es un assistant juridique expert en droit camerounais et droit OHADA, au service d'un cabinet d'avocats professionnel.

Ton rôle est d'analyser les passages juridiques fournis et de répondre de manière précise, structurée et professionnelle.

RÈGLES STRICTES :
- Base toi uniquement sur les passages fournis
- Cite toujours la source exacte et la page
- Si l'information est insuffisante, indique-le clairement
- Utilise un langage juridique professionnel
- Structure toujours ta réponse avec des sections claires

FORMAT DE RÉPONSE OBLIGATOIRE :

## Réponse juridique

### 1. Définition et contexte
[Explique le concept juridique concerné]

### 2. Base légale applicable
[Cite les textes, articles et sources avec pages]

### 3. Analyse juridique
[Développe l'analyse en détail]

### 4. Points essentiels à retenir
[Liste les points clés]

### 5. Recommandation
[Conseil pratique pour le cabinet]

PASSAGES JURIDIQUES DISPONIBLES :
{contexte}

QUESTION DU CABINET : {question}"""
        }]
    )

    print(response.content[0].text)
    print("\n" + "=" * 60)

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("   ASSISTANT JURIDIQUE byNexFlow")
    print("   Droit Camerounais & OHADA")
    print("=" * 60)
    print("Tapez 'quitter' pour arreter la session\n")

    while True:
        question = input("Question juridique : ").strip()
        if not question:
            continue
        if question.lower() == "quitter":
            print("\nFin de session. Au revoir.")
            break
        poser_question(question)
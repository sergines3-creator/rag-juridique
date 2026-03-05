import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from datetime import datetime
from anthropic import Anthropic
from supabase import create_client
import os

# ─── CONFIG ──────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY")

app = Flask(__name__)
CORS(app)

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
client = Anthropic(api_key=ANTHROPIC_KEY)

# Memoire conversationnelle par session
sessions = {}

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
            cle = str(chunk['document_id']) + "-" + str(chunk['page_numero'])
            if cle not in ids_vus:
                ids_vus.add(cle)
                tous_chunks.append(chunk)

    try:
        result = supabase.table("chunks").select(
            "contenu, page_numero, document_id"
        ).ilike("contenu", f"%{question.lower()}%").limit(limite).execute()
        ajouter_chunks(result.data)
    except Exception:
        pass

    if not tous_chunks:
        try:
            mots = [m for m in question.lower().split() if len(m) > 4]
            for mot in mots:
                result = supabase.table("chunks").select(
                    "contenu, page_numero, document_id"
                ).ilike("contenu", f"%{mot}%").limit(5).execute()
                ajouter_chunks(result.data)
        except Exception:
            pass

    if not tous_chunks:
        try:
            mots_cles = extraire_mots_cles(question)
            for mot in mots_cles[:5]:
                result = supabase.table("chunks").select(
                    "contenu, page_numero, document_id"
                ).ilike("contenu", f"%{mot}%").limit(3).execute()
                ajouter_chunks(result.data)
        except Exception:
            pass

    return tous_chunks[:10]

def obtenir_nom_document(document_id):
    try:
        result = supabase.table("documents").select("nom").eq("id", document_id).execute()
        if result.data:
            return result.data[0]["nom"].replace(".pdf", "").replace("-", " ").replace("_", " ")
    except Exception:
        pass
    return "Document inconnu"

# ─── ROUTES ──────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/question", methods=["POST"])
def question():
    try:
        data = request.json
        q = data.get("question", "").strip()
        session_id = data.get("session_id", "default")

        if not q:
            return jsonify({"erreur": "Question vide"}), 400

        if session_id not in sessions:
            sessions[session_id] = []

        historique_session = sessions[session_id]
        chunks = rechercher_chunks(q)

        contexte = ""
        sources = []
        if chunks:
            for i, chunk in enumerate(chunks, 1):
                nom_doc = obtenir_nom_document(chunk["document_id"])
                contexte += f"\n[Passage {i} - Source : {nom_doc}, Page {chunk['page_numero']}]\n{chunk['contenu']}\n"
                sources.append(f"{nom_doc} - Page {chunk['page_numero']}")

        messages = []
        for echange in historique_session[-6:]:
            messages.append({"role": "user", "content": echange["question"]})
            messages.append({"role": "assistant", "content": echange["reponse"]})

        prompt = (
            "Tu es un assistant juridique expert en droit camerounais et droit OHADA, "
            "au service du Cabinet de Maitre Boubou.\n\n"
            "REGLES :\n"
            "- Base toi sur les passages juridiques fournis\n"
            "- Cite toujours la source exacte et la page\n"
            "- Tiens compte du contexte des questions precedentes\n"
            "- Utilise un langage juridique professionnel\n\n"
            "FORMAT :\n"
            "1. Definition et contexte\n"
            "2. Base legale applicable\n"
            "3. Analyse juridique\n"
            "4. Points essentiels\n"
            "5. Recommandation\n"
        )

        if contexte:
            prompt += f"\nPASSAGES JURIDIQUES :\n{contexte}\n"

        prompt += f"\nQUESTION : {q}"
        messages.append({"role": "user", "content": prompt})

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=messages
        )

        reponse_texte = response.content[0].text
        sessions[session_id].append({"question": q, "reponse": reponse_texte})

        return jsonify({
            "reponse": reponse_texte,
            "sources": list(set(sources))
        })

    except Exception as e:
        print("ERREUR QUESTION:", str(e))
        return jsonify({"reponse": "Erreur : " + str(e), "sources": []}), 500

@app.route("/nouvelle-conversation", methods=["POST"])
def nouvelle_conversation():
    try:
        data = request.json
        session_id = data.get("session_id", "default")
        if session_id in sessions:
            del sessions[session_id]
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"erreur": str(e)}), 500

@app.route("/documents")
def documents():
    try:
        result = supabase.table("documents").select("nom, type, date_ajout").execute()
        return jsonify(result.data)
    except Exception as e:
        return jsonify({"erreur": str(e)}), 500

@app.route("/analyser", methods=["POST"])
def analyser():
    try:
        if "fichier" not in request.files:
            return jsonify({"erreur": "Aucun fichier recu"}), 400

        fichier = request.files["fichier"]
        if not fichier.filename.endswith(".pdf"):
            return jsonify({"erreur": "Format PDF uniquement"}), 400

        # Extraction du texte PDF
        import fitz
        import tempfile
        import os

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            fichier.save(tmp.name)
            tmp_path = tmp.name

        doc = fitz.open(tmp_path)
        texte = ""
        for page in doc:
            texte += page.get_text()
        doc.close()
        os.unlink(tmp_path)

        if not texte.strip():
            return jsonify({"erreur": "Impossible d'extraire le texte du PDF"}), 400

        # Limiter le texte pour ne pas depasser le contexte
        texte_limite = texte[:8000]
        question = request.form.get("question", "Fais une analyse complète de ce document.")

        prompt = (
            "Tu es un assistant juridique expert en droit camerounais et droit OHADA "
            "au service du Cabinet de Maitre Boubou.\n\n"
            f"Question du client : {question}\n\n"
            "Analyse ce document juridique en tenant compte de la question posée et fournis :\n\n"
            "## 1. Nature et type du document\n"
            "## 2. Parties impliquees\n"
            "## 3. Clauses et points essentiels\n"
            "## 4. Risques juridiques identifies\n"
            "## 5. Points necessitant attention ou modification\n"
            "## 6. Recommandations du cabinet\n\n"
            f"DOCUMENT :\n{texte_limite}"
        )

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )

        return jsonify({"analyse": response.content[0].text})

    except Exception as e:
        print("ERREUR ANALYSER:", str(e))
        return jsonify({"erreur": str(e)}), 500


@app.route("/generer", methods=["POST"])
def generer():
    try:
        data = request.json
        type_doc = data.get("type", "")
        donnees = data.get("donnees", {})

        prompts = {
            "contrat_travail": f"""Redige un contrat de travail professionnel selon le Code du travail camerounais avec ces informations :
- Employeur : {donnees.get('employeur', '')}
- Employe : {donnees.get('employe', '')}
- Poste : {donnees.get('poste', '')}
- Salaire : {donnees.get('salaire', '')} FCFA
- Type : {donnees.get('type_contrat', '')}
- Date debut : {donnees.get('date_debut', '')}
Inclus toutes les clauses obligatoires selon la legislation camerounaise.""",

            "mise_en_demeure": f"""Redige une mise en demeure formelle et professionnelle :
- Expediteur : {donnees.get('expediteur', '')}
- Destinataire : {donnees.get('destinataire', '')}
- Objet : {donnees.get('objet', '')}
- Faits : {donnees.get('details', '')}
- Delai accorde : {donnees.get('delai', '')} jours""",

            "statuts_sarl": f"""Redige les statuts complets d'une SARL selon l'Acte Uniforme OHADA :
- Denomination : {donnees.get('nom_societe', '')}
- Siege : {donnees.get('siege', '')}
- Capital : {donnees.get('capital', '')} FCFA
- Objet : {donnees.get('objet', '')}
- Gerant : {donnees.get('gerant', '')}
Inclus tous les articles obligatoires selon l'AUSCGIE 2014.""",

            "fiche_client": f"""Etablis une fiche client juridique structuree pour le Cabinet de Maitre Boubou :
- Client : {donnees.get('nom_client', '')}
- Cas : {donnees.get('description', '')}
- Parties : {donnees.get('parties', '')}
- Contexte : {donnees.get('contexte', '')}
Inclus : analyse juridique, textes applicables, risques, strategie recommandee et prochaines etapes.""",

            "contrat_bail": f"""Redige un contrat de bail selon la legislation camerounaise :
- Bailleur : {donnees.get('bailleur', '')}
- Locataire : {donnees.get('locataire', '')}
- Bien : {donnees.get('adresse', '')}
- Loyer : {donnees.get('loyer', '')} FCFA/mois
- Duree : {donnees.get('duree', '')}
- Type : {donnees.get('type_bail', '')}""",

            "conclusions": f"""Redige des conclusions judiciaires professionnelles :
- Juridiction : {donnees.get('tribunal', '')}
- Demandeur : {donnees.get('demandeur', '')}
- Defendeur : {donnees.get('defendeur', '')}
- Faits : {donnees.get('faits', '')}
- Demandes : {donnees.get('demandes', '')}
Structure avec : POUR CES MOTIFS et demandes formelles."""
        }

        if type_doc not in prompts:
            return jsonify({"erreur": "Type de document inconnu"}), 400

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=3000,
            messages=[{"role": "user", "content": prompts[type_doc]}]
        )

        return jsonify({"document": response.content[0].text})

    except Exception as e:
        print("ERREUR GENERER:", str(e))
        return jsonify({"erreur": str(e)}), 500
    # ============ UPLOAD DOCUMENT ============
@app.route("/upload_document", methods=["POST"])
def upload_document():
    try:
        if "fichier" not in request.files:
            return jsonify({"erreur": "Aucun fichier recu"}), 400

        fichier = request.files["fichier"]
        cabinet = request.form.get("cabinet", "Cabinet Boubou")

        if not fichier.filename.endswith(".pdf"):
            return jsonify({"erreur": "Format PDF uniquement"}), 400

        import fitz, tempfile, os, uuid

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            fichier.save(tmp.name)
            tmp_path = tmp.name

        doc = fitz.open(tmp_path)
        pages_texte = []
        for i, page in enumerate(doc):
            texte = page.get_text().strip()
            if texte:
                pages_texte.append({"page": i + 1, "texte": texte})
        doc.close()
        os.unlink(tmp_path)

        if not pages_texte:
            return jsonify({"erreur": "Impossible d'extraire le texte du PDF"}), 400

        # Sauvegarder le document
        doc_id = str(uuid.uuid4())
        supabase.table("documents").insert({
            "id": doc_id,
            "nom": fichier.filename,
            "type": "juridique",
            "cabinet": cabinet
        }).execute()

        # Découper et sauvegarder les chunks
        chunks_inseres = 0
        for page_data in pages_texte:
            texte = page_data["texte"]
            # Découper en chunks de ~500 caractères
            taille_chunk = 500
            for j in range(0, len(texte), taille_chunk):
                chunk_texte = texte[j:j + taille_chunk].strip()
                if len(chunk_texte) > 50:
                    supabase.table("chunks").insert({
                        "document_id": doc_id,
                        "contenu": chunk_texte,
                        "page_numero": page_data["page"]
                    }).execute()
                    chunks_inseres += 1

        return jsonify({
            "succes": True,
            "message": f"Document '{fichier.filename}' indexé avec succès",
            "chunks": chunks_inseres,
            "document_id": doc_id
        })

    except Exception as e:
        print("ERREUR UPLOAD:", str(e))
        return jsonify({"erreur": str(e)}), 500


# ============ LISTE DOCUMENTS ============
@app.route("/liste_documents", methods=["GET"])
def liste_documents():
    try:
        result = supabase.table("documents").select("id, nom, type, cabinet").order("nom").execute()
        return jsonify(result.data)
    except Exception as e:
        return jsonify({"erreur": str(e)}), 500


# ============ SUPPRIMER DOCUMENT ============
@app.route("/supprimer_document", methods=["DELETE"])
def supprimer_document():
    try:
        data = request.json
        doc_id = data.get("id")
        if not doc_id:
            return jsonify({"erreur": "ID manquant"}), 400
        # Supprimer les chunks d'abord
        supabase.table("chunks").delete().eq("document_id", doc_id).execute()
        # Supprimer le document
        supabase.table("documents").delete().eq("id", doc_id).execute()
        return jsonify({"succes": True, "message": "Document supprimé"})
    except Exception as e:
        return jsonify({"erreur": str(e)}), 500


# ============ SAUVEGARDER DOCUMENT GÉNÉRÉ ============
@app.route("/sauvegarder_document", methods=["POST"])
def sauvegarder_document():
    try:
        data = request.json
        nom = data.get("nom", "Document sans titre")
        contenu = data.get("contenu", "")
        type_doc = data.get("type_doc", "genere")

        if not contenu:
            return jsonify({"erreur": "Contenu vide"}), 400

        import uuid
        doc_id = str(uuid.uuid4())
        supabase.table("documents").insert({
            "id": doc_id,
            "nom": nom,
            "type": type_doc,
            "cabinet": "Cabinet Boubou"
        }).execute()

        # Indexer le contenu en chunks pour qu'il soit cherchable dans le chat
        taille_chunk = 500
        for j in range(0, len(contenu), taille_chunk):
            chunk_texte = contenu[j:j + taille_chunk].strip()
            if len(chunk_texte) > 50:
                supabase.table("chunks").insert({
                    "document_id": doc_id,
                    "contenu": chunk_texte,
                    "page_numero": 1
                }).execute()

        return jsonify({"succes": True, "message": f"Document '{nom}' sauvegardé", "document_id": doc_id})

    except Exception as e:
        print("ERREUR SAUVEGARDE:", str(e))
        return jsonify({"erreur": str(e)}), 500
if __name__ == "__main__":
    app.run(debug=True, port=5000)
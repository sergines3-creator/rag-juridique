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

@app.route("/historique", methods=["GET"])
def get_historique():
    try:
        result = supabase.table("historique").select(
            "id, question, reponse, date"
        ).order("date", desc=True).limit(50).execute()
        return jsonify(result.data)
    except Exception as e:
        print("ERREUR GET HISTORIQUE:", str(e))
        return jsonify([])

@app.route("/historique", methods=["POST"])
def save_historique():
    try:
        data = request.json
        question = data.get("question", "").strip()
        reponse = data.get("reponse", "").strip()

        if not question:
            return jsonify({"erreur": "Question vide"}), 400

        date_str = datetime.now().strftime("%d/%m/%Y %H:%M")
        supabase.table("historique").insert({
            "question": question,
            "reponse": reponse,
            "date": date_str
        }).execute()

        return jsonify({"ok": True})

    except Exception as e:
        print("ERREUR SAVE HISTORIQUE:", str(e))
        return jsonify({"erreur": str(e)}), 500

@app.route("/historique", methods=["DELETE"])
def clear_historique():
    try:
        supabase.table("historique").delete().neq("id", 0).execute()
        return jsonify({"ok": True})
    except Exception as e:
        print("ERREUR DELETE HISTORIQUE:", str(e))
        return jsonify({"erreur": str(e)}), 500

@app.route("/documents")
def documents():
    try:
        result = supabase.table("documents").select("nom, type, date_ajout").execute()
        return jsonify(result.data)
    except Exception as e:
        return jsonify({"erreur": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True, port=5000)
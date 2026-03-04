import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from datetime import datetime
from supabase import create_client

from llama_index.core import VectorStoreIndex, StorageContext, Settings
from llama_index.core.memory import ChatMemoryBuffer
from llama_index.llms.anthropic import Anthropic as LlamaAnthropic
from llama_index.vector_stores.supabase import SupabaseVectorStore
from llama_index.embeddings.huggingface import HuggingFaceEmbedding

# ─── CONFIG ──────────────────────────────────────────────
import os
SUPABASE_URL = os.environ.get("https://ylmlmoyhrngoxbhojlmt.supabase.co")
SUPABASE_KEY = os.environ.get("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlsbWxtb3locm5nb3hiaG9qbG10Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzI1MjMxMzAsImV4cCI6MjA4ODA5OTEzMH0.64RvsKIFKX6Re0NprJThZEKoKh9yZ11YwKXPQ9s7RD0")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY")
DB_URL = os.environ.get("https://github.com/sergines3-creator/rag-juridique.git")

app = Flask(__name__)
CORS(app)

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ─── LLAMAINDEX SETUP ─────────────────────────────────────
print("Initialisation de LlamaIndex...")
embed_model = HuggingFaceEmbedding(model_name="paraphrase-multilingual-mpnet-base-v2")
Settings.embed_model = embed_model
llm = LlamaAnthropic(
    model="claude-sonnet-4-20250514",
    api_key=ANTHROPIC_KEY,
    max_tokens=2000,
    system_prompt=(
        "Tu es un assistant juridique expert en droit camerounais et droit OHADA, "
        "au service du Cabinet de Maitre Boubou. "
        "Base toi uniquement sur les documents juridiques fournis. "
        "Cite toujours la source exacte et la page. "
        "Utilise un langage juridique professionnel. "
        "Structure ta reponse : 1. Definition et contexte "
        "2. Base legale applicable 3. Analyse juridique "
        "4. Points essentiels 5. Recommandation."
    )
)

vector_store = SupabaseVectorStore(
    postgres_connection_string=DB_URL,
    collection_name="chunks"
)

storage_context = StorageContext.from_defaults(vector_store=vector_store)
index = VectorStoreIndex.from_vector_store(
    vector_store,
    storage_context=storage_context
)

print("LlamaIndex initialise !")

# Stockage des sessions en memoire
chat_sessions = {}

def get_chat_engine(session_id):
    if session_id not in chat_sessions:
        memory = ChatMemoryBuffer.from_defaults(token_limit=4000)
        chat_engine = index.as_chat_engine(
            chat_mode="condense_plus_context",
            memory=memory,
            llm=llm,
            similarity_top_k=10,
            verbose=False
        )
        chat_sessions[session_id] = chat_engine
        print("Nouvelle session creee:", session_id)
    return chat_sessions[session_id]

# ─── ROUTES ──────────────────────────────────────────────

@app.route("/")
def index_page():
    return render_template("index.html")

@app.route("/question", methods=["POST"])
def question():
    try:
        data = request.json
        q = data.get("question", "").strip()
        session_id = data.get("session_id", "default")

        if not q:
            return jsonify({"erreur": "Question vide"}), 400

        chat_engine = get_chat_engine(session_id)
        response = chat_engine.chat(q)

        sources = []
        if hasattr(response, "source_nodes") and response.source_nodes:
            for node in response.source_nodes:
                if hasattr(node, "metadata"):
                    doc_id = node.metadata.get("document_id")
                    page = node.metadata.get("page_numero", "?")
                    if doc_id:
                        try:
                            result = supabase.table("documents").select("nom").eq("id", doc_id).execute()
                            if result.data:
                                nom = result.data[0]["nom"].replace(".pdf", "").replace("-", " ").replace("_", " ")
                                sources.append(f"{nom} - Page {page}")
                        except Exception:
                            pass

        return jsonify({
            "reponse": str(response),
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
        if session_id in chat_sessions:
            del chat_sessions[session_id]
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
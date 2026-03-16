from dotenv import load_dotenv
load_dotenv()

import sys
import io
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from flask import Flask, render_template, request, jsonify, send_file
from flask_cors import CORS
from datetime import datetime
from anthropic import Anthropic
from supabase import create_client
from bs4 import BeautifulSoup
import requests
import uuid
import tempfile
import os
import re
from predict_endpoint import predict_bp 

# ReportLab
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY

def log_erreur(contexte, erreur):
    message = str(erreur)
    # Masquer les infos sensibles
    message = message.replace(SUPABASE_KEY or "", "***")
    message = message.replace(ANTHROPIC_KEY or "", "***")
    print(f"[ERREUR] {contexte}: {message[:200]}")
    app.register_blueprint(predict_bp) #

# ─── CONFIG ──────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY")

app = Flask(__name__)
CORS(app)
app.config["JWT_SECRET_KEY"] = os.environ.get("JWT_SECRET_KEY", "dev-secret-change-en-prod")
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = False
jwt = JWTManager(app)

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
client = Anthropic(api_key=ANTHROPIC_KEY)

def get_session(session_id):
    try:
        result = supabase.table("sessions").select("historique").eq("id", session_id).execute()
        if result.data:
            return result.data[0]["historique"]
    except Exception:
        pass
    return []

def save_session(session_id, historique):
    try:
        supabase.table("sessions").upsert({
            "id": session_id,
            "historique": historique,
            "updated_at": datetime.now().isoformat()
        }).execute()
    except Exception as e:
        print("ERREUR SESSION:", str(e))

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

@app.route("/login", methods=["POST"])
@limiter.limit("10 per minute")
def login():
    try:
        data = request.json
        password = data.get("password", "")
        mot_de_passe_correct = os.environ.get("CABINET_PASSWORD", "Cabinet-Boubou@123")
        if password == mot_de_passe_correct:
            token = create_access_token(identity="cabinet_boubou")
            return jsonify({"token": token})
        else:
            return jsonify({"erreur": "Mot de passe incorrect"}), 401
    except Exception as e:
        return jsonify({"erreur": str(e)}), 500

@app.route("/question", methods=["POST"])
@jwt_required()
@limiter.limit("30 per minute")
def question():
    try:
        data = request.json
        q = data.get("question", "").strip()
        session_id = data.get("session_id", "default")

        if not q:
            return jsonify({"erreur": "Question vide"}), 400

        historique_session = get_session(session_id)
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
        historique_session.append({"question": q, "reponse": reponse_texte})
        save_session(session_id, historique_session)

        return jsonify({
            "reponse": reponse_texte,
            "sources": list(set(sources))
        })

    except Exception as e:
        log_erreur("QUESTION", e)
        return jsonify({"reponse": "Erreur : " + str(e), "sources": []}), 500


@app.route("/nouvelle-conversation", methods=["POST"])
def nouvelle_conversation():
    try:
        data = request.json
        session_id = data.get("session_id", "default")
        supabase.table("sessions").delete().eq("id", session_id).execute()
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

        import fitz

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

        texte_limite = texte[:8000]
        question = request.form.get("question", "Fais une analyse complète de ce document.")

        prompt = (
            "Tu es un assistant expert polyvalent au service du Cabinet de Maitre Boubou, "
            "spécialisé en droit camerounais et OHADA, capable d'analyser et de relier "
            "tout document ou domaine au droit applicable : fiscal, comptable, douanier, "
            "bancaire et financier, QHSE et sécurité industrielle, aéronautique et transport, "
            "médical et pharmaceutique, statistiques et données, artistique et propriété "
            "intellectuelle, commerce et marchés internationaux, immobilier et construction, "
            "environnement et développement durable, télécommunications et numérique, "
            "droit social et ressources humaines, droit des affaires et investissements, "
            "droit pénal et procédures judiciaires.\n\n"
            "Tu établis des connexions pertinentes entre ces domaines et le droit camerounais, "
            "OHADA, CEMAC et les conventions internationales ratifiées par le Cameroun. "
            "Tu analyses tout document fourni quelle que soit sa nature et tu identifies "
            "les implications juridiques, les risques et les opportunités pour le cabinet.\n\n"
        )

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )

        return jsonify({"analyse": response.content[0].text})

    except Exception as e:
        log_erreur("ANALYSER", e)
        return jsonify({"erreur": str(e)}), 500


@app.route("/generer", methods=["POST"])
@jwt_required()
@limiter.limit("20 per minute")
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
        log_erreur("GENERER", e)
        return jsonify({"erreur": str(e)}), 500


# ============ UPLOAD DOCUMENT ============
@app.route("/upload_document", methods=["POST"])
@jwt_required()
@limiter.limit("10 per minute")
def upload_document():
    try:
        if "fichier" not in request.files:
            return jsonify({"erreur": "Aucun fichier recu"}), 400

        fichier = request.files["fichier"]
        cabinet = request.form.get("cabinet", "Cabinet Boubou")

        if not fichier.filename.endswith(".pdf"):
            return jsonify({"erreur": "Format PDF uniquement"}), 400

        # Vérifier la signature du fichier (magic bytes)
        header = fichier.read(5)
        fichier.seek(0)
        if header != b'%PDF-':
            return jsonify({"erreur": "Fichier invalide — ce n'est pas un vrai PDF"}), 400

        # Vérifier la taille max (10 Mo)
        fichier.seek(0, 2)
        taille = fichier.tell()
        fichier.seek(0)
        if taille > 10 * 1024 * 1024:
            return jsonify({"erreur": "Fichier trop volumineux — max 10 Mo"}), 400
        
        # Vérifier si le document existe déjà
        nom_fichier = fichier.filename.lower().strip()
        docs_existants = supabase.table("documents").select("nom").execute()
        noms_existants = [d["nom"].lower().strip() for d in docs_existants.data]
        if nom_fichier in noms_existants:
            return jsonify({"erreur": f"Ce document existe déjà dans la base : '{fichier.filename}'"}), 400

        import fitz

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

        doc_id = str(uuid.uuid4())
        supabase.table("documents").insert({
            "id": doc_id,
            "nom": fichier.filename,
            "type": "juridique",
            "cabinet": cabinet
        }).execute()

        chunks_inseres = 0
        for page_data in pages_texte:
            texte = page_data["texte"]
            for j in range(0, len(texte), 500):
                chunk_texte = texte[j:j + 500].strip()
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
        log_erreur("UPLOAD", e)
        return jsonify({"erreur": str(e)}), 500


# ============ LISTE DOCUMENTS ============
@app.route("/liste_documents", methods=["GET"])
@jwt_required()
def liste_documents():
    try:
        result = supabase.table("documents").select("id, nom, type, cabinet").order("nom").execute()
        return jsonify(result.data)
    except Exception as e:
        return jsonify({"erreur": str(e)}), 500


# ============ SUPPRIMER DOCUMENT ============
@app.route("/supprimer_document", methods=["DELETE"])
@jwt_required()
def supprimer_document():
    try:
        data = request.json
        doc_id = data.get("id")
        if not doc_id:
            return jsonify({"erreur": "ID manquant"}), 400
        supabase.table("chunks").delete().eq("document_id", doc_id).execute()
        supabase.table("documents").delete().eq("id", doc_id).execute()
        return jsonify({"succes": True, "message": "Document supprimé"})
    except Exception as e:
        return jsonify({"erreur": str(e)}), 500


# ============ SAUVEGARDER DOCUMENT GÉNÉRÉ ============
@app.route("/sauvegarder_document", methods=["POST"])
@jwt_required()
def sauvegarder_document():
    try:
        data = request.json
        nom = data.get("nom", "Document sans titre")
        contenu = data.get("contenu", "")
        type_doc = data.get("type_doc", "genere")

        if not contenu:
            return jsonify({"erreur": "Contenu vide"}), 400

        doc_id = str(uuid.uuid4())
        supabase.table("documents").insert({
            "id": doc_id,
            "nom": nom,
            "type": type_doc,
            "cabinet": "Cabinet Boubou"
        }).execute()

        for j in range(0, len(contenu), 500):
            chunk_texte = contenu[j:j + 500].strip()
            if len(chunk_texte) > 50:
                supabase.table("chunks").insert({
                    "document_id": doc_id,
                    "contenu": chunk_texte,
                    "page_numero": 1
                }).execute()

        return jsonify({"succes": True, "message": f"Document '{nom}' sauvegardé", "document_id": doc_id})

    except Exception as e:
        log_erreur("SAUVEGARDE", e)
        return jsonify({"erreur": str(e)}), 500


# ============ EXPORT PDF ============
@app.route("/export_pdf", methods=["POST"])
@jwt_required()
def export_pdf():
    try:
        data = request.json
        contenu = data.get("contenu", "")
        type_doc = data.get("type_doc", "document")
        nom = data.get("nom", "Document juridique")

        if not contenu:
            return jsonify({"erreur": "Contenu vide"}), 400

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=2.5*cm,
            leftMargin=2.5*cm,
            topMargin=2.5*cm,
            bottomMargin=2.5*cm
        )

        GOLD = colors.HexColor("#C6A75E")
        DARK = colors.HexColor("#0F172A")
        GRAY = colors.HexColor("#64748B")

        style_cabinet = ParagraphStyle(
            "cabinet", fontName="Helvetica-Bold", fontSize=16,
            textColor=GOLD, alignment=TA_CENTER, spaceAfter=4
        )
        style_sous_titre = ParagraphStyle(
            "sous_titre", fontName="Helvetica", fontSize=9,
            textColor=GRAY, alignment=TA_CENTER, spaceAfter=2
        )
        style_titre_doc = ParagraphStyle(
            "titre_doc", fontName="Helvetica-Bold", fontSize=13,
            textColor=DARK, alignment=TA_CENTER, spaceBefore=16, spaceAfter=8
        )
        style_corps = ParagraphStyle(
            "corps", fontName="Helvetica", fontSize=10,
            textColor=DARK, leading=16, alignment=TA_JUSTIFY, spaceAfter=8
        )
        style_h1 = ParagraphStyle(
            "h1", fontName="Helvetica-Bold", fontSize=12,
            textColor=GOLD, spaceBefore=12, spaceAfter=6
        )
        style_h2 = ParagraphStyle(
            "h2", fontName="Helvetica-Bold", fontSize=11,
            textColor=DARK, spaceBefore=10, spaceAfter=4
        )
        style_date = ParagraphStyle(
            "date", fontName="Helvetica-Oblique", fontSize=9,
            textColor=GRAY, alignment=TA_CENTER, spaceAfter=4
        )

        elements = []
        elements.append(Paragraph("Cabinet de Maître Boubou", style_cabinet))
        elements.append(Paragraph("Avocat au Barreau du Cameroun · Douala", style_sous_titre))
        elements.append(Paragraph(
            f"Document généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')}",
            style_date
        ))
        elements.append(HRFlowable(width="100%", thickness=1, color=GOLD, spaceAfter=12))
        elements.append(Paragraph(nom.upper(), style_titre_doc))
        elements.append(HRFlowable(width="60%", thickness=0.5, color=GOLD, spaceAfter=16))

        for ligne in contenu.split("\n"):
            ligne = ligne.strip()
            if not ligne:
                elements.append(Spacer(1, 6))
                continue
            if ligne.startswith("### "):
                elements.append(Paragraph(ligne[4:], style_h2))
            elif ligne.startswith("## "):
                elements.append(Paragraph(ligne[3:], style_h1))
            elif ligne.startswith("# "):
                elements.append(Paragraph(ligne[2:], style_h1))
            else:
                ligne = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', ligne)
                ligne = re.sub(r'\*(.+?)\*', r'<i>\1</i>', ligne)
                elements.append(Paragraph(ligne, style_corps))

        elements.append(Spacer(1, 20))
        elements.append(HRFlowable(width="100%", thickness=0.5, color=GOLD))
        elements.append(Paragraph(
            "Document généré par l'assistant juridique IA · Cabinet de Maître Boubou · Confidentiel",
            style_sous_titre
        ))

        doc.build(elements)
        buffer.seek(0)

        nom_fichier = nom.replace(" ", "_").replace("/", "-") + ".pdf"
        return send_file(
            buffer,
            as_attachment=True,
            download_name=nom_fichier,
            mimetype="application/pdf"
        )

    except Exception as e:
        log_erreur("EXPORT PDF", e)
        return jsonify({"erreur": str(e)}), 500


# ============ VEILLE JURIDIQUE ============
SOURCES_VEILLE = [
    {
        "id": "ohada",
        "nom": "OHADA",
        "url": "https://www.ohada.com/actes-uniformes.html",
        "domaine": "ohada.com",
        "actif": True
    },
    {
        "id": "izf",
        "nom": "CEMAC / IZF",
        "url": "https://www.izf.net/textes-juridiques",
        "domaine": "izf.net",
        "actif": True
    },
    {
        "id": "juriafrica",
        "nom": "Jurisprudence Cameroun",
        "url": "https://www.legal-tools.org/search/?q=cameroun&type=legislation",
        "domaine": "legal-tools.org",
        "actif": True
    },
    {
        "id": "spm",
        "nom": "Lois Camerounaises",
        "url": "https://www.droit-afrique.com/pays/cameroun",
        "domaine": "droit-afrique.com",
        "actif": True
    },
    {
        "id": "ccja",
        "nom": "Jurisprudence CCJA OHADA",
        "url": "https://www.ccja-ohada.org/decisions",
        "domaine": "ccja-ohada.org",
        "actif": True
    },
    {
        "id": "wipo",
        "nom": "Propriété Intellectuelle Cameroun (OMPI)",
        "url": "https://www.wipo.int/wipolex/fr/profile/CM",
        "domaine": "wipo.int",
        "actif": True
    },
    {
        "id": "juridicas",
        "nom": "Droit Comparé International",
        "url": "https://www.juridicas.unam.mx",
        "domaine": "juridicas.unam.mx",
        "actif": True
    },
]


@app.route("/veille/sources", methods=["GET"])
@jwt_required()
def veille_sources():
    return jsonify(SOURCES_VEILLE)


@app.route("/veille/synchroniser", methods=["POST"])
@jwt_required()
@limiter.limit("5 per minute")
def veille_synchroniser():
    try:
        data = request.json
        source_id = data.get("source_id")

        sources = SOURCES_VEILLE
        if source_id:
            sources = [s for s in SOURCES_VEILLE if s["id"] == source_id]

        resultats = []
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        for source in sources:
            resultat = {
                "source": source["nom"],
                "source_id": source["id"],
                "nouveaux": 0,
                "doublons": 0,
                "erreurs": 0,
                "details": []
            }

            try:
                res = requests.get(source["url"], headers=headers, timeout=15)
                res.raise_for_status()
                soup = BeautifulSoup(res.text, "html.parser")

                liens_pdf = []
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    texte = a.get_text(strip=True)
                    if href.endswith(".pdf") or ".pdf" in href.lower():
                        if not href.startswith("http"):
                            base = f"https://{source['domaine']}"
                            href = base + href if href.startswith("/") else base + "/" + href
                        # Nom depuis URL
                        nom_depuis_url = href.split("/")[-1].replace("-", " ").replace("_", " ").replace(".pdf", "")
                        # Nom depuis élément parent
                        parent = a.find_parent(["li", "tr", "div", "p"])
                        titre_parent = parent.get_text(strip=True)[:80] if parent else ""
                        # Choisir le meilleur nom
                        if len(titre_parent) > 5 and titre_parent.lower() not in ["télécharger", "download", ""]:
                            nom_final = titre_parent
                        elif len(nom_depuis_url) > 5:
                            nom_final = nom_depuis_url
                        else:
                            nom_final = texte or href.split("/")[-1]
                        liens_pdf.append({"url": href, "nom": nom_final})

                docs_existants = supabase.table("documents").select("nom").execute()
                noms_existants = [d["nom"].lower() for d in docs_existants.data]

                for lien in liens_pdf[:10]:
                    nom_fichier = lien["nom"][:100] + ".pdf" if not lien["nom"].endswith(".pdf") else lien["nom"][:100]
                    nom_clean = nom_fichier.lower().strip()

                    if nom_clean in noms_existants:
                        resultat["doublons"] += 1
                        continue

                    try:
                        import fitz
                        pdf_res = requests.get(lien["url"], headers=headers, timeout=30)
                        if pdf_res.status_code == 200 and len(pdf_res.content) > 1000:
                            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                                tmp.write(pdf_res.content)
                                tmp_path = tmp.name

                            doc_fitz = fitz.open(tmp_path)
                            pages_texte = []
                            for i, page in enumerate(doc_fitz):
                                texte = page.get_text().strip()
                                if texte:
                                    pages_texte.append({"page": i + 1, "texte": texte})
                            doc_fitz.close()
                            os.unlink(tmp_path)

                            if pages_texte:
                                doc_id = str(uuid.uuid4())
                                supabase.table("documents").insert({
                                    "id": doc_id,
                                    "nom": nom_fichier,
                                    "type": source["id"],
                                    "cabinet": "Veille automatique"
                                }).execute()

                                chunks_inseres = 0
                                for page_data in pages_texte:
                                    texte = page_data["texte"]
                                    for j in range(0, len(texte), 500):
                                        chunk_texte = texte[j:j+500].strip()
                                        if len(chunk_texte) > 50:
                                            supabase.table("chunks").insert({
                                                "document_id": doc_id,
                                                "contenu": chunk_texte,
                                                "page_numero": page_data["page"]
                                            }).execute()
                                            chunks_inseres += 1

                                resultat["nouveaux"] += 1
                                resultat["details"].append(f"✓ {nom_fichier} ({chunks_inseres} chunks)")
                        else:
                            resultat["erreurs"] += 1

                    except Exception as e:
                        resultat["erreurs"] += 1
                        print(f"Erreur téléchargement {lien['url']}: {e}")

            except Exception as e:
                resultat["erreurs"] += 1
                resultat["details"].append(f"✗ Erreur accès source : {str(e)[:100]}")
                print(f"Erreur source {source['nom']}: {e}")

            resultats.append(resultat)

        return jsonify({"succes": True, "resultats": resultats})

    except Exception as e:
        log_erreur("VEILLE", e)
        return jsonify({"erreur": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
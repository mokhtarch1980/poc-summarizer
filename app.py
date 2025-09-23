# -*- coding: utf-8 -*-
"""
POC Résumeur de documents — version robuste UTF-8 (Windows-safe)
"""

import io
import os
import sys
import re
import csv
import time
import string
import datetime
import unicodedata
import traceback
import json
import httpx
from pathlib import Path
from typing import List

# ==== Forcer UTF-8 tôt (important sur Windows/Consoles) =======================
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
except Exception:
    pass

# ==== Dépendances =============================================================
import streamlit as st
from pypdf import PdfReader
from docx import Document
from dotenv import load_dotenv
from openai import OpenAI  

# ==== Chargement .env (clé OpenAI, modèle) ====================================
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# ============================ Constantes ======================================
MAX_FILE_SIZE_MB = 10
MAX_CHARS = 15000
MIN_CHARS_FOR_SUMMARY = 80

# ============================ Utilitaires UTF-8 ===============================
def to_safe_utf8_text(x: object) -> str:
    """pour convertir n'importe quoi en str UTF-8 safe (NFC)."""
    s = str(x)
    return unicodedata.normalize("NFC", s)

def bytes_utf8(s: str) -> bytes:
    return to_safe_utf8_text(s).encode("utf-8", errors="replace")

def _sleep_backoff(attempt: int):
    """Backoff simple: 0.5s, 1s, 2s, 4s ... jusqu'à 8s."""
    time.sleep(min(8, 0.5 * (2 ** attempt)))

# ============================ Diagnostic & Réseau =============================
def show_exception_panel(e: Exception):
    """Panneau de diagnostic: type, message, traceback et éventuelle réponse HTTP."""
    etype = type(e).__name__
    st.error(f"Type d'erreur : **{etype}**")
    st.write("**Message (str)**:", str(e))
    st.write("**Repr**:", repr(e))
    st.write("**Traceback**")
    st.code("".join(traceback.format_exc()), language="text")

    if isinstance(e, httpx.HTTPStatusError):
        resp = e.response
        st.write("**HTTP Status**:", resp.status_code)
        try:
            st.write("**Réponse JSON**:")
            st.json(resp.json())
        except Exception:
            st.write("**Réponse texte brute**:")
            st.code(resp.text, language="json")
    elif isinstance(e, httpx.HTTPError):
        st.write("**HTTPError (détails)**:", getattr(e, "args", None))

def openai_ping(verify=True):
    """Teste /v1/models pour checker clé/réseau/proxy/SSL."""
    if not OPENAI_API_KEY:
        return False, "OPENAI_API_KEY manquante"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json; charset=utf-8",
    }
    try:
        with httpx.Client(timeout=20.0, trust_env=True, verify=verify) as client:
            r = client.get("https://api.openai.com/v1/models", headers=headers)
            r.raise_for_status()
            data = r.json()
        models = [m.get("id") for m in data.get("data", []) if isinstance(m, dict)]
        return True, {"models_count": len(models), "model_requested": OPENAI_MODEL}
    except Exception as e:
        return False, e

# ============================ Logs (CSV) ======================================
LOG_DIR = Path("data")
LOG_DIR.mkdir(exist_ok=True)
LOG_PATH = LOG_DIR / "logs.csv"

def log_usage(file_name: str, method: str, length: str, lang: str, char_count: int, duration_ms: int):
    new = not LOG_PATH.exists()
    with LOG_PATH.open("a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["timestamp", "file", "method", "length", "lang", "chars", "duration_ms"])
        w.writerow([
            datetime.datetime.now().isoformat(timespec="seconds"),
            to_safe_utf8_text(file_name),
            to_safe_utf8_text(method),
            to_safe_utf8_text(length),
            to_safe_utf8_text(lang),
            int(char_count),
            int(duration_ms),
        ])

# ============================ Extraction de texte =============================
def extract_text_from_txt(file_bytes: bytes) -> str:
    return file_bytes.decode("utf-8", errors="ignore")

def extract_text_from_pdf(file_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(file_bytes))
    texts = []
    for page in reader.pages:
        texts.append(page.extract_text() or "")
    return "\n".join(texts)

def extract_text_from_docx(file_bytes: bytes) -> str:
    bio = io.BytesIO(file_bytes)
    doc = Document(bio)
    return "\n".join(p.text for p in doc.paragraphs)

def clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    return text.strip()

# ============================ Résumeur local ==================================
STOPWORDS = set("""
a au aux avec ce ces dans de des du elle en et eux il je la le les leur lui ma mais me
meme mes moi mon ne nos notre nous on ou par pas pour qu que qui sa se ses son sur ta te
tes ton toi tu un une vos votre vous c d j l t y s qu il n s
""".split())

def sentence_tokenize(text: str) -> List[str]:
    parts = re.split(r'(?<=[\.\?\!])\s+', text)
    return [p.strip() for p in parts if p and len(p.strip()) > 0]

def word_tokenize(text: str) -> List[str]:
    table = str.maketrans("", "", string.punctuation + "«»“”„‹›")
    return text.translate(table).lower().split()

def summarize_by_frequency(text: str, n_sentences: int = 5) -> str:
    sentences = sentence_tokenize(text)
    if not sentences:
        return ""
    words = word_tokenize(text)
    freqs = {}
    for w in words:
        if w in STOPWORDS or not w.isalpha():
            continue
        freqs[w] = freqs.get(w, 0) + 1

    scored = []
    for sent in sentences:
        score = 0
        for w in word_tokenize(sent):
            score += freqs.get(w, 0)
        scored.append((score, sent))

    top = sorted(scored, key=lambda x: x[0], reverse=True)[:n_sentences]
    keep = {s for _, s in top}
    ordered = [s for s in sentences if s in keep]
    return " ".join(ordered)

# ============================ Résumé via OpenAI (HTTPX UTF-8) =================
def summarize_with_llm(text: str, length: str = "court", language: str = "fr", verify=True) -> str:
    """
    - Appel OpenAI via HTTPX (UTF-8 ensure_ascii=False)
    - trust_env=True (proxy d'entreprise)
    - retries/backoff pour 429 rate-limit
    - message clair si insufficient_quota
    """
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY manquante (je l'ajoute dans un fichier .env).")

    body_text = to_safe_utf8_text(text[:MAX_CHARS])
    prompt = f"""
Tu es un assistant qui resume fidelement des documents.
Langue: {language.upper()}.
Longueur: {length}.

Consignes:
- Preserver l'idee principale et les points cles.
- Pas de contenu invente.
- Style clair et concis, phrases courtes.
- Si le texte est trop court ou vide, dis-le.

Texte a resumer (tronque si tres long):
\"\"\"{body_text}\"\"\""""

    payload = {
        "model": OPENAI_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }
    json_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json; charset=utf-8",
    }

    last_err = None
    with httpx.Client(timeout=60.0, trust_env=True, verify=verify) as client:
        for attempt in range(4):  # 4 tentatives max
            try:
                r = client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers=headers, content=json_bytes
                )
                r.raise_for_status()
                data = r.json()
                content = data["choices"][0]["message"]["content"]
                return to_safe_utf8_text(content).strip()

            except httpx.HTTPStatusError as e:
                last_err = e
                status = e.response.status_code
                err_code = None
                try:
                    err_code = e.response.json().get("error", {}).get("code")
                except Exception:
                    pass

                if status == 429:
                    # Quota épuisé -> inutile de retenter
                    if err_code == "insufficient_quota":
                        raise RuntimeError(
                            "Quota insuffisant sur l'API OpenAI. "
                            "Ajoute un moyen de paiement/crédits dans Billing puis réessaie."
                        ) from e
                    # Vrai rate limit -> backoff + retry
                    if attempt < 3:
                        _sleep_backoff(attempt)
                        continue
                # autres erreurs -> stop
                raise
            except Exception as e:
                last_err = e
                raise
    if last_err:
        raise last_err

# ============================ ÉVALUATION (ROUGE/TF-IDF) =======================
def _tokens(s: str):
    table = str.maketrans("", "", string.punctuation + "«»“”„‹›")
    s = s.translate(table).lower()
    return [t for t in re.split(r"\s+", s) if t]

def _ngrams(tokens, n):
    return list(zip(*[tokens[i:] for i in range(n)]))

def rouge_n(ref: str, hyp: str, n: int = 1):
    """ROUGE-N (recall): chevauchement d'ngrams côté référence."""
    r_toks, h_toks = _tokens(ref), _tokens(hyp)
    if not r_toks or not h_toks:
        return 0.0
    r_ngrams = _ngrams(r_toks, n)
    h_ngrams = _ngrams(h_toks, n)
    if not r_ngrams or not h_ngrams:
        return 0.0
    from collections import Counter
    r_c, h_c = Counter(r_ngrams), Counter(h_ngrams)
    overlap = sum((r_c & h_c).values())
    return overlap / max(1, len(r_ngrams))

def lcs_len(a, b):
    """Longest Common Subsequence length (O(n*m) simple)."""
    A, B = _tokens(a), _tokens(b)
    n, m = len(A), len(B)
    if n == 0 or m == 0:
        return 0
    dp = [0] * (m + 1)
    for i in range(1, n + 1):
        prev = 0
        for j in range(1, m + 1):
            cur = dp[j]
            if A[i - 1] == B[j - 1]:
                dp[j] = prev + 1
            else:
                dp[j] = max(dp[j], dp[j - 1])
            prev = cur
    return dp[-1]

def rouge_l(ref: str, hyp: str):
    r = _tokens(ref)
    if not r:
        return 0.0
    return lcs_len(ref, hyp) / len(r)

def tfidf_cosine(a: str, b: str):
    """Cosine TF-IDF simple (sans scikit-learn)."""
    ta, tb = _tokens(a), _tokens(b)
    vocab = {}
    for t in set(ta + tb):
        vocab.setdefault(t, len(vocab))
    import math
    def tf(tokens):
        from collections import Counter
        c = Counter(tokens)
        return {t: c[t] / len(tokens) for t in c}
    tfa, tfb = tf(ta), tf(tb)
    # idf binaire sur 2 docs
    df = {}
    for t in set(ta): df[t] = df.get(t, 0) + 1
    for t in set(tb): df[t] = df.get(t, 0) + 1
    idf = {t: math.log(2 / df[t]) if df[t] > 0 else 0.0 for t in vocab}
    def vec(tfmap):
        v = [0.0] * len(vocab)
        for t, w in tfmap.items():
            idx = vocab.get(t)
            if idx is not None:
                v[idx] = w * idf.get(t, 0.0)
        return v
    va, vb = vec(tfa), vec(tfb)
    dot = sum(x*y for x, y in zip(va, vb))
    na = math.sqrt(sum(x*x for x in va))
    nb = math.sqrt(sum(x*x for x in vb))
    return (dot / (na * nb)) if na > 0 and nb > 0 else 0.0

def compression_ratio(src: str, summ: str):
    return len(_tokens(summ)) / max(1, len(_tokens(src)))

def reading_time_minutes(words: int, wpm: int = 220):
    import math
    return round(words / max(1, wpm), 2)

# ============================ UI Streamlit (sans emojis) ======================
st.set_page_config(page_title="POC – Résumé de documents", layout="centered")

st.title("POC — Résumé automatique")
st.write(
    "Charge un **TXT**, **PDF** ou **DOCX** et génère un **résumé**.\n\n"
    "- Mode **local** (baseline) sans IA\n"
    "- Mode **IA** (OpenAI) pour une meilleure qualité\n"
    "- Onglet **Évaluation** pour comparer à un résumé humain"
)

# ---- Réglages réseau/SSL
st.markdown("### Réglages réseau (SSL / proxy)")
col_a, col_b = st.columns([2, 1], vertical_alignment="center")
with col_a:
    ca_bundle_path = st.text_input(
        "Chemin vers le certificat racine (.pem) de l'entreprise (optionnel)",
        value=os.environ.get("SSL_CERT_FILE", "")
    )
with col_b:
    insecure_ssl = st.checkbox("Ignorer la vérif SSL (non recommandé)", value=False)

# verify à propager aux appels HTTPX
verify_target = False if insecure_ssl else (ca_bundle_path or True)

# ---- Panneau de diagnostic OpenAI
with st.expander("Diagnostic OpenAI (optionnel)"):
    st.caption("Appel simple /v1/models pour vérifier la clé, le réseau et le proxy.")
    if st.button("Tester la connexion OpenAI"):
        ok, info = openai_ping(verify=verify_target)
        if ok:
            st.success("Connexion OK")
            st.json(info)
        else:
            st.error("Connexion KO")
            show_exception_panel(info if isinstance(info, Exception) else Exception(str(info)))

# ============================ Onglets =========================================
tab_generate, tab_eval = st.tabs(["Génération", "Évaluation"])

# --------------------------- Onglet Génération --------------------------------
with tab_generate:
    uploaded = st.file_uploader("Uploader un document", type=["txt", "pdf", "docx"])
    summary_length = st.slider("Longueur du résumé (nb de phrases pour le mode local)", 3, 10, 5)

    # Contrôles IA
    use_llm = st.toggle("Activer l'IA (OpenAI)", value=False, help="Nécessite OPENAI_API_KEY dans .env")
    length_choice = st.select_slider("Style de résumé (IA)", options=["court", "moyen", "long"], value="court")
    lang_choice = st.selectbox("Langue du résumé", options=["fr", "en"], index=0)

    if uploaded is not None:
        # Taille du fichier
        if getattr(uploaded, "size", 0) > MAX_FILE_SIZE_MB * 1024 * 1024:
            st.error(f"Fichier trop gros (> {MAX_FILE_SIZE_MB} Mo).")
            st.stop()

        raw = uploaded.read()
        ext = uploaded.name.lower().split(".")[-1]

        # Extraction avec gestion d'erreur
        try:
            if ext == "txt":
                text = extract_text_from_txt(raw)
            elif ext == "pdf":
                text = extract_text_from_pdf(raw)
            elif ext == "docx":
                text = extract_text_from_docx(raw)
            else:
                st.error("Format non supporté.")
                text = ""
        except Exception:
            st.error("Impossible de lire ce fichier. (Format corrompu ou non pris en charge)")
            text = ""

        text = clean_text(text)

        # Cas vide / trop court
        if not text:
            if ext == "pdf":
                st.warning("Aucun texte détecté. Ce PDF est peut-être scanné (OCR non supporté pour l’instant).")
            else:
                st.warning("Aucun texte détecté dans ce fichier.")
            st.stop()

        if len(text) < MIN_CHARS_FOR_SUMMARY:
            st.info("Texte trop court pour générer un résumé utile.")
            st.write(text)
            st.stop()

        # Tronquage
        if len(text) > MAX_CHARS:
            st.warning(f"Document tronqué à {MAX_CHARS} caractères pour le traitement.")
            text = text[:MAX_CHARS]

        st.subheader("Aperçu du texte (début)")
        st.write(to_safe_utf8_text(text[:1000]) + ("..." if len(text) > 1000 else ""))

        if st.button("Générer le résumé"):
            t0 = time.perf_counter()
            with st.spinner("Génération du résumé..."):
                try:
                    if use_llm:
                        summary = summarize_with_llm(
                            text, length=length_choice, language=lang_choice, verify=verify_target
                        )
                        method = "LLM"
                        length_label = length_choice
                    else:
                        n = summary_length
                        summary = summarize_by_frequency(text, n_sentences=n)
                        method = "local"
                        length_label = f"{n} phrases"

                    duration_ms = int((time.perf_counter() - t0) * 1000)

                    if summary:
                        st.subheader("Résumé")
                        st.text_area("Texte du résumé", value=to_safe_utf8_text(summary), height=220)

                        # Mémoriser pour l'onglet Évaluation
                        st.session_state["last_summary"] = summary

                        # Log protégé (jamais bloquant)
                        try:
                            log_usage(uploaded.name, method, length_label, lang_choice, len(text), duration_ms)
                        except Exception as log_err:
                            st.warning(f"Log non enregistré: {to_safe_utf8_text(log_err)}")

                        # Téléchargement
                        try:
                            st.download_button(
                                label="Télécharger le résumé (.txt)",
                                data=bytes_utf8(summary),
                                file_name="resume.txt",
                                mime="text/plain; charset=utf-8",
                            )
                        except Exception as dl_err:
                            st.warning(f"Téléchargement indisponible: {to_safe_utf8_text(dl_err)}")
                    else:
                        st.info("Résumé vide : texte trop court ou non pertinent.")

                except Exception as e:
                    msg = str(e)
                    # Fallback auto lorsque le quota épuisé
                    if "Quota insuffisant" in msg or "insufficient_quota" in msg:
                        st.warning("Plus de crédits/quota côté OpenAI. Passage en mode **local** (sans IA).")
                        n = summary_length
                        summary = summarize_by_frequency(text, n_sentences=n)
                        method = "local"
                        length_label = f"{n} phrases"
                        duration_ms = int((time.perf_counter() - t0) * 1000)
                        st.subheader("Résumé (fallback local)")
                        st.text_area("Texte du résumé", value=to_safe_utf8_text(summary), height=220)
                        st.session_state["last_summary"] = summary
                        try:
                            log_usage(uploaded.name, method, length_label, lang_choice, len(text), duration_ms)
                        except Exception:
                            pass
                    else:
                        st.error("Une erreur est survenue pendant le résumé (voir détails ci-dessous).")
                        show_exception_panel(e)

# --------------------------- Onglet Évaluation --------------------------------
with tab_eval:
    st.subheader("Évaluer un résumé (référence humaine vs modèle)")
    col1, col2 = st.columns(2)
    with col1:
        src_text = st.text_area("Texte source (ou extrait)", height=180, key="eval_src")
        ref_text = st.text_area("Résumé humain (référence)", height=140, key="eval_ref")
    with col2:
        hyp_choice = st.radio("Hypothèse (résumé à évaluer)",
                              ["Coller un résumé", "Utiliser dernier résumé généré"], index=1)
        if hyp_choice == "Coller un résumé":
            hyp_text = st.text_area("Résumé à évaluer", height=140, key="hyp_free")
        else:
            hyp_text = st.session_state.get("last_summary", "")
            st.text_area("Résumé à évaluer (auto)", value=hyp_text, height=140, key="hyp_auto", disabled=True)

    if st.button("Calculer les scores", key="btn_eval"):
        if not ref_text or not hyp_text:
            st.warning("Il faut un résumé **référence** et un **résumé à évaluer**.")
        else:
            r1 = rouge_n(ref_text, hyp_text, 1)
            r2 = rouge_n(ref_text, hyp_text, 2)
            rl = rouge_l(ref_text, hyp_text)
            cos = tfidf_cosine(ref_text, hyp_text)
            comp = compression_ratio(src_text or ref_text, hyp_text)

            src_w = len(_tokens(src_text or ref_text))
            hyp_w = len(_tokens(hyp_text))
            t_src = reading_time_minutes(src_w)
            t_hyp = reading_time_minutes(hyp_w)
            gain = round(max(0, t_src - t_hyp), 2)

            m1, m2, m3 = st.columns(3)
            m1.metric("ROUGE-1 (recall)", f"{r1:.2f}")
            m2.metric("ROUGE-2 (recall)", f"{r2:.2f}")
            m3.metric("ROUGE-L (LCS)", f"{rl:.2f}")
            m4, m5, m6 = st.columns(3)
            m4.metric("Cosine TF-IDF", f"{cos:.2f}")
            m5.metric("Compression", f"{comp:.2f} (↓ meilleur)")
            m6.metric("Gain de temps estimé", f"{gain} min")

            st.write({
                "Mots source": src_w, "Mots résumé": hyp_w,
                "Lecture source (min)": t_src, "Lecture résumé (min)": t_hyp
            })

# ============================ Historique & métriques ==========================
st.markdown("## Historique et métriques")
total_docs = 0
avg_duration = 0
ratio_llm = 0.0

if LOG_PATH.exists():
    try:
        rows = LOG_PATH.read_text(encoding="utf-8-sig").splitlines()
    except UnicodeDecodeError:
        rows = LOG_PATH.read_text(encoding="utf-8").splitlines()

    if rows:
        header, *data = rows
        total_docs = len(data)

        if data:
            durations = []
            llm_count = 0
            table_rows = []
            for line in data:
                parts = line.split(",")
                if len(parts) >= 7:
                    ts, fname, method, length_lbl, lang_lbl, chars, dur = parts[:7]
                    try:
                        durations.append(int(dur))
                    except Exception:
                        pass
                    if method == "LLM":
                        llm_count += 1
                    table_rows.append({
                        "Date/heure": ts,
                        "Fichier": fname,
                        "Méthode": method,
                        "Longueur": length_lbl,
                        "Langue": lang_lbl,
                        "Caractères": int(chars) if chars.isdigit() else chars,
                        "Durée (ms)": int(dur) if dur.isdigit() else dur,
                    })
            avg_duration = int(sum(durations) / len(durations)) if durations else 0
            ratio_llm = (llm_count / len(data)) * 100 if data else 0.0

            col1, col2, col3 = st.columns(3)
            col1.metric("Documents traités", f"{total_docs}")
            col2.metric("Durée moyenne", f"{avg_duration} ms")
            col3.metric("Part IA (LLM)", f"{ratio_llm:.0f}%")

            st.write("Dernières exécutions (10 plus récentes)")
            st.dataframe(list(reversed(table_rows[-10:])), use_container_width=True)
        else:
            st.info("Aucun historique pour le moment. Générez un premier résumé.")
    else:
        st.info("Aucun historique pour le moment. Générez un premier résumé.")
else:
    st.info("Aucun historique pour le moment. Générez un premier résumé.")

st.markdown("---")
st.caption("POC : baseline locale + option IA via OpenAI. Étapes suivantes : OCR, multilingue avancé, métriques.")

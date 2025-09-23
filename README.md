# POC – Résumeur de documents (Streamlit)

Ce projet est une preuve de concept (**POC**) d’application Streamlit permettant de **résumer automatiquement des documents** au format **TXT, PDF et DOCX**.  
Deux modes de résumé sont proposés :

- **Résumé local (baseline)** : basé sur la fréquence des mots-clés (aucune IA, fonctionne hors ligne).
- **Résumé via OpenAI (IA)** : meilleure qualité, nécessite une clé API valide et un compte OpenAI avec crédits actifs.

---

## Installation

### 1. Cloner le dépôt
```bash
git clone https://github.com/ton-compte/poc-summarizer.git
cd poc-summarizer
```

### 2. Créer et activer un environnement virtuel
```bash
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
.venv\Scripts\activate      # Windows PowerShell
```

### 3. Installer les dépendances
```bash
pip install -r requirements.txt
```

---

## Configuration

Crée un fichier **.env** à la racine du projet et ajoute :

```env
# Exemple de configuration (.env)

# Clé API OpenAI (obligatoire pour le mode IA)
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxx

# Modèle utilisé (par défaut gpt-4o-mini)
OPENAI_MODEL=gpt-4o-mini

# (Optionnel) Chemin vers un certificat racine pour réseaux d’entreprise
# SSL_CERT_FILE=/chemin/vers/certificat.pem
```

- `OPENAI_API_KEY` : ta clé API OpenAI (nécessite un compte avec crédits).  
- `OPENAI_MODEL` : modèle utilisé (`gpt-4o-mini` par défaut).  
- `SSL_CERT_FILE` : uniquement si ton réseau d’entreprise impose un certificat.  
  → Sinon, tu peux cocher **Ignorer la vérif SSL** dans l’interface Streamlit (non recommandé en prod).  

---

## Lancer l’application
```bash
streamlit run app.py
```

L’application sera disponible sur :  
- Local : [http://localhost:8501](http://localhost:8501)  
- Réseau : adresse affichée dans la console  

---

## Structure du projet
```
poc-summarizer/
│── app.py              # Application Streamlit principale
│── requirements.txt    # Dépendances (versions figées)
│── README.md           # Documentation + exemple de .env
│── data/
│    └── logs.csv       # Historique des exécutions (auto-généré)
│── .venv/              # Environnement virtuel (à ignorer dans git)
│── .env                # Fichier contenant ta clé OpenAI (non versionné)
```

---

## Limitations

- **Quota API** : le mode IA dépend de ton abonnement/crédits OpenAI.  
  → Si le quota est épuisé, l’application bascule automatiquement en mode **local**.  
- **PDF scannés (images)** : non supportés (OCR non implémenté).  
- **Résumé local** : beaucoup plus basique qu’un résumé IA.  

---

## Prochaines améliorations possibles

- OCR pour les PDF scannés  
- Détection automatique de la langue source  
- Interface de comparaison **Résumé humain vs Résumé IA**  
- Déploiement (Streamlit Cloud, Heroku, Netlify Functions, etc.)  

---

## Auteur

Projet réalisé par **Mokhtar Charbelli**  
Designer UX/UI & Développeur Frontend — Spécialisé en accessibilité Web

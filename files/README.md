# 🩺 OmniCoord IA

Application de coordination pour les structures d'aide à domicile (SAAD / SSIAD) :
gestion du vivier interne, sourcing direct, matching IA besoins/compétences,
plannings, remplacements d'urgence, suivi de conformité des habilitations.

Projet indépendant d'OmniRecrut IA — base de données, dépôt GitHub, déploiement
Streamlit Cloud et clé API Gemini propres à ce projet.

## Fonctionnalités

1. **Vivier & Sourcing Direct** — gestion des intervenants internes et candidats,
   liens de sourcing externe direct (LinkedIn, CVthèques, réseaux) pour réduire
   la dépendance aux agences d'intérim.
2. **Matching IA** — croise les besoins d'un bénéficiaire (pathologies, gestes
   techniques, horaires) avec les compétences, habilitations et disponibilités
   des intervenants, en détectant aussi les compétences transférables issues de
   leur parcours.
3. **Portefeuille Bénéficiaires** — suivi des personnes aidées et de leur prise
   en charge.
4. **Documents & Transmissions** — assistant de rédaction de comptes-rendus et
   fiches de liaison (brouillons à relire, pas de valeur juridique).
5. **Plannings, Tournées & Urgences** — gestion des interventions et recherche
   rapide de remplaçants disponibles en cas d'absence.
6. **Conformité & Suivi** — alertes sur les habilitations et visites médicales
   arrivant à expiration.
7. **Administration & Paramètres** — gestion des accès, configuration de la
   boîte mail, état de la base de données.

## Installation locale

```bash
pip3 install -r requirements.txt
streamlit run app.py
```

## Configuration des secrets (Streamlit Cloud ou `.streamlit/secrets.toml` en local)

Voir `secrets_example.toml` pour le format attendu. Ne jamais committer le
fichier `secrets.toml` rempli.

## Sécurité

- Mots de passe utilisateurs hachés avec bcrypt (jamais stockés en clair).
- Aucun mot de passe ni clé API en dur dans le code source.
- Base de données SQLite exclue du dépôt Git (voir `.gitignore`).

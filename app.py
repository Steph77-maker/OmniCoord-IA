"""Point d'entrée OmniCoord IA."""
import streamlit as st
from omnicoord import core
from omnicoord import ui


def _apply_global_layout():
    """Stabilise le confort visuel d'OmniCoord à 100 % de zoom navigateur."""
    st.markdown(
        """
        <style>
        /* Zone principale : évite que l'interface se perde sur les grands écrans. */
        .stMainBlockContainer,
        div[data-testid="stMainBlockContainer"],
        section.main > div.block-container {
            width: min(100%, 1280px) !important;
            max-width: 1280px !important;
            padding-left: 2.25rem !important;
            padding-right: 2.25rem !important;
            padding-top: 2rem !important;
            margin-left: auto !important;
            margin-right: auto !important;
        }

        /* Sidebar confortable et stable. */
        section[data-testid="stSidebar"] {
            min-width: 282px !important;
            width: 282px !important;
        }

        section[data-testid="stSidebar"] > div:first-child {
            width: 282px !important;
        }

        /* Typographie générale : garde une taille lisible à 100 %. */
        div[data-testid="stAppViewContainer"] p,
        div[data-testid="stAppViewContainer"] label,
        div[data-testid="stAppViewContainer"] li,
        div[data-testid="stAppViewContainer"] .stMarkdown {
            font-size: 16px;
        }

        div[data-testid="stAppViewContainer"] h1 {
            font-size: clamp(2rem, 2.4vw, 2.7rem) !important;
            line-height: 1.15 !important;
        }

        div[data-testid="stAppViewContainer"] h2 {
            font-size: clamp(1.55rem, 1.9vw, 2rem) !important;
        }

        div[data-testid="stAppViewContainer"] h3 {
            font-size: clamp(1.2rem, 1.45vw, 1.5rem) !important;
        }

        /* Formulaires et boutons : ne deviennent pas minuscules sur grand écran. */
        div[data-testid="stAppViewContainer"] input,
        div[data-testid="stAppViewContainer"] textarea,
        div[data-testid="stAppViewContainer"] button,
        div[data-testid="stAppViewContainer"] [data-baseweb="select"] {
            font-size: 16px !important;
        }

        div[data-testid="stAppViewContainer"] input,
        div[data-testid="stAppViewContainer"] [data-baseweb="select"] > div {
            min-height: 42px;
        }

        /* Métriques plus lisibles. */
        div[data-testid="stMetricValue"] {
            font-size: clamp(1.9rem, 2.1vw, 2.45rem) !important;
        }

        div[data-testid="stMetricLabel"] {
            font-size: 0.95rem !important;
        }

        /* Les tableaux gardent une densité confortable. */
        div[data-testid="stDataFrame"] {
            font-size: 15px;
        }

        /* Login : largeur raisonnable au lieu d'un petit formulaire perdu au centre. */
        div[data-testid="stForm"] {
            min-width: 0;
        }

        /* Responsive : ne force pas les largeurs desktop sur petit écran. */
        @media (max-width: 900px) {
            .stMainBlockContainer,
            div[data-testid="stMainBlockContainer"],
            section.main > div.block-container {
                width: 100% !important;
                max-width: 100% !important;
                padding-left: 1rem !important;
                padding-right: 1rem !important;
            }

            section[data-testid="stSidebar"] {
                min-width: initial !important;
                width: initial !important;
            }

            section[data-testid="stSidebar"] > div:first-child {
                width: initial !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def main():
    _apply_global_layout()
    if not core.check_password():
        st.stop()
    ui.render()


if __name__ == "__main__":
    main()

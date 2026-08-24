"""Point d'entrée OmniCoord IA."""
import streamlit as st
from omnicoord import core
from omnicoord import ui

def main():
    if not core.check_password():
        st.stop()
    ui.render()

if __name__ == "__main__":
    main()

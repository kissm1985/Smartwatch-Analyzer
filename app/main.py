"""VitalMart Streamlit entry point:  uv run streamlit run app/main.py

The bundled .streamlit/config.toml binds to 127.0.0.1 and disables telemetry.
"""

from __future__ import annotations

import streamlit as st

from vitalmart.i18n import t
from vitalmart.ui.common import get_cfg, guard_localhost

st.set_page_config(page_title="VitalMart", page_icon="❤️", layout="wide")
get_cfg()
guard_localhost()

pages = [
    st.Page("pages/1_Attekintes.py", title=t("ui.nav.overview"), icon="🏠", default=True),
    st.Page("pages/2_Import.py", title=t("ui.nav.import"), icon="📥"),
    st.Page("pages/3_Riport.py", title=t("ui.nav.report"), icon="📄"),
    st.Page("pages/4_Elemzo.py", title=t("ui.nav.explorer"), icon="📊"),
    st.Page("pages/5_Adatminoseg.py", title=t("ui.nav.quality"), icon="🧪"),
    st.Page("pages/6_Export.py", title=t("ui.nav.export"), icon="💾"),
    st.Page("pages/7_Beallitasok.py", title=t("ui.nav.settings"), icon="⚙️"),
]
st.navigation(pages).run()

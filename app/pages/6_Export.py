"""Placeholder – implemented in milestone M7."""

from __future__ import annotations

import streamlit as st

from vitalmart.i18n import t
from vitalmart.ui.common import disclaimer, get_cfg

get_cfg()
st.title(t("ui.nav.export"))
st.info(t("ui.coming", milestone="M7"))
disclaimer()

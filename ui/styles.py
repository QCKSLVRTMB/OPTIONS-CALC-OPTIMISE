"""CSS-стили приложения. Вызываются из app.py после st.set_page_config."""
import streamlit as st


def inject_base_styles():
    """Основные стили: sticky tabs, формы, компактный лейаут."""
    st.markdown("""
<style>
.block-container {padding-top: 1rem; padding-bottom: 2rem;}
[data-testid="stForm"] {
    border: 1px solid #cfdfe9 !important;
    border-radius: 12px !important;
    padding: 14px 16px !important;
}
.pos-row-wrap [data-testid="stHorizontalBlock"] {
    border-bottom: 1px solid #e6edf4;
    padding: 2px 0;
}
.pos-row-wrap [data-testid="stHorizontalBlock"]:last-child {
    border-bottom: none;
}
div[data-testid="stTabs"] > div[data-baseweb="tab-list"] {
    position: sticky !important;
    top: 0 !important;
    z-index: 999 !important;
    background-color: #ffffff !important;
    border-bottom: 1px solid #e6edf4 !important;
    padding-top: 6px !important;
    padding-bottom: 4px !important;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
}
div[data-testid="stTabs"] > div[data-baseweb="tab-list"] button {
    background-color: transparent !important;
}
div[data-testid="stTabs"] > div[data-baseweb="tab-list"] button,
div[data-testid="stTabs"] > div[data-baseweb="tab-list"] button p,
div[data-testid="stTabs"] > div[data-baseweb="tab-list"] button div,
div[data-testid="stTabs"] > div[data-baseweb="tab-list"] button span {
    font-size: 1.05rem !important;
    font-weight: 600 !important;
    letter-spacing: .01em !important;
    line-height: 1.2 !important;
    padding-top: 6px !important;
    padding-bottom: 6px !important;
}
div[data-testid="stTabs"] > div[data-baseweb="tab-list"] {
    gap: 8px !important;
}
</style>
""", unsafe_allow_html=True)


def inject_responsive_styles():
    """Адаптивный CSS для вкладок внутри st.container(key='responsive_content')."""
    st.markdown("""
<style>
/* Изменения ограничены контентом вне доски опционов.
   Цвета текста и акцентные цвета не переопределяются. */

.st-key-responsive_content [data-testid="stForm"] {
    border-radius: 8px !important;
}

.st-key-responsive_content [data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: 8px !important;
}

.st-key-responsive_content [data-testid="stButton"] button,
.st-key-responsive_content [data-testid="stDownloadButton"] button {
    border-radius: 6px !important;
}

.st-key-responsive_content input,
.st-key-responsive_content textarea {
    font-variant-numeric: tabular-nums;
}

@media (max-width: 760px) {
    .st-key-responsive_content [data-testid="stHorizontalBlock"] {
        flex-wrap: wrap !important;
        gap: 0.75rem !important;
    }

    .st-key-responsive_content [data-testid="stColumn"] {
        flex: 1 1 16rem !important;
        width: auto !important;
        min-width: 0 !important;
    }

    .st-key-responsive_content [data-testid="stForm"] {
        padding: 10px !important;
    }

    .st-key-responsive_content input,
    .st-key-responsive_content textarea,
    .st-key-responsive_content [data-baseweb="select"] input {
        font-size: 16px !important;
    }

    .st-key-responsive_content [data-testid="stButton"] button,
    .st-key-responsive_content [data-testid="stDownloadButton"] button,
    .st-key-responsive_content [data-testid="stPopover"] button {
        min-height: 44px !important;
    }

    .st-key-responsive_content [data-testid="stRadio"] [role="radiogroup"] {
        flex-wrap: wrap !important;
        row-gap: 8px !important;
    }

    .st-key-responsive_content [data-testid="stMetricValue"] {
        font-size: 1.45rem !important;
    }

    .st-key-responsive_content h2 {
        font-size: 1.35rem !important;
    }

    .st-key-responsive_content h3 {
        font-size: 1.12rem !important;
    }
}
</style>
""", unsafe_allow_html=True)
"""UI блока «Резервная копия · JSON»."""
from datetime import datetime

import streamlit as st

from config import _IS_CLOUD
from core.backup import _export_full_cache, _import_full_cache


def _render_backup_controls():
    with st.expander("Резервная копия · JSON", expanded=False):
        message = st.session_state.pop("_backup_flash", None)
        if message:
            st.success(message)

        st.caption(
            "Позиции по всем активам, журнал, история IV/HV/RVI, "
            "выбор оповещений, уровни и настройки."
        )

        if _IS_CLOUD:
            st.caption(
                "На Cloud данные текущей сессии не гарантированно "
                "сохранятся после её завершения. Скачивайте резервную копию."
            )

        if st.button(
            "1. Подготовить актуальную копию",
            key="backup_prepare",
            use_container_width=True,
        ):
            try:
                st.session_state["_prepared_backup"] = (
                    _export_full_cache().encode("utf-8")
                )
                st.session_state["_prepared_backup_at"] = (
                    datetime.now().strftime("%d.%m.%Y %H:%M:%S")
                )
            except Exception as error:
                st.session_state.pop("_prepared_backup", None)
                st.error(f"Ошибка подготовки JSON: {error}")

        prepared = st.session_state.get("_prepared_backup")

        if prepared is not None:
            st.download_button(
                "2. Скачать JSON",
                data=prepared,
                file_name=(
                    f"moex_backup_"
                    f"{st.session_state['_prepared_backup_at']}"
                    .replace(":", "-")
                    .replace(" ", "_")
                    + ".json"
                ),
                mime="application/json",
                key="backup_download",
                use_container_width=True,
            )
            st.caption(
                "Снимок подготовлен: "
                + st.session_state["_prepared_backup_at"]
                + ". После изменения данных подготовьте его заново."
            )

        uploaded = st.file_uploader(
            "Восстановить резервную копию",
            type=["json"],
            key="backup_upload",
        )

        if uploaded is not None:
            st.caption(
                "Восстановление заменит соответствующие данные "
                "текущей сессии."
            )

            confirmed = st.checkbox(
                "Подтверждаю восстановление",
                key="backup_restore_confirm",
            )

            if st.button(
                "Восстановить из JSON",
                key="backup_restore",
                disabled=not confirmed,
                use_container_width=True,
            ):
                try:
                    text = uploaded.getvalue().decode("utf-8-sig")
                    ok, message = _import_full_cache(text)
                except UnicodeError:
                    ok, message = False, "Файл должен иметь кодировку UTF-8"

                if ok:
                    st.session_state["_backup_flash"] = message
                    st.rerun()
                else:
                    st.error(message)
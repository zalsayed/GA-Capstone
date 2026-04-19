"""
core/auth.py — Simple password-based authentication for Streamlit.
Enable with AUTH_ENABLED=true and AUTH_PASSWORD_HASH=<bcrypt hash> in .env
Generate hash: python -c "import bcrypt; print(bcrypt.hashpw(b'yourpassword', bcrypt.gensalt()).decode())"
"""

import streamlit as st
import hashlib
import os
from core.config import get_config


def _check_password(password: str, hashed: str) -> bool:
    try:
        import bcrypt
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ImportError:
        # Fallback: SHA-256 if bcrypt not installed
        return hashlib.sha256(password.encode()).hexdigest() == hashed


def require_auth() -> bool:
    """
    Show login wall if AUTH_ENABLED=true.
    Returns True if user is authenticated (or auth is disabled).
    """
    cfg = get_config()

    if not cfg.auth_enabled:
        return True

    if st.session_state.get("authenticated"):
        return True

    st.title("🔒 Req2Defect — Login Required")
    st.divider()

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.subheader("Sign In")
        password = st.text_input("Password", type="password", key="auth_password")

        if st.button("Login", use_container_width=True, type="primary"):
            if not cfg.auth_password_hash:
                st.error("Server misconfiguration: AUTH_PASSWORD_HASH not set.")
                return False

            if _check_password(password, cfg.auth_password_hash):
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Incorrect password.")

        st.caption("Contact your administrator for access.")

    return False


def logout():
    st.session_state.pop("authenticated", None)
    st.rerun()

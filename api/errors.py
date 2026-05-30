class ExtractError(Exception):
    """Помилка вилучення вмісту. str(exc) — короткий код (`no_content`, `blocked`,
    `timeout`, `not_html`, `request_error`); main.py мапить його у i18n-ключ `err_<code>`."""

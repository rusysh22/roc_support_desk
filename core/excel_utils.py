"""
Excel safety utilities shared across all apps.
"""

_FORMULA_PREFIXES = ('=', '+', '-', '@', '\t', '\r')


def safe_cell(value):
    """
    Prevent CSV/Excel formula injection.
    Any cell value starting with a formula prefix character is escaped
    by prepending a single quote so Excel treats it as a literal string.
    """
    if value is None:
        return ""
    s = str(value)
    if s and s[0] in _FORMULA_PREFIXES:
        return "'" + s
    return s

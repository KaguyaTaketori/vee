_NO_DECIMAL = {"JPY", "KRW", "VND"}

def amount_to_int(amount: float, currency: str) -> int:
    """Python float → 数据库 INTEGER"""
    if currency.upper() in _NO_DECIMAL:
        return round(amount)
    return round(amount * 100)

def int_to_amount(value: int, currency: str) -> float:
    """数据库 INTEGER → Python float"""
    if currency.upper() in _NO_DECIMAL:
        return float(value)
    return value / 100.0

def format_amount(value: int, currency: str) -> str:
    amount = int_to_amount(value, currency)
    if currency.upper() in _NO_DECIMAL:
        return f"{amount:.0f}"
    return f"{amount:.2f}"

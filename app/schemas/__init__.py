"""Pydantic domain schemas.

This package contains the application-level data models. They are the
"domain" representation — money is kept as :class:`decimal.Decimal`,
dates as :class:`datetime.date` — independent of wire formats used by
external systems (LLMs, KSeF XML, REST payloads).
"""

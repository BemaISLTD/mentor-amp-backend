"""Column types that behave identically on PostgreSQL and also work on SQLite (used by tests).

On PostgreSQL these are exactly ARRAY(VARCHAR) and BIGINT. SQLite has no ARRAY type and only
auto-numbers INTEGER primary keys, so the SQLite variants make in-memory end-to-end tests possible.
"""

from sqlalchemy import JSON, BigInteger, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY

StringArray = ARRAY(String).with_variant(JSON(), "sqlite")
BigIntegerPK = BigInteger().with_variant(Integer(), "sqlite")

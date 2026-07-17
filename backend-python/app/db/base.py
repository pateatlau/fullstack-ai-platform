"""SQLAlchemy declarative base and shared metadata conventions.

A single ``Base`` owns all model metadata for the MVP persistence layer.
The naming convention makes constraint/index names deterministic so Alembic
autogenerate produces stable, review-friendly migrations.
"""

from sqlalchemy import MetaData, Text
from sqlalchemy.orm import DeclarativeBase

# Deterministic naming for constraints and indexes (helps Alembic autogenerate).
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base shared by every persistence model."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    # Plan Section 2.1: string columns are PostgreSQL TEXT, not length-bound VARCHAR.
    type_annotation_map = {str: Text}

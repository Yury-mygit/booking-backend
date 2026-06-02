# Ensure all model modules are loaded on package import so that
# Base.metadata sees every table. Critical for alembic autogenerate
# and target_metadata coverage.

from app.models import models  # noqa: F401  -- legacy domain
from app.models import support  # noqa: F401  -- ticketing domain

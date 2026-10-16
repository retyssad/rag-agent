from alembic import context
from sqlalchemy import create_engine
from app.config import get_settings
from app.db import Base

config = context.config
url = get_settings().database_url
target_metadata = Base.metadata
if context.is_offline_mode():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    from app.db import Database
    database = Database(url)
    with database.engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()

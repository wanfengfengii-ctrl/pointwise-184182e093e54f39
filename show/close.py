from show.session import mark_closed
from show.store import control_transaction


def close_show(path, show):
    with control_transaction(path) as db:
        return mark_closed(db, show)

import json

from art.models import Side
from show.rollout import RolloutPending
from show.session import load_session, mark_closed
from show.store import control_transaction
from show.transport import PanelUnavailable


def close_show(path, show, read_step=None):
    with control_transaction(path) as db:
        session = load_session(db, show)
        row = db.execute('SELECT ticket FROM rollouts WHERE show_id = ?', (session.show,)).fetchone()
        if row is not None:
            ticket = json.loads(row['ticket'])
            if read_step is None:
                raise RolloutPending('rollout completion must be verified at the projection panels')
            for side in Side:
                last_step = len(ticket[side.value])
                try:
                    status, data = read_step(side.value, ticket['rollout'], last_step)
                except PanelUnavailable as exc:
                    raise RolloutPending(f'{side} panel cannot confirm the final rollout step') from exc
                if status != 200 or not isinstance(data, dict):
                    raise RolloutPending(f'{side} side has not finished its accepted rollout list')
                if (data.get('rollout') != ticket['rollout'] or data.get('side') != side.value
                        or data.get('step') != last_step):
                    raise RolloutPending(f'{side} final rollout step does not match the acceptance ticket')
        return mark_closed(db, session.show)

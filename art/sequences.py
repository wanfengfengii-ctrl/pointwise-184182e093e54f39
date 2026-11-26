from art.catalog import load_frame
from art.models import Side


class InvalidSequence(ValueError):
    code = 'invalid_sequence'

    def __init__(self, message, side, frame=None):
        super().__init__(message)
        self.details = {'side': side}
        if frame is not None:
            self.details['frame'] = frame


def resolve_side_sequence(db, side, frames):
    side = Side(side)
    if not frames:
        raise InvalidSequence('the side playlist is empty', side)
    resolved = []
    for frame_id in frames:
        try:
            frame = load_frame(db, frame_id)
        except KeyError as exc:
            raise InvalidSequence('playlist references an unknown frame', side, frame_id) from exc
        if frame.side != side:
            raise InvalidSequence('frame belongs to the other side', side, frame_id)
        resolved.append(frame)
    return tuple(resolved)

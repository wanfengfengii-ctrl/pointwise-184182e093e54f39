import re
from dataclasses import dataclass
from enum import StrEnum


class Side(StrEnum):
    LEFT = 'left'
    RIGHT = 'right'


class Conflict(ValueError):
    code = 'conflict'
    status = 409


def identifier(value):
    if not isinstance(value, str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', value) is None:
        raise ValueError('expected an identifier starting with an ASCII letter or digit')
    return value


@dataclass(frozen=True)
class Frame:
    frame: str
    side: Side
    content: str
    measurements: tuple[tuple[str, int], ...]

    def payload(self):
        return {
            'frame': self.frame,
            'side': self.side,
            'content': self.content,
            'measurements': dict(self.measurements),
        }

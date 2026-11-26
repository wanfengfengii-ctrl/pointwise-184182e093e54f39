from dataclasses import dataclass

from art.models import Side


@dataclass(frozen=True)
class MeterWindow:
    low: int
    high: int


@dataclass(frozen=True)
class SeamReading:
    left: int
    right: int
    total: int
    low: int
    high: int


class SeamError(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = details


def normalize_windows(raw):
    if not isinstance(raw, dict):
        raise SeamError('invalid_window', 'windows must be keyed by point name')
    windows = {}
    for point, bounds in raw.items():
        if not isinstance(point, str) or not point.strip():
            raise SeamError('invalid_window', 'expected a nonempty point name')
        if (not isinstance(bounds, (list, tuple)) or len(bounds) != 2
                or any(type(value) is not int for value in bounds) or bounds[0] > bounds[1]):
            raise SeamError('invalid_window', 'expected an ordered integer closed interval', point=point)
        windows[point] = MeterWindow(*bounds)
    return dict(sorted(windows.items()))


def _constant_vector(side, frames, points):
    reference = None
    for frame in frames:
        values = dict(frame.measurements)
        missing = sorted(points - values.keys())
        if missing:
            raise SeamError('missing_meter', 'frame is missing a required point',
                            point=missing[0], side=side, frame=frame.frame)
        excess = sorted(values.keys() - points)
        if excess:
            raise SeamError('unexpected_meter', 'frame contains an unlisted point',
                            point=excess[0], side=side, frame=frame.frame)
        if reference is None:
            reference = values
            continue
        for point in sorted(points):
            if values[point] != reference[point]:
                raise SeamError('side_vector_mismatch', 'frame changes the side contribution',
                                point=point, side=side, frame=frame.frame)
    return reference


def prove_seam(left_frames, right_frames, windows):
    if not windows:
        raise SeamError('invalid_window', 'seam proof needs at least one permitted point')
    if not left_frames or not right_frames:
        raise SeamError('invalid_sequence', 'seam proof needs both side playlists')
    points = set(windows)
    left = _constant_vector(Side.LEFT, left_frames, points)
    right = _constant_vector(Side.RIGHT, right_frames, points)
    readings = {}
    for point, window in sorted(windows.items()):
        total = left[point] + right[point]
        if not window.low <= total <= window.high:
            raise SeamError('seam_out_of_range', 'combined brightness is outside the closed interval',
                            point=point, total=total, low=window.low, high=window.high)
        readings[point] = SeamReading(left[point], right[point], total, window.low, window.high)
    return readings

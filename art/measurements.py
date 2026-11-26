class InvalidMeasurements(ValueError):
    code = 'invalid_measurements'

    def __init__(self, message, point=None):
        super().__init__(message)
        self.details = {'point': point} if point is not None else {}


def normalize_measurements(raw):
    if isinstance(raw, dict):
        entries = raw.items()
    elif isinstance(raw, (list, tuple)):
        entries = raw
    else:
        raise InvalidMeasurements('measurements must be a point map or a list of pairs')

    by_name = {}
    for entry in entries:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise InvalidMeasurements('expected a point name and a brightness reading')
        point, reading = entry
        if not isinstance(point, str) or not point.strip():
            raise InvalidMeasurements('expected a nonempty point name')
        if point in by_name:
            raise InvalidMeasurements('duplicate point name', point)
        if type(reading) is not int or reading < 0:
            raise InvalidMeasurements('brightness must be a nonnegative integer', point)
        by_name[point] = reading
    return tuple(sorted(by_name.items()))

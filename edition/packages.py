import json
from dataclasses import asdict, dataclass

from art.models import Conflict, Side, identifier
from edition.seam import SeamReading


@dataclass(frozen=True)
class ReleasePackage:
    release: str
    draft: str
    playlists: dict[Side, tuple[str, ...]]
    meters: dict[str, SeamReading]

    def payload(self):
        return asdict(self)


@dataclass(frozen=True)
class SidePackage:
    release: str
    side: Side
    frames: tuple[str, ...]
    meters: dict[str, int]


def initialize_releases(db):
    db.execute('CREATE TABLE IF NOT EXISTS releases (release_id TEXT PRIMARY KEY, payload TEXT NOT NULL)')


def save_release(db, package: ReleasePackage):
    inserted = db.execute('INSERT INTO releases VALUES (?, ?) ON CONFLICT(release_id) DO NOTHING',
                          (package.release, json.dumps(package.payload(), ensure_ascii=False)))
    if inserted.rowcount == 1:
        return package
    original = load_release(db, package.release)
    if original != package:
        raise Conflict('release number already names another fixed edition')
    return original


def load_release(db, release):
    row = db.execute('SELECT payload FROM releases WHERE release_id = ?', (identifier(release),)).fetchone()
    if row is None:
        raise KeyError(f'release {release} is unknown')
    payload = json.loads(row['payload'])
    return ReleasePackage(payload['release'], payload['draft'],
                          {Side(side): tuple(frames) for side, frames in payload['playlists'].items()},
                          {point: SeamReading(**reading) for point, reading in payload['meters'].items()})


def package_for_side(package: ReleasePackage, side):
    side = Side(side)
    meters = {}
    for point, reading in package.meters.items():
        meters[point] = reading.left if side == Side.LEFT else reading.right
    return SidePackage(package.release, side, package.playlists[side], meters)

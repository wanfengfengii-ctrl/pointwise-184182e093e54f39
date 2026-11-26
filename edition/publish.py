from art.models import Side, identifier
from edition.drafts import load_draft, load_settings
from edition.packages import ReleasePackage, save_release
from edition.proof import evaluate_proof
from show.store import control_transaction


def publish_edition(path, release, draft=None):
    release = identifier(release)
    with control_transaction(path) as db:
        if draft is None:
            draft = load_settings(db)['default_draft']
        proof = evaluate_proof(db, load_draft(db, draft))
        playlists = {
            Side.LEFT: tuple(frame.frame for frame in proof.left),
            Side.RIGHT: tuple(frame.frame for frame in proof.right),
        }
        return save_release(db, ReleasePackage(release, draft, playlists, proof.meters))

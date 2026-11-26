from dataclasses import dataclass

from art.models import Frame, Side
from art.sequences import resolve_side_sequence
from edition.drafts import ProofDraft
from edition.seam import SeamReading, prove_seam


@dataclass(frozen=True)
class EditionProof:
    left: tuple[Frame, ...]
    right: tuple[Frame, ...]
    meters: dict[str, SeamReading]


def evaluate_proof(db, draft: ProofDraft):
    left = resolve_side_sequence(db, Side.LEFT, draft.left)
    right = resolve_side_sequence(db, Side.RIGHT, draft.right)
    return EditionProof(left, right, prove_seam(left, right, draft.windows))

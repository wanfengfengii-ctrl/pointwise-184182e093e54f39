from dataclasses import dataclass

from edition.packages import SidePackage
from panel.receipts import DisplayReceipt


@dataclass(frozen=True)
class SideCoverage:
    played: tuple[DisplayReceipt, ...]
    missing: tuple[str, ...]


def compute_side_coverage(package: SidePackage, receipts):
    played = tuple(sorted(receipts, key=lambda receipt: receipt.sequence))
    displayed = {receipt.frame for receipt in played}
    if displayed - set(package.frames):
        raise ValueError('official display log contains a frame outside the side playlist')
    missing = tuple(frame for frame in package.frames if frame not in displayed)
    return SideCoverage(played, missing)

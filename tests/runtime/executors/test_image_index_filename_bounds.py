"""Camera-name recognition preserves its language without exponential work."""

from itertools import product
from pathlib import Path
import re
import subprocess
import sys

import pytest

from test_image_index_build_phases import builder


@pytest.mark.parametrize(("index", "previous", "prefixes", "alphabet"), [
    (1, r"^IMG[_-]?\d+(?:[_-]?[A-Z]*\d*)*$", ("IMG", "IMG_", "IMG-"), "1A_-."),
    (6, r"^\d{4}[-_]?\d{2}[-_]?\d{2}([-_]?\d+)*$",
     ("20260101", "2026_01_01", "2026-0101"), "1_-."),
])
def test_camera_name_simplification_preserves_short_language(index, previous, prefixes, alphabet):
    old = re.compile(previous, re.I)
    new = builder._AUTO_FILENAME_PATTERNS[index]
    for prefix in prefixes:
        for length in range(6):
            for suffix in product(alphabet, repeat=length):
                value = prefix + "".join(suffix)
                assert bool(old.fullmatch(value)) == bool(new.fullmatch(value)), value
    for value in ("IMG１٢3", "IMG_١__A-", "20260101١_٢", "20260101__1", "20260101_"):
        assert bool(old.fullmatch(value)) == bool(new.fullmatch(value)), value


def test_pathological_camera_names_finish_in_a_bounded_child():
    # A regression must fail this test, not hang the pytest interpreter while
    # holding the regex GIL. subprocess.run kills and waits for this exact
    # child on timeout; it creates no grandchildren or live service calls.
    code = (
        "import sys; "
        f"sys.path.insert(0, {str(Path(builder.__file__).parent)!r}); "
        "import create_images_indices as b; "
        "assert b._meaningful_filename_tokens('IMG'+'1'*240+'.'); "
        "assert b._meaningful_filename_tokens('20260101'+'1'*230+'.'); "
        "print('bounded')"
    )
    outcome = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=3, check=True,
    )
    assert outcome.stdout.strip() == "bounded"

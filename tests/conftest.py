import pytest

from animal_pipeline.media import h264_encoder


@pytest.fixture(scope="session")
def h264_enc() -> str:
    """A usable H.264 encoder for fixture clips — videotoolbox on Apple
    Silicon, hardware or libx264 elsewhere."""
    return h264_encoder()

"""Generate public, synthetic wxgf/HEVC bytes; no account data is used."""
import io
from pathlib import Path

import av

buffer = io.BytesIO()
with av.open(buffer, mode="w", format="hevc") as container:
    stream = container.add_stream("libx265", rate=1)
    stream.width = stream.height = 64
    stream.pix_fmt = "yuv420p"
    stream.options = {"preset": "ultrafast", "x265-params": "log-level=error"}
    frame = av.VideoFrame(64, 64, "rgb24")
    frame.planes[0].update(bytes([27, 122, 65]) * 64 * 64)
    for packet in stream.encode(frame):
        container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
target = Path(__file__).resolve().parents[1] / "tests/fixtures/public-image.wxgf"
target.write_bytes(b"wxgf" + bytes(12) + buffer.getvalue())
print("GENERATED", target.name, target.stat().st_size)

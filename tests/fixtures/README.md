# Public media fixture

`public-image.wxgf` is a synthetic 64×64 green frame encoded with HEVC and prefixed with a wxgf marker. It contains no account data. Regenerate it with `python scripts/generate_hevc_fixture.py` using the pinned PyAV dependency. Tests wrap these bytes in the WeChat AES-V2 format and validate JPEG dimensions and colors after conversion.

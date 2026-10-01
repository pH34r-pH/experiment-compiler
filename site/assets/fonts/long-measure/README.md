# Long measure title font

Nimbus Sans Narrow Regular, copyright (URW)++, Copyright 2014 by
(URW)++ Design & Development. Distributed under AGPL-3.0 with the font
exception in LICENSE; the complete license is in COPYING.

Upstream: <https://github.com/ArtifexSoftware/urw-base35-fonts>

The original OpenType and Type 1 font programs are included alongside the
web font. On 2026-09-22, Portfolio converted the OpenType file to WOFF2 using
FontTools without changing the glyphs, metrics, or character coverage.

To rebuild the web format:

```sh
python -m pip install 'fonttools[woff]'
python -m fontTools.ttLib.woff2 compress NimbusSansNarrow-Regular.otf
```

Only the WOFF2 file is requested by the site. The original formats and license
files remain available here as source and redistribution notices.

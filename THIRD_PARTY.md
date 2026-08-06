# Third-party components

The front end has no build step, so its JavaScript dependencies are vendored into
`web/vendor/` and served directly by the local Python server. That keeps the app
working offline and makes a packaged build self-contained.

Every vendored file keeps its original copyright banner. This page records what's
bundled, at which version, and under what terms.

| Component | Version | License | Upstream |
|---|---|---|---|
| three.js (`three.module.js`, `three.core.js`) | r180 | MIT | https://github.com/mrdoob/three.js |
| three.js examples: `OrbitControls`, `GLTFLoader`, `RoomEnvironment`, `BufferGeometryUtils` | r180 | MIT | https://github.com/mrdoob/three.js |
| uPlot (`uPlot.esm.js`, `uPlot.min.css`) | 1.6.31 | MIT | https://github.com/leeoniya/uPlot |

## License texts

three.js is Copyright (c) 2010-2025 three.js authors.
uPlot is Copyright (c) 2024 Leon Sorokin.

Both are distributed under the MIT License:

> Permission is hereby granted, free of charge, to any person obtaining a copy of
> this software and associated documentation files (the "Software"), to deal in
> the Software without restriction, including without limitation the rights to
> use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
> of the Software, and to permit persons to whom the Software is furnished to do
> so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

## Build time only

pybind11 (BSD-3-Clause) is fetched by CMake when building the `motorsim_py`
extension. It isn't vendored or redistributed here.

## Generated assets

`web/assets/motor.glb` is produced by this repository's own `tools/build_motor.py`.
See `web/assets/ATTRIBUTION.md`. No third-party model is bundled.

# Local three.js (ESM)

Vendor the exact files (no CDN):

- `three.module.js`
- `examples/jsm/controls/OrbitControls.js`

## Option A (npm → copy files)
```powershell
npm init -y
npm i three@0.168.0
copy node_modules\three\build\three.module.js apps\puzzle_ui\viewer\libs\three\
copy node_modules\three\examples\jsm\controls\OrbitControls.js apps\puzzle_ui\viewer\libs\three\examples\jsm\controls\
```

## Option B (PowerShell download)
```powershell
Invoke-WebRequest https://unpkg.com/three@0.168.0/build/three.module.js -OutFile apps\puzzle_ui\viewer\libs\three\three.module.js
Invoke-WebRequest https://unpkg.com/three@0.168.0/examples/jsm/controls/OrbitControls.js -OutFile apps\puzzle_ui\viewer\libs\three\examples\jsm\controls\OrbitControls.js
```

Commit these files (they are small). No import maps.

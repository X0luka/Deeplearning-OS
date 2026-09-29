# Third-party notices

The root MIT license applies to original project code, not to third-party libraries,
fonts, source snapshots or model assets. Existing third-party notices must be retained.

This candidate is a developer source distribution. Its checked asset and dependency
inventory is recorded below and in the linked notices. No personal input documents,
papers, installed Python/npm environments or model weights are bundled.
A future binary installer needs a separate inventory of its actual bundled contents.
License metadata and retained notices are not a legal certification.

The development Windows test package includes Microsoft.Web.WebView2 SDK 1.0.3537.50 files from the official NuGet package, with its LICENSE.txt preserved as LICENSE-WebView2.txt. These files are not covered by the project's MIT license. This does not install or redistribute the full WebView2 Runtime. These local test binaries are not part of the source archive.

On 2026-09-29 the Workbench's bundled KaTeX 0.16.22, Marked 15.0.12,
DOMPurify 3.4.15 and 60 KaTeX fonts were byte-verified against the locked
npm packages. License texts and provenance are retained in
components/AgentOS-K/aos/host/static/assistant-vendor.
PaperReader's vendor build now preserves these package licenses too.
This verification covers those assets; the remaining source-distribution inventory follows below.

CPU PDF dependency metadata is inventoried in docs/DEPENDENCY_INVENTORY.json, with exact versions/hashes in requirements/pdf-cpu-py312-linux.txt. Model revisions, sizes and hashes are in assets/pdf-models.lock.json and assets/pdf-ocr.lock.json. The locally downloaded layout model uses Apache-2.0; Docling model/CodeFormula assets use CDLA-Permissive-2.0 as recorded in their upstream notices. Model weights are NOT bundled in source review archives. These metadata records do not substitute for review of the final distributed binaries and all required notices.

## Developer source distribution inventory (2026-09-30)

The developer source archive excludes virtual environments, npm caches, model weights,
SDK binaries, personal inputs and runtime databases. Dependency downloads remain subject
to their own notices; the root MIT license does not relicense them.

- Workbench browser assets: locked KaTeX 0.16.22, Marked 15.0.12 and DOMPurify 3.4.15;
  full notices are retained beside the assets under assistant-vendor.
- Wiki browser assets: React 19.3.0, React DOM 19.3.0, XYFlow React 12.11.6,
  html-to-image 1.11.13 and elkjs 0.12.0, plus their bundled dependencies.
  Full package notices are in components/GitHub-Wiki/frontend/THIRD_PARTY_NOTICES.md.
  elkjs declares EPL-2.0 OR GPL-3.0-or-later; the included EPL text remains applicable.
  Upstream source repository: https://github.com/kieler/elkjs (version 0.12.0).
  The build only normalizes trailing whitespace in worker comment lines.
- Wiki Cytoscape has its license beside the vendored file.
- Reader assets are produced from its npm lock at build time, with PDF.js, KaTeX,
  Marked and DOMPurify license files copied by scripts/vendor.mjs.
- The Windows staging helper copies the verified WebView2 SDK license with the SDK;
  it does not bundle or install the full Runtime.

This inventory describes what was checked for the developer source distribution.
A future binary installer must review its own actual contents separately.

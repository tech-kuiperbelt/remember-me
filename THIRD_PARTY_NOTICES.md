# Third-party notices

Remember Me's own source code is licensed under [Apache-2.0](LICENSE), with
project attribution in [NOTICE](NOTICE). Third-party materials retain their own
licenses. This index does not replace the complete texts linked below.

## Code distributed in this repository

| Component | Distributed file / version | Official source | License text |
|---|---|---|---|
| Marked | web/static/vendor/marked.js, 18.1.0 | [npm archive](https://registry.npmjs.org/marked/-/marked-18.1.0.tgz), package/lib/marked.esm.js | MIT; [complete upstream text](web/static/vendor/marked-LICENSE.txt), including the inherited Markdown / John Gruber notice |
| DOMPurify | web/static/vendor/purify.js, 3.4.16 | [npm archive](https://registry.npmjs.org/dompurify/-/dompurify-3.4.16.tgz), package/dist/purify.es.mjs | Upstream offers MPL-2.0 OR Apache-2.0; this distribution uses [Apache-2.0](web/static/vendor/purify-LICENSE.txt) for DOMPurify itself |
| Babel helpers / regenerator notice | Helpers embedded in the DOMPurify build; exact Babel build version not established | [License linked by the bundled code](https://github.com/babel/babel/blob/main/packages/babel-helpers/LICENSE) | MIT; [full text](web/static/vendor/babel-helpers-LICENSE.txt), including Sebastian McKenzie and contributors and the scoped Facebook regenerator notice |

The JS files are byte-identical to the official npm release files, renamed for
local serving. Copyright headers remain intact. Marked and DOMPurify license
files also match the archives byte for byte. Neither archive contains a separate
NOTICE file. Babel's MIT terms supplement DOMPurify's license.
See [verification details](web/static/vendor/README.md).

## Dependencies installed separately

Python packages listed in [requirements.txt](requirements.txt),
[requirements-fastembed.txt](requirements-fastembed.txt) and
[requirements-sentence-transformers.txt](requirements-sentence-transformers.txt)
are obtained by the installer; their wheels and site-packages are not bundled
in this source repository. They retain their own licenses. Upstream projects
include [MCP](https://github.com/modelcontextprotocol/python-sdk),
[Requests](https://github.com/psf/requests), [NumPy](https://github.com/numpy/numpy),
[FastEmbed](https://github.com/qdrant/fastembed) and
[Sentence Transformers](https://github.com/huggingface/sentence-transformers).

Redistributing an environment or container requires review of that exact
artifact: license texts, NOTICE files, native libraries and applicable
source-provision obligations. This source-only index is not clearance for
distributing Python wheels, OS packages or containers.

## Models in Python installations and Docker images

Model weights, tokenizer files and model caches are not distributed in this
source repository. The Docker image includes the pinned BGE-M3 dense runtime
files described below. Remember Me's license does not relicense these materials.

| Provider | Default model / source | Evidence and limits |
|---|---|---|
| Ollama | [bge-m3](https://ollama.com/library/bge-m3); an optional external service, not included in the Docker image | Check the selected artifact's license; no specific digest is cleared for redistribution here |
| Sentence Transformers | [BAAI/bge-m3](https://huggingface.co/BAAI/bge-m3) | Docker bundles dense runtime files at revision `5617a9f61b028005a4858fdac845db406aefb181`; the [pinned model card](https://huggingface.co/BAAI/bge-m3/blob/5617a9f61b028005a4858fdac845db406aefb181/README.md) declares MIT. See the [11-file source/digest manifest](docker/bge-m3-manifest.json), [attribution](docker/licenses/BGE-M3-NOTICE.txt) and [full MIT text](docker/licenses/BGE-M3-MIT.txt) retained under `/opt/remember-me/models/` in the image |
| FastEmbed | intfloat/multilingual-e5-large; FastEmbed 0.8.0 maps to [qdrant/multilingual-e5-large-onnx](https://huggingface.co/qdrant/multilingual-e5-large-onnx), model.onnx and model.onnx_data, with a Qdrant-hosted archive alternative | Installed registry declares MIT; exact converted artifacts/revisions have not been independently cleared. The original model license alone does not establish every conversion's terms |

In Python installations, local providers try the cache first, downloading on a
cache miss only when configuration permits. The official Docker image uses its
bundled BGE-M3 without runtime downloads. For local providers, explicit bin/manage embedding download-model permits
missing-model downloads. Other selected
models and provider versions may use different sources and licenses. Bundling
additional models or artifacts in a release or image requires a separate review.

## Design assets and quotations

On 2026-10-16 the maintainer confirmed independent development with AI assistance,
no employer-owned or third-party-owned materials presented as project-owned
work, and that the queried logo, Help backgrounds, design references and icons
were AI-generated rather than taken from an icon library. This is a maintainer
provenance statement, not a claim that purely AI-generated material necessarily
receives the same copyright protection as human-authored work. Tool versions and
generation records have not been independently verified. Project screenshots
and architecture diagrams document the project itself.

The Help page quotes William Shakespeare's *The Tempest*, Act II, Scene I:
“What's past is prologue.” Shakespeare's original is in the public domain. The
Chinese wording “过去的一切，皆为序章。” is the project's selected expression
and was not copied from a modern published translation. The attribution is
included for identification; the quotation is not represented as project-owned
text licensed under Apache-2.0.

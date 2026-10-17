# Markdown dependencies

Official npm releases, verified against SHA-512 integrity on 2026-10-08. No runtime CDN. DOMPurify uses a per-render allowlist without IN_PLACE, hooks or global configuration. `.mjs` is renamed `.js` for the existing static route. See adjacent upstream licenses.

- marked 18.1.0: https://registry.npmjs.org/marked/-/marked-18.1.0.tgz
  npm integrity: `sha512-PamYXWWWg2nboG3oX5Ffzpy3EFZROqZJK9hSlMmUnekp7TxPs5DcT95vwd46m73/exfKCRVMtgXoPMI4FEJL6w==`
  local SHA-256: `054d73b676031dc8e60e247ef228044fbeebe10af14bac1a6e53852f80f826e5`

- dompurify 3.4.16: https://registry.npmjs.org/dompurify/-/dompurify-3.4.16.tgz
  npm integrity: `sha512-sqo+pNp3qRhCIpbgRi1y8Tgk27Bo2Ry7w0dC1NBeNTdZChWjz9Xb/KOoZbRP/R6pQZ80Qw8YhXw13hWWBbMRnQ==`
  local SHA-256: `c44274a7959cfdd4da871fa78a5d5fbbef55db68d118c5c0833bc4b5cf9633ad`

Official safety references checked during implementation: [Marked advisories](https://github.com/markedjs/marked/security/advisories), [DOMPurify advisories](https://github.com/cure53/DOMPurify/security/advisories). Markdown rendering uses a new fragment, never IN_PLACE or untrusted hooks; all raw HTML is escaped before the allowlist sanitizer.

## Source-distribution license verification (2026-10-16)

Both official npm archives were fetched again and their SHA-512 integrity
matched the values above. Local JS files match package/lib/marked.esm.js and
package/dist/purify.es.mjs byte for byte; adjacent licenses match package/LICENSE.
Neither archive contains a separate NOTICE file.

DOMPurify's package declares (MPL-2.0 OR Apache-2.0); we use the Apache-2.0 option.
Its bundled helpers include a Facebook/regenerator MIT header at purify.js:37,
but neither that JS file nor the existing license contains the complete Babel
MIT terms. The missing text is now supplied in
[babel-helpers-LICENSE.txt](babel-helpers-LICENSE.txt), copied unchanged from the
[upstream URL identified by the header](https://raw.githubusercontent.com/babel/babel/main/packages/babel-helpers/LICENSE).
It preserves both Sebastian McKenzie/contributors and the scoped Facebook
regenerator copyright notices. The exact Babel build version is not established;
no runtime JS was modified or upgraded. This resolves the earlier disagreement:
a copyright/URL header was present, but the full corresponding MIT text was not.

See the repository [third-party index](../../../THIRD_PARTY_NOTICES.md).

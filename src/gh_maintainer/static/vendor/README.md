# Vendored Markdown libraries

These browser distributions are served locally; the dashboard needs no CDN or Node.js at runtime.

| Library | Version | Source | License |
| --- | --- | --- | --- |
| Marked | 18.1.1 | https://github.com/markedjs/marked | MIT (`marked.LICENSE.md`) |
| DOMPurify | 3.4.17 | https://github.com/cure53/DOMPurify | Apache-2.0 OR MPL-2.0 (`dompurify.LICENSE`) |

Files are copied unchanged from the corresponding npm release (`marked/lib/marked.umd.js`,
`dompurify/dist/purify.min.js`). Keep versions and licenses together when upgrading, and
run the browser Markdown sanitization checks after updating either library.

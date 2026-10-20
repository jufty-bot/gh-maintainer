# Vendored browser libraries

These browser distributions are served locally; the dashboard needs no CDN or Node.js at runtime.

| Library | Version | Source | License |
| --- | --- | --- | --- |
| Marked | 18.1.1 | https://github.com/markedjs/marked | MIT (`marked.LICENSE.md`) |
| diff2html | 3.4.56 | https://github.com/rtfpessoa/diff2html | MIT (`diff2html.LICENSE.md`); bundled jsdiff BSD-3-Clause (`jsdiff.LICENSE`) and Hogan Apache-2.0 (`hogan.LICENSE`) |
| DOMPurify | 3.4.17 | https://github.com/cure53/DOMPurify | Apache-2.0 OR MPL-2.0 (`dompurify.LICENSE`) |

Files are copied unchanged from the corresponding npm release (`marked/lib/marked.umd.js`,
`dompurify/dist/purify.min.js`). Keep versions and licenses together when upgrading, and
run the browser Markdown sanitization checks after updating either library.

The diff viewer uses `diff2html/bundles/js/diff2html-ui-base.min.js` and
`diff2html/bundles/css/diff2html.min.css`, copied unchanged from npm release 3.4.56.
This base bundle includes parsing, line matching and synchronized scrolling, without a
syntax-highlighting bundle. Its generated HTML passes through DOMPurify before insertion.
Rerun the PR code-view browser checks after upgrading; retain bundled dependency licenses.

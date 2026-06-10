# swe-forge — static landing page

A zero-build static site that introduces `swe-forge` in the style of modern
AI-product landing pages (inspired by [mirix.io](https://mirix.io/)).

## Files

- `index.html` — markup
- `styles.css` — dark-themed responsive styles (no framework)
- `script.js` — copy-to-clipboard + reveal-on-scroll (no dependencies)

## View it locally

Any static file server works. With Python:

```bash
python -m http.server --directory docs/site 8080
# then open http://localhost:8080
```

Or just open `docs/site/index.html` directly in a browser.

## Deploy on GitHub Pages

GitHub Pages serves `index.html` at the root of the folder you select, so
choose **one** of these options:

- **Option A (recommended): use a custom GitHub Action** (e.g.
  `actions/upload-pages-artifact`) to publish only `docs/site/` as the
  Pages artifact. The site will be served at the repository's Pages URL.
- **Option B: move the files.** Copy the contents of `docs/site/` into
  `/docs` (so `docs/index.html` exists) and in **Settings → Pages** set
  the source to the `main` branch with the `/docs` folder.

No build step. No dependencies. Pure HTML/CSS/JS.

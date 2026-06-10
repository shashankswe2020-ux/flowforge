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

In **Settings → Pages**, set the source to the `main` branch and the
`/docs` folder, then point Pages at `docs/site/index.html` (or move the
files to `/docs` directly if you prefer the default layout).

No build step. No dependencies. Pure HTML/CSS/JS.

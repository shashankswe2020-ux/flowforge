# Specification

## Summary

Kawaii Calculator is a small, fully client-side single-page web app that performs standard four-function arithmetic (add, subtract, multiply, divide) with clear/delete and decimal support, wrapped in a cohesive, delightful kawaii visual identity (pastel palette, rounded buttons, playful mascot, gentle animations). It is responsive across mobile and desktop, fully keyboard-operable, accessible, and handles edge cases (divide-by-zero, overflow, floating-point rounding) gracefully. It requires no backend, ships as a static site to a host like GitHub Pages or Netlify, and keeps dependencies minimal. Nice-to-have enhancements (sound effects, swappable themes, light/dark mode, calculation history, percentage and sign-toggle, mascot reactions) are explicitly out of the MVP and gated behind prioritization. Success is measured by sub-2s load, correct results across a table-driven test suite, cross-browser parity (Chrome/Edge/Firefox/Safari), keyboard accessibility with zero serious axe violations, and a consistent, charming aesthetic.

## Objective

Build a charming, reliable, no-backend calculator that makes everyday arithmetic feel fun for casual and younger audiences while remaining fast, correct, and accessible. WHY IT MATTERS: most calculators are utilitarian; the differentiator here is a cohesive kawaii experience that delights without sacrificing correctness or speed. WHO IT'S FOR: students, casual consumers, and design-loving users on mobile and desktop. SUCCESS LOOKS LIKE: a static bundle that loads in under 2s, returns correct results for all four operations plus decimals and edge cases, works identically across evergreen browsers, is fully keyboard-operable and screen-reader-labeled, and presents a consistent pastel kawaii theme. USER STORIES: (1) As a casual user, I want to perform basic arithmetic quickly so I can get answers without friction. (2) As a mobile user, I want large, tappable, rounded buttons so I can calculate one-handed. (3) As a desktop/keyboard user, I want to type digits, operators, Enter for equals, Escape to clear, and Backspace to delete so I can work fast. (4) As a design-loving user, I want a cohesive kawaii theme with a cute mascot and gentle animations so the experience feels delightful. (5) As a user who made a mistake, I want clear/delete and friendly, non-crashing error messages so I can recover easily. (6) As an accessibility-reliant user, I want ARIA labels, visible focus, and WCAG AA contrast so I can use every feature.

## Target Users

General consumers skewing younger — students, casual users, and people who enjoy cute, playful aesthetics. Technical level: non-technical end users with no setup expectations; they open a URL and start tapping or typing. Primary use cases: quick everyday arithmetic on a phone or laptop (totals, splits, simple math), enjoyed for the cute, friendly experience as much as the function. Secondary: keyboard power users on desktop who want fast numeric entry. Accessibility-reliant users (keyboard-only, screen reader, reduced-motion, high-contrast needs) are first-class and must be able to use all core features.

## Tech Stack

- TypeScript 5.4+ (strict mode enabled)
- Vite 5.2+ (dev server + static build)
- Vanilla DOM (no runtime UI framework — chosen to honor 'lightweight/minimal dependencies')
- CSS3 with custom properties for theming (no CSS framework)
- Vitest 1.6+ (or 2.x) with @vitest/coverage-v8 (unit + integration)
- happy-dom 14+ (DOM environment for integration tests)
- @playwright/test 1.44+ (cross-browser e2e: Chromium, Firefox, WebKit)
- @axe-core/playwright 4.9+ (automated accessibility checks)
- ESLint 9+ (flat config) with typescript-eslint 7+
- Prettier 3.2+ (formatting)
- @lhci/cli (Lighthouse CI) for performance budget assertions
- Node.js 20 LTS+ (tooling/CI only; not a runtime dependency of the shipped site)

## Commands

```bash
build: npm ci && npm run build   # runs: tsc --noEmit && vite build → outputs static site to dist/
test: npm run test   # runs: vitest run --coverage && playwright test (unit+integration then cross-browser e2e+a11y)
lint: npm run lint   # runs: eslint . && prettier --check . && tsc --noEmit
dev: npm run dev   # runs: vite → serves http://localhost:5173 with HMR
```

## Project Structure

```
index.html → SPA entry point; mounts root, includes Content-Security-Policy meta and viewport meta
src/ → Application source (TypeScript)
src/main.ts → Bootstrap: wires UI to logic, registers keyboard handlers, applies default theme
src/logic/ → Pure calculator engine: arithmetic, input state machine, number formatting; NO DOM access, fully unit-testable
src/ui/ → DOM rendering, event handlers, display updates, mascot/animation controllers (all side effects live here)
src/styles/ → Global CSS, kawaii theme tokens as custom properties (e.g., --kawaii-pink), responsive layout, reduced-motion rules
src/assets/ → Bundled mascot art, icons, and optional sound files
public/ → Static files copied verbatim (favicon, manifest.webmanifest, robots.txt)
tests/ → Vitest unit + integration specs, mirroring src/ structure
e2e/ → Playwright cross-browser end-to-end and accessibility specs
docs/spec/ → This specification and design notes
Config root → vite.config.ts, vitest.config.ts, playwright.config.ts, eslint.config.js, .prettierrc, tsconfig.json, lighthouserc.json
```

## Code Style

- Files: kebab-case.ts (e.g., calculator-engine.ts, display-formatter.ts)
- Types/Interfaces/Classes: PascalCase (no 'I' prefix on interfaces)
- Functions/variables: camelCase
- Constants and enum members: UPPER_SNAKE_CASE
- CSS classes: BEM-style kebab-case (e.g., .calc-button--operator); theme values exposed as CSS custom properties
- Named exports only — no default exports
- TypeScript strict mode; explicit return types on all exported functions; no implicit any, no '@ts-ignore' without an inline justification comment
- Calculator math lives in pure, side-effect-free functions in src/logic; all DOM/audio/storage side effects isolated in src/ui
- Formatting enforced by Prettier: 2-space indent, single quotes, semicolons, trailing commas
- ESLint no-restricted-syntax rule bans eval and new Function; lint must pass with zero warnings
- Accessibility-first markup: every interactive control has an aria-label; decorative emoji/mascot marked aria-hidden="true"
- Commits follow Conventional Commits (feat:, fix:, chore:, test:, docs:)

## Acceptance Criteria

1. VERIFY THAT the production build served from dist/ scores Time-to-Interactive < 2s and total transferred JS+CSS <= 150KB gzipped under Lighthouse CI on a simulated mid-tier mobile / 4G profile (npm run test runs @lhci/cli assertions).
2. VERIFY THAT the src/logic unit suite covers add, subtract, multiply, divide, decimal entry, chained operations, and sign-toggle with table-driven cases and passes at 100% line+branch coverage for src/logic (vitest run --coverage).
3. GIVEN any number divided by 0 WHEN '=' is pressed THEN the display shows a friendly, non-crashing message (e.g., "Oops! Can't divide by zero") and the app remains fully usable for the next calculation (covered by unit + e2e specs).
4. GIVEN a result exceeding Number.MAX_SAFE_INTEGER or the display width WHEN computed THEN the value is shown in a bounded, rounded/scientific format and never renders raw 'NaN' or 'Infinity' or overflows the display container.
5. GIVEN floating-point operations such as 0.1 + 0.2 WHEN evaluated THEN the displayed result is '0.3' (rounded to <= 12 significant figures) — asserted by unit test.
6. GIVEN a keyboard-only user WHEN they press digits, +, -, *, /, '.', Enter (=), Escape (clear), and Backspace (delete) THEN every calculator action is reachable, the result is correct, and the focused control shows a visible focus indicator (Playwright keyboard e2e).
7. VERIFY THAT @axe-core/playwright reports zero serious or critical accessibility violations on the main view and that interactive controls meet WCAG AA contrast despite the pastel palette (npm run test).
8. GIVEN the Playwright suite WHEN run against Chromium, Firefox, and WebKit THEN all e2e tests pass, demonstrating parity for Chrome/Edge (Chromium), Firefox, and Safari (WebKit).
9. GIVEN viewport widths from 320px to 1920px WHEN the app renders THEN all controls are visible with touch targets >= 44x44px and no horizontal scrolling appears (Playwright responsive assertions).
10. VERIFY THAT no eval, Function constructor, or string-based code execution is used to compute results (ESLint no-restricted-syntax rule passes in npm run lint).
11. GIVEN a user with prefers-reduced-motion enabled WHEN animations would play THEN motion is reduced or disabled per the media query (asserted via CSS/e2e check).

## Assumptions

> These assumptions were surfaced during spec generation.
> Correct them now if they're wrong.

- Tech stack was not specified; assuming TypeScript + Vite + vanilla DOM (no UI framework) and hand-written CSS to satisfy the 'lightweight/minimal dependencies' constraint.
- Calculator uses immediate-execution (left-to-right) semantics like a standard pocket calculator, NOT operator precedence/PEMDAS — this changes results (e.g., 2 + 3 * 4 = 20, not 14) and needs confirmation.
- Floating-point precision is handled in-app by rounding to ~12 significant figures; no big-decimal library is added unless approved.
- MVP scope = four-function arithmetic + decimal + clear/delete + cohesive kawaii theme + responsive layout + keyboard support; all listed nice-to-haves (sound, multiple themes, dark mode, history, percentage, mascot reactions) are deferred until prioritized.
- localStorage, if used, holds only non-sensitive preferences (theme) and optional history; no personal data or accounts.
- Mascot/character artwork and any sound assets will be provided by design or sourced license-cleared; lightweight placeholders are used until assets arrive.
- Deployment is a static host (GitHub Pages/Netlify) serving over HTTPS; no server-side rendering or runtime backend.
- Only modern evergreen browsers are targeted; no IE/legacy polyfills.
- MVP copy is English only, but display/error strings are centralized to allow future i18n.
- Node.js 20+ is available in development and CI for tooling.
- A bundle-size performance budget of <= 150KB gzipped (JS+CSS) is acceptable as a proxy for the sub-2s load goal.

## Security Considerations

- No backend, accounts, or PII are involved; nonetheless apply defense-in-depth on the client.
- NEVER evaluate expressions with eval() or new Function(); implement a deterministic input state machine/parser to eliminate code-injection risk (also enforced by ESLint).
- Whitelist keyboard and button input; ignore non-calculator keys and clamp numeric ranges to prevent invalid-state crashes.
- Render all display output via textContent — never innerHTML/outerHTML/insertAdjacentHTML with dynamic values — to prevent XSS.
- Set a strict Content-Security-Policy via host headers and an index.html meta tag (default-src 'self'; object-src 'none'; base-uri 'self'; avoid inline script).
- Serve only over HTTPS (enforced by the static host) and enable HSTS where the host supports it.
- Prefer self-hosted/bundled assets; if any third-party/CDN asset is unavoidable, require Subresource Integrity (SRI) hashes.
- Treat localStorage as untrusted on read: namespace keys, validate/parse defensively with try/catch and a schema/shape check, and fall back to defaults on corruption; store only non-sensitive data.
- Supply-chain hygiene: pin/lock dependencies, keep the dependency count minimal, run npm audit and enable Dependabot in CI.
- No secrets, API keys, or tokens should exist in the codebase; reject any PR that introduces them.
- No third-party trackers or analytics by default to preserve user privacy.

## Testing Strategy

- Frameworks: Vitest (unit + integration) with @vitest/coverage-v8, Playwright (@playwright/test) for cross-browser e2e, and @axe-core/playwright for automated accessibility scans.
- Unit (largest layer): pure calculator engine in src/logic — table-driven tests for all four operations, decimals, chained operations, sign-toggle, divide-by-zero, overflow/bounding, and float rounding (e.g., 0.1+0.2=0.3). Coverage target: 100% lines+branches for src/logic; >= 90% overall.
- Integration: DOM event handling in happy-dom — verify button clicks and keydown events map to correct engine calls and display updates; mock localStorage and the Web Audio API; use vi.useFakeTimers() for animation/debounce timing.
- End-to-end: Playwright against Chromium, Firefox, and WebKit — full user flows, keyboard-only navigation (digits/operators/Enter/Escape/Backspace), responsive viewports (320px–1920px), divide-by-zero friendly message, and theme switching if in scope.
- Accessibility: axe-core scan must report zero serious/critical issues; assert visible focus, ARIA labels on all controls, WCAG AA contrast, and prefers-reduced-motion handling.
- Performance: Lighthouse CI (@lhci/cli) asserts Time-to-Interactive < 2s and the <= 150KB gzipped bundle budget on a mid-tier mobile profile.
- Mocking approach: stub Web Audio (sound), localStorage, and timers; no network layer exists, so there are no external services to mock.
- Test locations: unit/integration specs in tests/ mirroring src/ structure; e2e/a11y specs in e2e/. CI runs lint + all test levels on every PR with the coverage gate enforced as a merge blocker.

## Boundaries

### Always

- ✅ Implement all arithmetic as pure, side-effect-free functions in src/logic with full unit coverage.
- ✅ Run `npm run lint` and `npm run test` (unit + integration + e2e + a11y) before every commit/PR and keep them green.
- ✅ Use TypeScript strict mode with explicit types on exported functions and zero implicit any.
- ✅ Validate/whitelist all keyboard and button input, and explicitly handle divide-by-zero, overflow, and float rounding.
- ✅ Write display output via textContent and use semantic HTML with ARIA labels/roles for every control.
- ✅ Respect prefers-reduced-motion, maintain >= 44px touch targets, and meet WCAG AA contrast even within the pastel palette.
- ✅ Keep the shipped bundle within the performance budget (<= 150KB gzipped JS+CSS).
- ✅ Provide a keyboard equivalent for every interactive control.

### Ask First

- ⚠️ Adding any runtime dependency (e.g., decimal.js, a UI framework, an animation or sound library).
- ⚠️ Introducing a UI framework or changing the build tool (Vite → other).
- ⚠️ Changing calculator semantics (immediate-execution <-> operator precedence).
- ⚠️ Adding persistence beyond theme/history, or any data leaving the device.
- ⚠️ Adding analytics/telemetry or any third-party/CDN-hosted asset.
- ⚠️ Raising the bundle-size or load-time performance budget.
- ⚠️ Changing CI/CD, hosting, or Content-Security-Policy configuration.

### Never

- 🚫 Use eval(), new Function(), or any string-based code execution to compute results.
- 🚫 Inject dynamic or user-derived strings via innerHTML/outerHTML/insertAdjacentHTML.
- 🚫 Commit secrets, API keys, or tokens (none should exist in this project).
- 🚫 Store sensitive or personal data in localStorage.
- 🚫 Ship with failing or skipped tests, or remove failing tests without explicit approval.
- 🚫 Use the `any` type or silence type errors with `@ts-ignore` without a justified inline comment.
- 🚫 Add user accounts, backend calls, or third-party trackers.
- 🚫 Block or gate core arithmetic behind optional/nice-to-have features.

## Open Questions

- [ ] Confirm calculator semantics: immediate-execution (standard pocket calculator) vs. operator precedence (PEMDAS)?
- [ ] Which nice-to-haves, if any, are in scope for v1 — sound effects, specific themes, light/dark mode, history, percentage/sign-toggle, mascot reactions?
- [ ] If history is included, should it persist across sessions via localStorage or be session-only?
- [ ] Who supplies the mascot/character art and sounds, and what are the licensing terms? Is there a specific character to match?
- [ ] Preferred number formatting: thousands separators? At what magnitude should it switch to scientific notation, and what max digit count?
- [ ] Are there target Lighthouse thresholds beyond load time (e.g., Accessibility >= 95, Performance >= 90)?
- [ ] Is any analytics/telemetry desired, or should the app remain zero-tracking by default (privacy-preserving)?
- [ ] Should gentle animations always respect prefers-reduced-motion (assumed yes) — confirm intensity/defaults?

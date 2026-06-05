# Specification

## Summary

A small, fully client-side single-page web application: a kawaii-themed standard calculator for general consumers who love cute design. It performs the four core arithmetic operations (add, subtract, multiply, divide) plus decimals, clear, and delete, correctly handling edge cases like divide-by-zero and overflow, all wrapped in a polished pastel kawaii aesthetic with rounded buttons, cute typography, and mascot/emoji accents. The UI is responsive and touch-friendly across mobile and desktop, fully keyboard-accessible, and runs entirely in the browser with no backend or external API dependencies, loading interactively in under 2 seconds and working offline after first load. Optional enhancements (press animations, sound effects, percentage/sign-toggle, calculation history, theme switching, scientific functions) are scoped as nice-to-haves behind explicit approval gates.

## Objective

Build a delightful, correct, and accessible basic calculator that makes everyday arithmetic feel cute and fun. WHY: existing calculators are utilitarian and joyless; a kawaii calculator differentiates on emotional design while still being a reliable tool. WHO: students, casual users, and fans of cute aesthetics on phones, tablets, and desktops. SUCCESS: arithmetic is always correct, edge cases degrade gracefully with friendly messaging, the app loads in <2s, the theme is visually consistent and polished, and the interface is usable by touch, mouse, and keyboard (including screen readers). User stories: (1) As a casual user, I want to perform add/subtract/multiply/divide quickly so I can handle everyday math. (2) As a kawaii-design fan, I want a cute, cohesive pastel interface so the experience feels delightful. (3) As a mobile user, I want large, well-spaced, touch-friendly buttons so I can tap accurately one-handed. (4) As a keyboard user, I want to type digits, operators, Enter (=), and Escape (clear) so I can calculate without a mouse. (5) As a screen-reader user, I want labeled controls and announced results so I can use the calculator with assistive tech. (6) As any user, when I divide by zero or overflow, I want a friendly, non-crashing message rather than 'Infinity' or 'NaN'.

## Target Users

General consumers and kawaii-aesthetic fans with no technical expertise required — students, casual everyday users, and people who enjoy cute design. They access the app via a modern mobile or desktop browser, primarily for quick, low-stakes arithmetic (tips, splitting bills, homework checks). Not a professional, financial, or scientific audience; precision and scientific notation are secondary concerns. Users expect instant load, obvious tappable controls, and a charming visual experience.

## Tech Stack

- TypeScript 5.4+ (strict mode) — chosen over plain JS for type safety and testability of the calculator engine
- Vite 5+ — build tool, dev server, and static bundler
- Vanilla DOM + ES modules (no UI framework) — keeps bundle tiny to meet <2s load
- CSS3 with custom properties (CSS variables) for design tokens and theme switching; no CSS framework
- Vitest 1.x + @testing-library/dom + jsdom — unit and DOM-integration tests
- Playwright 1.4x — end-to-end, responsive-viewport, and offline tests
- @axe-core/playwright 4.x — automated accessibility checks
- ESLint 9 (flat config) + @typescript-eslint + Prettier 3 — linting and formatting
- Node.js 20 LTS — build/CI runtime
- npm 10+ — package manager (lockfile committed)

## Commands

```bash
build: npm run build   # runs: tsc --noEmit && vite build  → emits static site to dist/
test: npm run test   # runs: vitest run --coverage  (unit + DOM integration); e2e via: npm run test:e2e (playwright test)
lint: npm run lint   # runs: eslint . --max-warnings 0 && prettier --check . && tsc --noEmit
dev: npm run dev   # runs: vite --open  → http://localhost:5173
```

## Project Structure

```
index.html → SPA entry HTML; mounts #app and loads src/main.ts
src/ → application source code (TypeScript + CSS)
src/main.ts → bootstrap: wires engine to UI and registers input handlers
src/calculator/ → pure calculation engine, no DOM or side effects (highly unit-tested)
src/calculator/engine.ts → state machine for input sequence, chained ops, equals/repeat
src/calculator/operations.ts → arithmetic primitives and safe divide-by-zero/overflow handling
src/calculator/format.ts → display formatting, precision rounding, scientific fallback
src/ui/ → DOM rendering and event wiring (depends on engine, never the reverse)
src/ui/keypad.ts → button grid rendering and click/tap handling
src/ui/display.ts → display render + ARIA live announcements
src/ui/keyboard.ts → physical keyboard mapping (digits, operators, Enter, Escape, Backspace)
src/ui/theme.ts → theme/token application and prefers-color-scheme handling
src/styles/ → CSS: tokens.css (pastel palette, radii, spacing), calculator.css, themes.css, reset.css
src/assets/ → self-hosted mascot/emoji images, fonts, and optional sound files
public/ → static files copied verbatim (favicon, manifest if approved)
tests/unit/ → Vitest specs mirroring src/ structure
tests/e2e/ → Playwright specs (flows, responsive, a11y, offline)
docs/spec/ → this specification and related design notes
vite.config.ts, tsconfig.json, eslint.config.js, .prettierrc, package.json → tooling config at root
```

## Code Style

- Files: kebab-case.ts and kebab-case.css
- Types/interfaces/classes: PascalCase (e.g., CalculatorState)
- Functions/variables: camelCase
- Module-level constants: UPPER_SNAKE_CASE
- CSS custom properties: --kc-* prefix in kebab-case (e.g., --kc-color-pastel-pink)
- Named exports only; no default exports
- Strict TypeScript: explicit return types on exported functions; no `any`, no non-null `!` assertions
- Calculator engine functions are pure (input → output), deterministic, and DOM-free
- UI modules own all DOM access; render with textContent / createElement, never innerHTML for dynamic values
- Prettier formatting: 2-space indent, single quotes, semicolons, 100-char print width, trailing commas
- CSS class naming: BEM-ish (block__element--modifier) or data-* hooks for JS; no inline styles for theming
- All interactive elements are real <button> elements with type and aria-label

## Acceptance Criteria

1. GIVEN two operands and any of + - × ÷ WHEN equals is triggered THEN the displayed result equals the mathematically correct value (verified by Vitest engine tests covering positive, negative, decimal, and large-number cases)
2. GIVEN any number divided by zero WHEN equals is triggered THEN the display shows a friendly non-crashing message (e.g., 'Oops! Can't divide by zero 🥺') and the engine returns an explicit error state — never 'Infinity', 'NaN', or a thrown exception (Vitest unit test + Playwright e2e)
3. GIVEN chained operations like 2 + 3 × 4 = WHEN evaluated THEN the result matches the documented left-to-right pocket-calculator semantics (see open question) and is asserted in Vitest
4. GIVEN decimal arithmetic such as 0.1 + 0.2 WHEN evaluated THEN the displayed result is '0.3' after precision rounding (no 0.30000000000000004) — verified by Vitest format/engine tests
5. VERIFY THAT the production build (npm run build) is interactive in < 2s on a simulated Fast 3G/mid-tier CPU profile, asserted via a Playwright performance measurement (Time-to-Interactive / first-input readiness) in tests/e2e
6. VERIFY THAT @axe-core/playwright reports zero 'critical' or 'serious' accessibility violations on the calculator page
7. GIVEN a viewport width of 320px and of 1440px WHEN the app renders THEN all buttons are visible, non-overlapping, and have a touch target ≥ 44×44 CSS px (Playwright viewport assertions)
8. GIVEN keyboard-only operation WHEN the user types digits, an operator, Enter, Escape, and Backspace THEN the display updates correctly for each, matching equivalent button clicks (Playwright e2e)
9. VERIFY THAT statement+branch coverage of src/calculator/** is ≥ 90% and overall project coverage is ≥ 80% (vitest --coverage gate fails the build below threshold)
10. VERIFY THAT after first load, the app functions with the network disabled and makes zero runtime network requests (Playwright offline/route-abort test)
11. VERIFY THAT npm run lint exits 0 (eslint --max-warnings 0, prettier --check, tsc --noEmit all pass)

## Assumptions

> These assumptions were surfaced during spec generation.
> Correct them now if they're wrong.

- TypeScript is used instead of plain JavaScript for engine testability and type safety; the constraint 'vanilla JS or lightweight framework' is interpreted as permitting TS that compiles to vanilla JS with no runtime framework
- No UI framework is used (vanilla DOM) to keep the bundle minimal and reliably meet the <2s load target
- The calculator uses simple left-to-right evaluation (basic pocket-calculator behavior), NOT operator precedence (PEMDAS) — flagged as an open question
- Floating-point results are rounded to ~12 significant digits and fall back to scientific notation only beyond a display-width threshold to avoid NaN/precision artifacts
- Calculation history (nice-to-have) and theme choice, if implemented, persist only in localStorage; no other data is stored and nothing leaves the device
- UI is English-only; no internationalization in scope
- 'Modern evergreen browsers' means the latest two stable versions of Chrome, Firefox, Safari, and Edge
- Mascot/emoji accents use system emoji or self-hosted, license-cleared assets — no external CDN or third-party API
- Sound effects (nice-to-have) default OFF and respect prefers-reduced-motion; no audio plays without user opt-in
- Hosting is a static file host (e.g., GitHub Pages/Netlify) over HTTPS; the deliverable is the contents of dist/

## Security Considerations

- Arithmetic is computed with explicit operation functions only — never eval() or new Function() — to eliminate code-injection risk from user input
- All dynamic display values are written via textContent / DOM node APIs; innerHTML is never used with computed or stored values to prevent XSS
- Input is validated/constrained at the engine boundary: only digits, a single decimal point, and known operators are accepted; malformed sequences are rejected gracefully
- A Content-Security-Policy (meta tag and/or host header) restricts script/style/img/font/connect sources to 'self'; no inline event handlers
- No secrets, API keys, tokens, or credentials exist in the codebase or build output (nothing to leak; there is no backend)
- localStorage (if used for theme/history) stores only non-sensitive data and is defensively parsed (try/catch, schema/shape validation) on read to tolerate tampering or corruption
- Any third-party asset is self-hosted; if a CDN is ever introduced it must use Subresource Integrity (SRI) — but the default is zero external requests at runtime
- The app is served exclusively over HTTPS
- Dependencies are pinned via committed lockfile; `npm audit --audit-level=high` runs in CI and Dependabot/Renovate monitors updates

## Testing Strategy

- Framework: Vitest (+ jsdom + @testing-library/dom) for unit/integration; Playwright (+ @axe-core/playwright) for e2e/a11y
- Hierarchy — Unit (largest layer): exhaustively test src/calculator/** pure functions: each operation, chained operations, repeated equals, decimal precision, divide-by-zero, overflow, clear/delete, leading-zero and multi-decimal guards
- Hierarchy — Integration: test src/ui/** wiring with @testing-library/dom — button clicks and keyboard events drive engine state and update the display/ARIA live region correctly
- Hierarchy — E2E: full user flows in real browsers, responsive checks at 320px and 1440px, keyboard-only operation, offline-after-load, and a load-performance assertion (<2s interactive)
- Accessibility: automated axe scans gate on zero critical/serious violations; manual screen-reader smoke check documented in test notes
- Coverage targets: ≥90% statements+branches for src/calculator/**, ≥80% overall; CI fails below threshold
- Mocking: mock the Web Audio API and localStorage in unit tests; use Vitest fake timers for animation/debounce logic; Playwright aborts/disables network for the offline test — no external services to mock since there are none
- Test layout: tests/unit/ mirrors src/ structure; tests/e2e/ holds Playwright specs; tests run in CI on every push/PR before merge

## Boundaries

### Always

- ✅ Run `npm run lint` and `npm run test` (and pass) before every commit/PR merge
- ✅ Keep src/calculator/** pure: no DOM access, no side effects, deterministic outputs, fully unit-tested
- ✅ Write computed/stored values to the DOM via textContent or element APIs — never innerHTML
- ✅ Maintain ≥90% coverage on the calculator engine and ≥80% overall
- ✅ Drive every color, radius, spacing, and font through CSS custom-property design tokens
- ✅ Respect prefers-reduced-motion (disable animations/sound) and prefers-color-scheme (default theme)
- ✅ Give every control a real <button> with an explicit type and aria-label, and announce results via an aria-live region
- ✅ Self-host all fonts, images, and sounds; keep runtime network requests at zero

### Ask First

- ⚠️ Adding any runtime dependency or introducing a UI framework
- ⚠️ Adding a service worker / PWA manifest or any caching layer
- ⚠️ Persisting anything to localStorage (theme, history) or other client storage
- ⚠️ Changing evaluation semantics (e.g., switching from left-to-right to PEMDAS)
- ⚠️ Enabling sound effects on by default or adding autoplaying audio
- ⚠️ Changing build, CI, lint, or coverage-threshold configuration
- ⚠️ Adding scientific functions or other features beyond the confirmed must-have set

### Never

- 🚫 Use eval(), new Function(), or any dynamic code execution for arithmetic
- 🚫 Use innerHTML (or equivalent) with user-entered or computed values
- 🚫 Add a backend, server, or any external/third-party API or network call at runtime
- 🚫 Commit secrets, API keys, tokens, or credentials
- 🚫 Use the `any` type, `@ts-ignore`, or otherwise disable TypeScript strictness without approval
- 🚫 Remove, skip, or weaken failing tests (including accessibility checks) to make CI pass without approval
- 🚫 Ship a build with critical/serious axe violations or with lint/type errors

## Open Questions

- [ ] Evaluation semantics: simple left-to-right (assumed) or full operator precedence (PEMDAS)? This changes engine design and test expectations.
- [ ] Maximum displayable digits and the exact overflow threshold before switching to scientific notation or an 'overflow' message?
- [ ] Should calculation history persist across sessions (localStorage) or be session-only? Is history a confirmed must-have or nice-to-have for v1?
- [ ] Branding: custom-illustrated mascot vs. emoji-only? If custom art, who provides assets and what is the license?
- [ ] Are sound effects in v1 scope, and what is the default state (off assumed)?
- [ ] Minimum supported mobile width — 320px (assumed) or 360px?
- [ ] Is PWA/installability + a service worker for offline desired, or is 'works offline once loaded' satisfied by static caching alone?
- [ ] Which themes ship in v1 (light/dark only, or a selectable palette set), and is theme switching must-have or nice-to-have?

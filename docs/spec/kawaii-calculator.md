# Specification

## Summary

A client-side, single-page web application that delivers a fully functional everyday four-function calculator wrapped in a polished kawaii aesthetic (pastel palette, rounded buttons, a reactive mascot, and gentle micro-animations). It runs entirely in the browser with no backend, persisting only optional UI preferences and history via localStorage. The MVP ships reliable arithmetic (add, subtract, multiply, divide, decimal, clear/delete), a responsive mobile-first layout, and full keyboard + touch input. Nice-to-have layers (sound effects, mascot reactions, selectable themes, dark mode, history, percent, and sign-toggle) build on a decoupled, pure calculation engine so the delightful UI can evolve without risking arithmetic correctness. Success is defined by correct results across all edge cases, a sub-2-second load, accessible and responsive layouts on phones and desktops, cross-browser support (Chrome/Firefox/Safari/Edge), and a convincingly adorable look-and-feel.

## Objective

Build a charming, lightweight, dependency-light calculator that makes routine arithmetic feel fun for general consumers — especially fans of cute aesthetics, students, and casual mobile/desktop users. It matters because most calculators are utilitarian and joyless; a fast, accessible, delightful tool that still computes correctly can win users on personality without sacrificing reliability. Success looks like: every arithmetic operation returns the correct, precision-safe result; edge cases (division by zero, floating-point artifacts, chained operations) are handled gracefully with friendly messaging; the app loads in under 2 seconds on a 4G connection; it is fully usable via touch and keyboard across modern browsers and screen sizes from 320px up; and it presents an unmistakably kawaii visual design that meets accessibility contrast standards. User stories: (1) As a casual user, I want to add/subtract/multiply/divide numbers so I can do everyday math. (2) As a mobile user, I want a responsive, thumb-friendly layout so I can calculate one-handed on my phone. (3) As a keyboard user, I want to type digits, operators, Enter, and Backspace so I can work quickly on desktop. (4) As a fan of cute things, I want a pastel mascot that reacts to my actions so the tool feels delightful. (5) As a returning user, I want my theme and recent calculations remembered so the app feels personal. (6) As a user who makes mistakes, I want clear, delete, and friendly error messages so errors are easy to recover from.

## Target Users

Non-technical general consumers: students doing homework, casual shoppers splitting bills, and fans of cute/kawaii aesthetics who enjoy charming lightweight tools. They expect zero setup (open a URL and use it), no sign-in, and instant responsiveness. Primary use cases are quick everyday arithmetic on a phone (touch) or desktop (keyboard). Technical level: low — they will never read docs, configure settings beyond a theme toggle, or tolerate confusing states; the UI must be self-evident, forgiving, and accessible.

## Tech Stack

- TypeScript 5.4+ (strict mode)
- Vite 5.2+ (dev server, bundler, static build to dist/)
- Vanilla TypeScript with no UI framework (assumption — keeps bundle small for the <2s budget; see open_questions)
- Plain CSS with CSS custom properties for design tokens/theming (no CSS framework)
- Vitest 1.6+ with @vitest/coverage-v8 for unit/integration tests (jsdom environment)
- jsdom 24+ as the DOM environment for component-level unit tests
- Playwright 1.44+ for cross-browser e2e (Chromium, Firefox, WebKit) and responsive/mobile viewport tests
- @axe-core/playwright 4.9+ for automated accessibility assertions
- ESLint 9+ with typescript-eslint 7+ (flat config)
- Prettier 3.2+ for formatting
- Lighthouse CI 0.13+ for performance/load-budget enforcement in CI (optional but recommended)
- Web Audio API (native) for button-press sounds — no audio library
- Node.js >=20.11 LTS and npm >=10 for build/dev/test tooling

## Commands

```bash
build: tsc --noEmit && vite build
test: vitest run --coverage && playwright test
lint: eslint . --max-warnings 0 && prettier --check .
dev: vite
```

## Project Structure

```
index.html → App entry HTML; mounts root container and loads src/main.ts
public/ → Static assets served as-is: mascot SVGs, sound files (.mp3/.ogg), favicon, web manifest
src/main.ts → Bootstrap; wires keypad, keyboard input, renderer, and feature modules together
src/core/calculator.ts → Pure, DOM-free calculator state machine (current/previous operand, pending op, error state)
src/core/operations.ts → Precision-safe arithmetic operations and rounding helpers
src/core/format.ts → Display formatting: digit grouping, overflow/exponential fallback, friendly error text
src/ui/keypad.ts → Renders buttons and binds click/pointer events to calculator actions
src/ui/display.ts → Updates primary result and secondary expression displays via safe DOM APIs
src/ui/mascot.ts → Mascot expression/reaction state (idle, happy, surprised, error)
src/ui/animations.ts → Micro-animation helpers (button press, value transitions); respects prefers-reduced-motion
src/input/keyboard.ts → Maps keyboard events to calculator actions (digits, operators, Enter, Backspace, Escape)
src/features/history.ts → Calculation history backed by localStorage (capped, schema-validated)
src/features/themes.ts → Theme + dark-mode selection persisted to localStorage
src/features/sound.ts → Button-press sound effects via Web Audio with mute toggle
src/styles/tokens.css → Design tokens: pastel palette, radii, spacing, typography (CSS custom properties)
src/styles/base.css → CSS reset and base layout
src/styles/calculator.css → Component styles and responsive breakpoints (mobile-first)
tests/unit/ → Vitest unit/integration tests mirroring src/ structure
tests/e2e/ → Playwright specs: touch flows, keyboard flows, responsive layouts, a11y, performance
docs/spec/kawaii-calculator.md → This specification (source of truth)
vite.config.ts → Vite + Vitest configuration
playwright.config.ts → Playwright projects (browsers + mobile viewports)
tsconfig.json → TypeScript strict configuration
eslint.config.js → ESLint flat config
.prettierrc → Prettier configuration
package.json → Scripts and dependencies
```

## Code Style

- Module/file names: kebab-case (e.g., keyboard-input.ts); single-word modules allowed (calculator.ts)
- Functions and variables: camelCase
- Types, interfaces, and classes: PascalCase
- True constants: UPPER_SNAKE_CASE
- Named exports only — no default exports
- TypeScript strict mode on; never use `any` or `// @ts-ignore` (use `unknown` + narrowing instead)
- src/core/ must be pure: no DOM, window, or localStorage access — only data in, data out
- Render dynamic values with textContent or explicit DOM node creation; never innerHTML with dynamic data
- Formatting via Prettier: 2-space indent, single quotes, semicolons, trailing commas, max line length 100
- All colors, spacing, radii, and fonts come from CSS custom properties in tokens.css — no hardcoded values in component CSS
- Comments explain why, not what; public functions in src/core/ carry JSDoc with examples
- Prefer small pure functions; isolate side effects (DOM, audio, storage) at the edges (ui/, features/, input/)

## Acceptance Criteria

1. GIVEN the calculator UI WHEN a user enters '7 × 8 =' via taps THEN the primary display shows '56' (verified by Vitest engine test and a Playwright click test)
2. GIVEN any divide-by-zero input WHEN the user computes '5 ÷ 0 =' THEN the display shows a friendly error message (e.g., 'Oops! Can not divide by zero') and never renders 'Infinity', 'NaN', or 'undefined' (Vitest + Playwright)
3. VERIFY THAT '0.1 + 0.2 =' displays '0.3' due to precision-safe rounding (Vitest unit test on src/core)
4. VERIFY THAT chained immediate-execution input '2 + 3 × 4 =' yields '20' (left-to-right semantics), asserted by a Vitest test that documents the chosen semantics
5. VERIFY THAT the production build's total gzipped JS+CSS is < 150 KB AND Lighthouse (Fast 3G/4G throttle, mid-tier mobile) reports Time-To-Interactive < 2s (Lighthouse CI assertion in pipeline)
6. GIVEN a 320px-wide viewport WHEN the app loads THEN all controls are visible with no horizontal scroll and every interactive target is at least 44x44 CSS px (Playwright responsive test using boundingBox checks)
7. GIVEN keyboard focus on the page WHEN the user types '1 2 + 3 Enter' THEN the display shows '15' (Playwright keyboard test) and Backspace deletes the last digit while Escape clears all
8. VERIFY THAT unit-test coverage is >= 95% lines and branches for src/core/ and >= 85% overall, enforced as Vitest coverage thresholds that fail the build when unmet
9. VERIFY THAT @axe-core/playwright reports zero serious or critical violations and all themes (including dark mode) pass WCAG AA contrast on text and controls
10. VERIFY THAT core arithmetic flows pass in Chromium, Firefox, and WebKit via the Playwright cross-browser project matrix
11. GIVEN localStorage throws or is unavailable WHEN the app starts THEN it still loads and computes correctly, with history/theme persistence silently disabled (Playwright test with storage mocked to throw)
12. VERIFY THAT no calculation path uses eval(), Function(), or innerHTML with dynamic data, enforced by an ESLint rule (no-eval, no-implied-eval) that fails lint

## Assumptions

> These assumptions were surfaced during spec generation.
> Correct them now if they're wrong.

- No UI framework will be used; the app is built in vanilla TypeScript + Vite to keep the bundle minimal for the <2s load target (React/Vue were 'acceptable' but not required) — flagged for confirmation
- The calculator uses immediate-execution (left-to-right) semantics like a standard four-function calculator, NOT operator precedence; '2 + 3 × 4' evaluates to 20
- Results are rounded to ~10 significant digits to suppress floating-point artifacts; values exceeding display width fall back to exponential notation
- 'Typical connection' for the load-time criterion is interpreted as a Fast 3G/4G throttle on a mid-tier mobile device (Lighthouse default mobile profile)
- Mascot artwork and sound assets are provided or sourced under a permissive license; tasteful placeholders are used until final assets arrive
- The app is English-only and single-locale for v1, using '.' as decimal separator and ',' for thousands grouping
- localStorage is the only persistence; there are no runtime network calls, no analytics, and no telemetry
- 'Modern evergreen browsers' means the last two stable versions of Chrome, Firefox, Safari, and Edge with ES2020+, CSS custom properties, and Web Audio support
- Sound effects default to OFF (with an in-UI toggle) to respect browser autoplay policies and avoid surprising users
- Sound, themes, dark mode, history, percent, and sign-toggle are nice-to-haves; the must-have arithmetic + responsive + keyboard scope is delivered first
- Hosting serves the static build over HTTPS and allows setting a Content-Security-Policy header

## Security Considerations

- Authentication/authorization: none by design — no accounts, no login, no roles; this is explicitly out of scope and must remain so
- Expression evaluation: never use eval(), new Function(), or any string-to-code execution; arithmetic is computed by the typed engine in src/core/ to eliminate injection risk (enforced via ESLint no-eval/no-implied-eval)
- Input validation: constrain all keypad and keyboard input to an explicit allowed token set (digits, '.', operators, control keys) before the engine processes it; reject/ignore anything else
- XSS prevention: render all dynamic and computed values via textContent or DOM node APIs — never innerHTML/outerHTML/document.write with dynamic content
- localStorage safety: treat stored data as untrusted; wrap all reads/writes in try/catch, schema-validate parsed values, cap history size to prevent quota abuse, and fall back gracefully on failure or unavailability
- Secrets management: none required and none permitted in the client bundle; nothing sensitive ships to the browser
- Dependency/supply-chain: commit a lockfile, pin version ranges, minimize runtime dependencies, and run `npm audit --audit-level=high` in CI
- Transport & headers: serve over HTTPS with a strict Content-Security-Policy (default-src 'self'; no inline scripts; no eval), plus X-Content-Type-Options: nosniff and a sane Referrer-Policy
- Asset hosting: self-host mascot and sound assets from /public; avoid third-party runtime CDNs to prevent tracking and CSP gaps (use Subresource Integrity if a CDN is ever introduced)
- Privacy: collect no PII, run no analytics or telemetry, and make no outbound network requests at runtime

## Testing Strategy

- Frameworks: Vitest (+ @vitest/coverage-v8) for unit/integration in a jsdom environment; Playwright for cross-browser e2e; @axe-core/playwright for accessibility; Lighthouse CI for the performance budget
- Unit tests (largest layer): exhaustively cover src/core/ (calculator.ts, operations.ts, format.ts) — all four operations, decimals, clear/delete, chained immediate-execution, division by zero, floating-point precision, overflow/exponential formatting, and multiple-decimal-point rejection; target >= 95% line/branch coverage for core
- Integration tests: verify input-to-engine-to-display wiring, keyboard mapping (input/keyboard.ts), and feature modules (history/themes/sound) with mocked localStorage and Web Audio; target >= 85% overall coverage
- E2E tests (Playwright): real-browser tap and keyboard flows, responsive layouts at 320/768/1024+ widths, theme and dark-mode toggle persistence across reload, friendly error states, and graceful degradation when localStorage throws
- Accessibility tests: run axe scans on initial load and across themes; assert zero serious/critical violations and WCAG AA contrast
- Performance tests: Lighthouse CI asserts TTI < 2s and bundle size budget < 150 KB gzipped on a mobile throttle profile
- Test locations: unit/integration in tests/unit/ mirroring src/ structure; e2e and a11y/perf in tests/e2e/
- Mocking approach: core engine is pure and needs no mocks; use vi.fn()/vi.mock for Web Audio and localStorage, vi.useFakeTimers for animation/debounce timing, and Playwright storage/route mocks for failure scenarios
- Determinism: assert end-states, classes, and data attributes rather than exact animation timing; respect and test prefers-reduced-motion behavior
- CI gate: lint + typecheck + unit (with coverage thresholds) + e2e + a11y + performance budget must all pass before merge

## Boundaries

### Always

- ✅ Keep all arithmetic and state logic in pure, DOM-free modules under src/core/ with accompanying unit tests
- ✅ Validate and constrain every keypad and keyboard input to the allowed token set before processing
- ✅ Render values with textContent or safe DOM node APIs — never innerHTML with dynamic data
- ✅ Run lint, typecheck, unit tests (meeting coverage thresholds), e2e, and a11y checks before merging
- ✅ Source all colors/spacing/radii from CSS custom-property design tokens and verify WCAG AA contrast for every theme including dark mode
- ✅ Wrap all localStorage access in try/catch with schema validation and graceful fallback when unavailable or full
- ✅ Maintain minimum 44x44 CSS px touch targets and full keyboard operability for every control
- ✅ Keep the production bundle within budget (< 150 KB gzipped, < 2s TTI on a 4G/mobile profile) and respect prefers-reduced-motion

### Ask First

- ⚠️ Adding any runtime dependency or introducing a UI framework (React/Vue) — must justify against the bundle/performance budget
- ⚠️ Changing calculator semantics (e.g., immediate-execution to operator precedence) or defining percent behavior
- ⚠️ Adding network calls, analytics, telemetry, a service worker/PWA, or any third-party runtime CDN
- ⚠️ Changing the localStorage schema or persistence keys
- ⚠️ Adjusting performance budgets, coverage thresholds, or the supported-browser matrix
- ⚠️ Introducing or reconfiguring build/test tooling (Vite, ESLint, Playwright, Prettier, tsconfig)

### Never

- 🚫 Use eval(), new Function(), or any string-to-code execution to compute results
- 🚫 Render dynamic, user, or computed content via innerHTML/outerHTML or document.write
- 🚫 Display raw 'NaN', 'Infinity', or 'undefined' to the user — always map to a friendly state
- 🚫 Commit secrets, API keys, or any tracking/telemetry code
- 🚫 Use the `any` type or `// @ts-ignore` to bypass type errors
- 🚫 Remove or skip failing tests, or lower coverage thresholds, without explicit approval
- 🚫 Hardcode colors, spacing, or radii outside the design-token system
- 🚫 Add a backend, user accounts, or collect any personally identifiable information

## Open Questions

- [ ] Confirm vanilla TypeScript vs. adopting a small framework (React/Vue) for anticipated feature growth — recommendation is vanilla to protect the bundle budget
- [ ] What are the final mascot design(s), mascot name, and exact pastel palette hex values? Need design assets/tokens
- [ ] Confirm calculator semantics: immediate-execution (assumed) vs. operator precedence
- [ ] Define percent (%) behavior precisely: 'x% of current operand' vs. simple 'divide by 100'?
- [ ] History scope: how many entries are kept, can users tap a past result to reuse it, and is there a clear-all action?
- [ ] Should sounds default ON (with mute) or OFF (with enable)? Confirm against UX/autoplay tradeoffs
- [ ] Is an installable PWA / offline support (service worker + manifest) desired now or later?
- [ ] What is the maximum on-screen digit length before triggering overflow/exponential formatting?
- [ ] Any future localization or RTL requirements that should influence the layout/token design now?
- [ ] Standardize the exact device class and network profile for the canonical <2s load measurement

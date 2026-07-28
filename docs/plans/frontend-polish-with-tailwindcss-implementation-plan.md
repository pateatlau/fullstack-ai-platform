# Frontend Polish with Tailwind CSS v4 - Implementation Plan

## Objective

Overhaul the chatbot page into a ChatGPT-like interface with a reusable sidebar foundation for future multi-chat sessions, implemented with Tailwind CSS v4 and fully responsive behavior.

## Execution Mode

- Implement sequentially by phase.
- After each phase verification is complete, stop and request explicit user confirmation before starting the next phase.

## Phase Status

- Phase 0 - Complete
- Phase 1 - Complete (2026-07-12)
- Phase 2 - Complete (2026-07-12)
- Phase 3 - Complete (2026-07-12)
- Phase 4 - Complete (2026-07-12)
- Phase 5 - Complete (2026-07-12)
- Phase 6 - Complete (2026-07-12)
- Phase 7 - Complete (2026-07-12)

## Scope

- In scope:
  - Chatbot page layout/system redesign
  - Left sidebar for chat history foundation
  - Main chat thread redesign
  - Prompt composer redesign
  - Full responsive behavior (mobile/tablet/desktop)
  - Removal of legacy page-level CSS styling usage
- Out of scope:
  - Backend schema/API changes for persistent multi-chat sessions
  - Advanced chat management features (rename, archive, share) beyond placeholder wiring

## Non-Negotiable Requirements

1. Tailwind CSS v4 is the primary styling system for the redesigned chatbot page.
2. Existing CSS styles/classes for the page are removed from active use.
3. The sidebar architecture supports future multi-chat session integration.
4. Every phase below must be verifiable before moving to the next phase.
5. shadcn may be used when required (for example, accessible primitives or faster implementation), while keeping Tailwind-first styling.
6. User confirmation is required between phases.

## Phase 0 - Baseline and Safety Net

### Tasks

- Capture current UI screenshots (desktop/tablet/mobile) for comparison.
- Identify all chatbot-page-related style sources (CSS files, class names, shared style imports).
- Identify all current page components involved in rendering the chatbot screen.
- Confirm current behavior with existing tests and app startup sanity checks.

### Verification Checklist

- Baseline screenshots stored or attached to PR notes.
- Inventory list of legacy CSS/style touchpoints created.
- Existing app starts and current chatbot route renders without errors.

### Exit Criteria

- Team agrees on baseline and target scope before code changes begin.
- User confirms Phase 0 completion.

## Phase 1 - Tailwind v4 Foundation

### Tasks

- Ensure Tailwind CSS v4 is installed and configured correctly in frontend.
- Wire Tailwind entry imports and build pipeline for dev and production.
- Define core design tokens (spacing scale usage, color intent, radius, shadows, typography intent).
- Verify utility classes are applied in a simple test component/page.

### Verification Checklist

- Tailwind classes visibly render in app.
- Build/lint pass with Tailwind setup in place.
- No regression in app boot or routing.

### Exit Criteria

- Tailwind v4 setup is stable and ready for full page migration.
- User confirms Phase 1 completion.

## Phase 2 - App Shell Restructure (ChatGPT-like Layout)

### Tasks

- Refactor chatbot page into structural sections:
  - Sidebar container
  - Main conversation container
  - Composer container
- Implement responsive shell behavior:
  - Desktop: persistent sidebar
  - Tablet: collapsible sidebar
  - Mobile: off-canvas drawer/sidebar
- Add semantic landmarks and accessibility hooks (nav/main/form/aria labels).

### Verification Checklist

- Layout works at representative breakpoints (e.g., 375px, 768px, 1024px, 1440px).
- Sidebar can be shown/hidden appropriately on tablet/mobile.
- Keyboard navigation across shell controls works.

### Exit Criteria

- New responsive shell is complete and functional without message-level visual polish yet.
- User confirms Phase 2 completion.

## Phase 3 - Sidebar (History Foundation for Multi-Chat)

### Tasks

- Build sidebar sections:
  - New Chat action
  - Chat history list (mocked or current session placeholders)
  - Footer/utility area
- Add selected/hover/focus states via Tailwind utilities.
- Establish extensible sidebar item type/interface for future multi-chat metadata.
- Add empty/loading states that can later map to real session data.

### Verification Checklist

- Sidebar renders list items reliably.
- Active item state is visually distinct and accessible.
- Sidebar structure can accept array-based session data without refactor.

### Exit Criteria

- Sidebar is production-ready as a UI foundation for future multi-chat integration.
- User confirms Phase 3 completion.

## Phase 4 - Conversation Thread UI Polish

### Tasks

- Redesign message list with clear user/assistant role differentiation.
- Improve spacing, max-width, typography rhythm, and scroll behavior.
- Add loading/streaming placeholder style consistency.
- Ensure long content wraps safely and code/text blocks remain readable.

### Verification Checklist

- User and assistant messages are visually distinct.
- Conversation panel remains readable on small and large viewports.
- No overflow/layout break for long messages.

### Exit Criteria

- Conversation area matches target quality and usability.
- User confirms Phase 4 completion.

## Phase 5 - Composer Redesign

### Tasks

- Implement sticky/fixed bottom composer behavior appropriate to viewport.
- Add polished input container, send button states, and disabled/loading states.
- Ensure keyboard interaction remains correct (focus, submit behavior, accessibility labels).
- Keep current message submit behavior unchanged unless explicitly required.

### Verification Checklist

- Composer remains reachable and usable on mobile with virtual keyboard constraints considered.
- Send/disabled/loading states are clear.
- Existing submit flow still works correctly.

### Exit Criteria

- Composer meets UX and accessibility expectations across breakpoints.
- User confirms Phase 5 completion.

## Phase 6 - Legacy CSS/Class Cleanup

### Tasks

- Remove old CSS files/imports that were specific to prior chatbot styling.
- Remove obsolete class names that are no longer used.
- Confirm chatbot page has no dependency on legacy custom CSS styles/classes.
- Keep any truly global/shared styles only if still required by non-chatbot screens.

### Verification Checklist

- Search confirms removed/unused style references are cleaned.
- Chatbot page styling is fully driven by Tailwind utilities.
- No visual regressions caused by stale style remnants.

### Exit Criteria

- Cleanup complete and codebase has no dead styling artifacts for chatbot page.
- User confirms Phase 6 completion.

## Phase 7 - QA, Accessibility, and Regression Validation

### Tasks

- Run lint/build/tests relevant to frontend.
- Perform manual responsive QA on key breakpoints.
- Perform keyboard-only and basic screen-reader label checks.
- Validate critical user journey: open page, view history panel, type message, send message, receive response.

### Verification Checklist

- Lint/build/tests pass (or documented known pre-existing issues).
- Responsive checklist passed on mobile/tablet/desktop.
- Accessibility basics pass for focus order, labels, and contrast.

### Exit Criteria

- Redesign is verified and ready for merge.
- User confirms Phase 7 completion.

## Suggested Task Breakdown (PR-Friendly)

1. PR 1: Tailwind v4 setup + shell skeleton.
2. PR 2: Sidebar foundation + responsive toggling.
3. PR 3: Message thread polish + composer redesign.
4. PR 4: CSS cleanup + QA fixes.

## Risk Register and Mitigation

- Risk: Layout regressions during shell refactor.
  - Mitigation: Keep Phase 0 screenshots and compare each phase.
- Risk: Hidden dependency on legacy CSS.
  - Mitigation: Search-driven cleanup and route-level visual smoke tests.
- Risk: Mobile keyboard overlap in composer.
  - Mitigation: Dedicated mobile QA pass in Phase 5.

## Final Acceptance Gate

All items must be true:

- Chatbot page is visually overhauled to ChatGPT-like app shell quality.
- Sidebar exists and is future-ready for multi-chat sessions.
- Tailwind CSS v4 is used for page styling.
- Legacy chatbot CSS/class usage is removed.
- Responsive and accessibility verification checklists are completed.

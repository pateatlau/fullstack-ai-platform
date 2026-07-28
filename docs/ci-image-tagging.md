# CI Image Tagging Convention (Stage C2)

This project publishes container images to GHCR from the GitHub Actions workflow `.github/workflows/build-publish-images.yml`.

## Registry and Image Names

- Registry: `ghcr.io`
- Frontend image: `ghcr.io/<owner>/fullstack-ai-platform-frontend`
- Backend Node.js image: `ghcr.io/<owner>/fullstack-ai-platform-backend-nodejs`
- Backend Python image: `ghcr.io/<owner>/fullstack-ai-platform-backend-python`

`<owner>` is the GitHub repository owner at workflow runtime.

## Tag Types

Each published image may receive the following tags:

- Immutable tag:
  - `sha-<git_sha>`
- Mutable channel tags:
  - `main`
  - `staging`
  - `prod`

## Tag Rules

- On push to `main`:
  - Publish changed service images with `sha-<git_sha>`, `main`, and `staging`.
- On release tag push (`v*` or `release-*`):
  - Publish all service images from the tagged commit with `sha-<git_sha>` and `prod`.

## Provenance Baseline Metadata

Each service build uploads a workflow artifact with:

- service name
- git ref
- commit sha
- image digest
- generated Docker metadata (tags/labels payload)

Artifact names:

- `frontend-build-metadata`
- `backend-nodejs-build-metadata`
- `backend-python-build-metadata`

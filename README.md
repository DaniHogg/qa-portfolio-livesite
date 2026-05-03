# QA Portfolio Live Site

Employer-facing static website that showcases QA automation repositories and the latest test evidence.

The site is designed to republish fresh automated test evidence without manual intervention.

## Site Layout
- `index.html`: business landing page with professional summary and navigation.
- `about.html`: About Me page for work history and career details.
- `portfolio.html`: testing portfolio showcase with tech stack and testing application summaries.
- `dashboard.html`: Automation Test Results dashboard with repository cards and latest run links.
- `project.html`: per-repository detail view for latest status, suite-level results, and run history.
- `data/portfolio-projects.json`: source data for portfolio cards rendered on `portfolio.html`.

## What This Project Does
- Publishes repository cards with latest run status.
- Links each repository card to both the source repository and the latest workflow run evidence.
- Shows stale badges when data is older than 7 days.
- Keeps a rolling history of 5 runs per project.
- Links directly to workflow runs and report artifacts.
- Preserves honest states for passed, failed, skipped, and not-run suites.

## Initial Project Included
- qa-automation-template

## Local Preview
From this folder, run:

python -m http.server 8080

Then open http://localhost:8080

## Data Pipeline
The extractor script converts Allure results into normalized project summaries:

python scripts/extract_qa_template_summary.py \
  --allure-dir ../qa-automation-template/allure-results \
  --output-dir data/projects \
  --run-id local-run \
  --run-url http://localhost/local-run

The extractor now also writes a per-run coverage audit artifact:
- data/projects/<project-id>/coverage-audit.json

Coverage audit includes:
- covered suites
- not-covered suites
- expected test-file counts by suite (for mapped suites)
- notes for not-run/prerequisite-gated suites

## Deployment
GitHub Actions workflow in .github/workflows/update-and-deploy.yml:
- downloads latest report artifacts
- regenerates normalized JSON
- deploys static site to GitHub Pages

Automatic publish behavior:
- scheduled fallback publish runs once a day
- immediate publish can be triggered from the source test repository after fresh artifacts are uploaded

Required repository variables for cross-repo artifact ingestion:
- SOURCE_REPO: owner/repo that produces allure-results
- SOURCE_WORKFLOW: workflow filename in that repo (example: ci.yml)
- SOURCE_BRANCH: optional, defaults to main
- SOURCE_ARTIFACT: optional, defaults to allure-results

Required secret for reliable cross-repo artifact download:
- SOURCE_REPO_TOKEN: fine-grained PAT with Actions read access to the source repository

Optional source-repo automation for immediate publish after daily tests finish:
- In the test repo, set variable PORTFOLIO_SITE_REPO to the live-site repo name (example: danihogg/qa-portfolio-livesite)
- In the test repo, set secret PORTFOLIO_SITE_TOKEN to a fine-grained PAT with Contents/Actions access needed to dispatch the live-site workflow

If SOURCE_REPO or SOURCE_WORKFLOW is not set, the workflow skips ingestion and only deploys currently committed data.

## Daily Automation
- qa-automation-template CI is scheduled to run once a day and upload merged `allure-results`.
- qa-portfolio-live-site can publish in either of two ways:
  - immediately, when the source repo dispatches a `portfolio-results-ready` event after uploading artifacts
  - on its own daily schedule, which acts as a fallback if dispatch is not configured

## Hosting Recommendation
1. GitHub Pages (default)
2. Netlify (alternative)
3. Cloudflare Pages (alternative)

This structure is fully compatible with GitHub Pages because it is a static site that reads JSON from the same repository.

Use a server-backed design only if you need private artifact proxying or authenticated APIs.

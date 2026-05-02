# QA Portfolio Live Site

Employer-facing static website that showcases QA automation repositories and the latest test evidence.

## What This Project Does
- Publishes repository cards with latest run status.
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

## Deployment
GitHub Actions workflow in .github/workflows/update-and-deploy.yml:
- downloads latest report artifacts
- regenerates normalized JSON
- deploys static site to GitHub Pages

Required repository variables for cross-repo artifact ingestion:
- SOURCE_REPO: owner/repo that produces allure-results
- SOURCE_WORKFLOW: workflow filename in that repo (example: ci.yml)
- SOURCE_BRANCH: optional, defaults to main
- SOURCE_ARTIFACT: optional, defaults to allure-results

If SOURCE_REPO or SOURCE_WORKFLOW is not set, the workflow skips ingestion and only deploys currently committed data.

## Hosting Recommendation
1. GitHub Pages (default)
2. Netlify (alternative)
3. Cloudflare Pages (alternative)

Use a server-backed design only if you need private artifact proxying or authenticated APIs.

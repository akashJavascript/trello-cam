# CI workflow (parked)

`tests.yml` belongs in `.github/workflows/`. It is parked here because the GitHub CLI token used to push
lacks the `workflow` scope, and GitHub refuses pushes that add workflow files without it. To enable CI:

```bash
gh auth refresh -h github.com -s workflow
git mv ci/tests.yml .github/workflows/tests.yml && git rm ci/README.md
git commit -m "Enable CI" && git push
```

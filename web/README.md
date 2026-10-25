# Web

An Angular 20 workspace with two apps that share the Aurora look (`shared/`, generated from `../design/tokens.json`):

| App | What | Output |
|---|---|---|
| `site` | The public website, prerendered to static HTML and deployed to Vercel by the release workflow | `dist/site/browser/` |
| `setup` | The Mac setup wizard, served by `digest setup` on 127.0.0.1 and bundled into the recorder wheel | `../recorder/src/workdigest/setup/web/` |

```sh
npm ci
npm run build:site                 # prerender, then scripts/prepare-site.mjs adds install.sh, vercel.json and version.json
npx ng build setup                 # the wizard, into the recorder package
npx ng serve site                  # development server
npx ng test site --watch=false     # unit tests (ChromeHeadless); also `setup`
npx playwright test                # end-to-end on desktop and a Pixel-size phone, with axe
```

`prepare-site.mjs` reads these optional environment variables:

- `SITE_URL`: the site's address, used in `install.sh` and the install command;
- `GITHUB_REPOSITORY`: where the download redirects point;
- `VERSION_JSON`: the release's update file.

The CSP allows no inline scripts, so hydration event replay and critical-CSS inlining are off.

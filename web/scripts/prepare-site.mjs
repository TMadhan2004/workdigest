// After `ng build site`: make dist/site/browser a complete Vercel deployment.
//
// Environment (all optional for a local preview):
//   SITE_URL           https://<the site>                     (default: the placeholder)
//   GITHUB_REPOSITORY  owner/repo, for download and release-notes redirects
//   VERSION_JSON       path to the release's version.json, copied as /version.json
import { copyFileSync, existsSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const out = resolve(here, '../dist/site/browser');
const placeholder = 'https://workdigest.example';
const site = (process.env.SITE_URL || placeholder).replace(/\/$/, '');
const repo = process.env.GITHUB_REPOSITORY || 'OWNER/REPO';

if (!existsSync(join(out, 'index.html'))) {
  console.error('Build the site first: ng build site');
  process.exit(1);
}

// The prerendered page shows the install command for the real address.
const index = join(out, 'index.html');
writeFileSync(index, readFileSync(index, 'utf8').replaceAll(placeholder, site));

// The one-line installer, pointing back at this site.
const installer = readFileSync(resolve(here, '../../install/install.sh'), 'utf8').replaceAll('__SITE_URL__', site);
writeFileSync(join(out, 'install.sh'), installer);

if (process.env.VERSION_JSON) copyFileSync(process.env.VERSION_JSON, join(out, 'version.json'));

const security = [
  { key: 'Content-Security-Policy', value: "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'none'" },
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
  { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
  { key: 'Strict-Transport-Security', value: 'max-age=63072000; includeSubDomains' },
];
const vercel = {
  $schema: 'https://openapi.vercel.sh/vercel.json',
  cleanUrls: true,
  trailingSlash: false,
  redirects: [
    // Always the newest build: GitHub serves the asset of the latest release.
    { source: '/download/android', destination: `https://github.com/${repo}/releases/latest/download/WorkDigest.apk`, permanent: false },
    { source: '/releases', destination: `https://github.com/${repo}/releases`, permanent: false },
  ],
  headers: [
    { source: '/(.*)', headers: security },
    { source: '/install.sh', headers: [{ key: 'Content-Type', value: 'text/plain; charset=utf-8' }, { key: 'Cache-Control', value: 'no-cache' }] },
    { source: '/version.json', headers: [{ key: 'Cache-Control', value: 'no-cache' }, { key: 'Access-Control-Allow-Origin', value: '*' }] },
    { source: '/(.*)\\.(js|css|ttf)', headers: [{ key: 'Cache-Control', value: 'public, max-age=31536000, immutable' }] },
  ],
};
writeFileSync(join(out, 'vercel.json'), JSON.stringify(vercel, null, 2) + '\n');
console.log(`Site ready in ${out} for ${site} (downloads from ${repo}).`);

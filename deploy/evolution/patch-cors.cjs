// Narrow compatibility fix for the exact v2.3.7 digest in Dockerfile.
// Upstream rejects requests without Origin when CORS_ORIGIN is restricted.
// Allow server-to-server traffic; retain the browser-origin allowlist and API auth.
const fs = require('node:fs');
const path = '/evolution/dist/main.js';
const source = fs.readFileSync(path, 'utf8');
const before = 'return c.includes("*")||c.indexOf(r)!==-1?a(null,!0):a(new Error("Not allowed by CORS"))';
const after = 'return typeof r==="undefined"||c.includes("*")||c.indexOf(r)!==-1?a(null,!0):a(new Error("Not allowed by CORS"))';
if (source.split(before).length !== 2) {
  throw new Error('Evolution CORS patch does not match this image; review before upgrading.');
}
fs.writeFileSync(path, source.replace(before, after));

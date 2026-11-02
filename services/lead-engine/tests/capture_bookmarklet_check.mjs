// Validates the bookmarklet generator in static/capture.html without opening a browser.
//
// The bookmarklet is the only part of the capture path that cannot be exercised by the Python
// suites: it runs on a third-party page. This check parses the generator, rebuilds the bookmarklet
// with a test origin and token, decodes it, and syntax-checks the result. It also asserts the payload
// shape matches what /api/enrich/identity expects.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "..", "static", "capture.html"), "utf8");

const failures = [];
const check = (name, ok, detail = "") => {
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  " + detail : ""}`);
  if (!ok) failures.push(name);
};

check("capture page has a bookmarklet generator", /function booklet\(/.test(html));
check("generator is wired to a build button", /getElementById\('build'\)/.test(html));

// Pull the template literal out of booklet() and render it with test values.
const bodyMatch = html.match(/const js = `([\s\S]*?)`;/);
check("generator template found", Boolean(bodyMatch));

let js = "";
if (bodyMatch) {
  js = bodyMatch[1]
    .replaceAll("${JSON.stringify(token)}", JSON.stringify("test-token-123"))
    .replaceAll("${JSON.stringify(origin)}", JSON.stringify("http://127.0.0.1:8500"));
  // the template contains \\n and \\s escapes meant for the generated code
  js = js.replaceAll("\\\\n", "\\n").replaceAll("\\\\s", "\\s");
}

let syntaxOk = true;
let syntaxErr = "";
try {
  new Function(js);           // parse only, never executed
} catch (err) {
  syntaxOk = false;
  syntaxErr = err.message;
}
check("bookmarklet body is valid JavaScript", syntaxOk, syntaxErr);

check("posts to /api/enrich/identity", js.includes("/api/enrich/identity"));
check("sends the Authorization header", /Authorization/.test(js));
check("carries a token", js.includes("test-token-123"));
check("sends linkedinUrl", js.includes("linkedinUrl"));
check("sends firstName", js.includes("firstName"));
check("sends companyName", js.includes("companyName"));
check("reads only the current page", js.includes("location.href"));
check("reports work email back", js.includes("workEmail"));
check("reports phone back", js.includes("phone"));

// A generator that emits a bookmarklet without the javascript: scheme silently does nothing.
check("generator prefixes javascript:", /return 'javascript:' \+ encodeURIComponent\(js\)/.test(html));

console.log("");
if (failures.length) {
  console.log(`${failures.length} check(s) failed`);
  process.exit(1);
}
console.log("capture bookmarklet OK");

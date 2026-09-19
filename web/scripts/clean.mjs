// Remove the previous build's hashed files (but keep frontend/legacy, the old UI).
import { rmSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const out = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../frontend");
for (const name of ["assets", "index.html", "favicon.svg"]) {
  const target = path.join(out, name);
  if (existsSync(target)) rmSync(target, { recursive: true, force: true });
}

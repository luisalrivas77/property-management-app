// The uploaded originals of the run are test data in the throwaway database's store: remove
// them with it. (The database itself is dropped by `scripts/e2e-db.sh down`.)
import { rm } from "node:fs/promises";

import { blobRoot } from "./artifacts";

export default async function globalTeardown(): Promise<void> {
  await rm(blobRoot, { recursive: true, force: true });
}

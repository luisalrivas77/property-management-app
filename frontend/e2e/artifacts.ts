// Where an e2e run writes its report, traces and the API's blob store: outside the source
// tree (the system temp directory unless HAUSBUCH_E2E_ARTIFACTS says otherwise), so neither
// `eslint .` nor git ever sees them.
import os from "node:os";
import path from "node:path";

export const artifactsDir = process.env.HAUSBUCH_E2E_ARTIFACTS ?? path.join(os.tmpdir(), "hausbuch-e2e");

/** The API's LocalBlobStore root during the run; removed by the global teardown. */
export const blobRoot = path.join(artifactsDir, "blobs");

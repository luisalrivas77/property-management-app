"use client";

// The card „Nebenkostenpauschalen“ (phase 4 D155) on the property page beside the tenancies
// (phase 10 §7.6; on Planung › Immobilie until then): GET …/flat-rate-check, the costs when each
// Pauschale was set against today, per tenancy its share and verdict, and the Pauschale coverage
// of the shown year from GET …/performance/actuals. The card is components/planning.tsx's own,
// unchanged; every figure is the server's. It needs no rule set, so it shows when the actuals are
// refused (without the year's coverage line), and nothing when no tenancy has a Pauschale.
// A link to `/properties/<id>#nebenkostenpauschalen` (a to-do's „Kosten und Belege“) scrolls
// to it once it has loaded.
import { useCallback, useEffect } from "react";

import { api, ok } from "@/api/client";

import { FLAT_RATE_CARD_ID } from "./flatRate";
import { FlatRateCard } from "./planning";
import { Loading } from "./ui";
import { useLoad } from "./useLoad";

export function PropertyFlatRateCard({ propertyId }: { propertyId: string }) {
  const load = useCallback(async () => {
    const [check, actuals] = await Promise.all([
      ok(api.GET("/api/properties/{property_id}/flat-rate-check", { params: { path: { property_id: propertyId } } })),
      // The year's coverage line only; refused (no rule set) or failed, the card shows without it.
      ok(api.GET("/api/properties/{property_id}/performance/actuals", { params: { path: { property_id: propertyId } } })).catch(
        () => null,
      ),
    ]);
    return { check, years: actuals?.years };
  }, [propertyId]);
  const state = useLoad(load);
  const loaded = state.data !== undefined;
  useEffect(() => {
    if (loaded && window.location.hash === `#${FLAT_RATE_CARD_ID}`) {
      document.getElementById(FLAT_RATE_CARD_ID)?.scrollIntoView({ block: "start" });
    }
  }, [loaded]);
  // As on Planung: nothing while it loads (most properties have no Pauschale), the error with its
  // retry when the check fails.
  if (state.data === undefined && state.error == null) return null;
  return (
    <Loading state={state}>
      {({ check, years }) => <FlatRateCard check={check} actualYears={years} propertyId={propertyId} />}
    </Loading>
  );
}

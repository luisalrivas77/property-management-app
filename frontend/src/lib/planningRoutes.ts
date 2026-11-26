// Where Planung's places are since phase 10 wave 3 (a) (§7.9 (a), D288): Planung left the menu;
// its Budget and Reserve are Haushalt's tabs, „Anlegen“ is the foot of Haushalt › Budget („Was
// mit dem Rest passiert“), „Vermögen“ (the net worth on typed rates) is on Anlagen › Übersicht,
// and Immobilie on Anlagen › Immobilie. Since §7.10 (a) (D299) the panel „Annahmen“ is gone: the
// spending, income and one-off levers are Haushalt › Durchspielen, and the others sit beside the
// figure they act on, each saving on its own (the reserve target on Reserve `#reserve-ziel`, the
// buffer and the savings plans on Budget `#puffer`, the rates on Anlagen `#rendite`). An old
// `#d-annahmen-<group>` link leads to the group's new place (`assumptionsHref`).
//
// Every old link still works: /planning is a redirect page that sends each URL through
// `planningTarget`, and a to-do whose href the server writes under rules/plan/ (those files feed
// rule_version(), so their hrefs stay as they are: `/planning?tab=haushalt`, its
// `#ausgaben-durchspielen`, and `/planning` for the property's scenario) is mapped by
// `planTodoTarget` where it is shown.

/** The groups of the former „Annahmen“ panel, which old links and to-dos still name. */
export const ASSUMPTION_GROUPS = ["reserve", "puffer", "einkommen", "zahlungen", "sparplaene", "rendite", "ausgaben"] as const;
export type AssumptionGroup = (typeof ASSUMPTION_GROUPS)[number];

/** The detail panel's kind „Annahmen“ had (lib/detailHash.ts): an old `#d-annahmen-<group>`
 * hash is sent to the group's new place. */
export const ASSUMPTIONS = "annahmen";

/** The levers beside their figures (§7.10 (a) 7): each an anchor that opens its form. */
export const RESERVE_LEVER = "reserve-ziel";
export const BUFFER_LEVER = "puffer";
export const RATE_LEVER = "rendite";

export function isAssumptionGroup(value: string | null): value is AssumptionGroup {
  return value !== null && (ASSUMPTION_GROUPS as readonly string[]).includes(value);
}

/** The Haushalt tabs (D288; Durchspielen since §7.10 (a), D299). */
export const HOUSEHOLD_TABS = ["monat", "budget", "ziel", "reserve", "durchspielen"] as const;
export type HouseholdTab = (typeof HOUSEHOLD_TABS)[number];

export const HOUSEHOLD_BUDGET = "/household?tab=budget";
export const HOUSEHOLD_RESERVE = "/household?tab=reserve";
export const HOUSEHOLD_GOAL = "/household?tab=ziel";
/** „Euer Plan“ under Haushalt › Ziel (phase 10 §7.11 (h), D306). */
export const HOUSEHOLD_PLAN_PAGE = "/household/plan";
/** Haushalt › Durchspielen, the what-if (§7.10 (a), D299). */
export const HOUSEHOLD_PLAY = "/household?tab=durchspielen";
/** The foot of Haushalt › Budget, what was Planung › Anlegen. */
export const REST_ANCHOR = "was-mit-dem-rest";
/** The net worth on typed rates on Anlagen › Übersicht, what was Planung › Vermögen. */
export const PROJECTION_ANCHOR = "annahme";

/** Where a group of the former panel „Annahmen“ is now (§7.10 (a), D299): the spending, income
 * and one-offs on Haushalt › Durchspielen, the reserve target and its accounts on Reserve, the
 * buffer and the savings plans on Budget, the rates on Anlagen. `_path` is the page the link was
 * written for; the place no longer depends on it. */
export function assumptionsHref(_path: string, group: AssumptionGroup): string {
  switch (group) {
    case "reserve":
      return `${HOUSEHOLD_RESERVE}#${RESERVE_LEVER}`;
    case "puffer":
    case "sparplaene":
      return `${HOUSEHOLD_BUDGET}#${BUFFER_LEVER}`;
    case "rendite":
      return `/assets#${RATE_LEVER}`;
    case "einkommen":
    case "zahlungen":
    case "ausgaben":
      return HOUSEHOLD_PLAY;
  }
}

/** An old `#d-annahmen-<group>` hash (a bookmark, a link from before §7.10 (a)) as the group's
 * new place, or null for any other hash. */
export function staleAssumptionsTarget(hash: string, path: string): string | null {
  const prefix = `#d-${ASSUMPTIONS}-`;
  if (!hash.startsWith(prefix)) return null;
  const group = hash.slice(prefix.length);
  return assumptionsHref(path, isAssumptionGroup(group) ? group : "reserve");
}

/** The query parameters Anlagen › Immobilie reads: the property and the projection's levers. */
const PROPERTY_PARAMS = ["immobilie", "szenario", "wertsteigerung", "mieterhoehung", "verkauf"] as const;

/** Anlagen › Immobilie, keeping the property and its levers from an old Planung link. */
export function propertyPlanningHref(search?: URLSearchParams): string {
  const query = new URLSearchParams({ tab: "immobilie" });
  for (const name of PROPERTY_PARAMS) {
    const value = search?.get(name) ?? null;
    if (value !== null) query.set(name, value);
  }
  return `/assets?${query.toString()}`;
}

/** The hash an old Planung link carried, as a group of the „Annahmen“ panel: „Ausgaben
 * durchspielen“, „Weitere Annahmen“, and the old scenario sheet (`#d-scenario-…`), which opens at
 * the place's own group. */
function groupOfOldHash(hash: string, section: string | null): AssumptionGroup | null {
  if (hash === "#ausgaben-durchspielen") return "ausgaben";
  if (hash === "#weitere-annahmen") return "einkommen";
  if (hash.startsWith("#d-scenario-")) return section === "reserve" ? "reserve" : "ausgaben";
  return null;
}

/** Budget's own panels (the targets, a line's codes) keep their hash on Haushalt › Budget. */
function isBudgetPanelHash(hash: string): boolean {
  return hash.startsWith("#d-goals-") || hash.startsWith("#d-budget-");
}

/**
 * Where an old `/planning…` URL lands now (D266, §7.9 (a) 6): the bare route and `?section=budget`
 * (and phase 9's `?tab=haushalt`) on Haushalt › Budget, `reserve` on Haushalt › Reserve, `anlegen`
 * on Haushalt › Budget at „Was mit dem Rest passiert“, `vermoegen` on Anlagen › Übersicht at the
 * projection, `immobilie` and any link naming a property on Anlagen › Immobilie. `hash` is the
 * old URL's hash (`#ausgaben-durchspielen`, `#weitere-annahmen`, `#annahme`, the old scenario
 * sheet `#d-scenario-…`, Budget's `#d-goals-…` and `#d-budget-…`).
 */
export function planningTarget(search: URLSearchParams, hash = ""): string {
  const section = search.get("section");
  if (section === "immobilie" || (section === null && search.get("immobilie") !== null)) return propertyPlanningHref(search);
  if (section === "vermoegen") {
    return hash === `#${PROJECTION_ANCHOR}` || hash.startsWith("#d-scenario-")
      ? assumptionsHref("/assets", "rendite")
      : `/assets#${PROJECTION_ANCHOR}`;
  }
  const page = section === "reserve" ? HOUSEHOLD_RESERVE : HOUSEHOLD_BUDGET;
  const group = groupOfOldHash(hash, section);
  if (group) return assumptionsHref(page, group);
  if (isBudgetPanelHash(hash)) return `${HOUSEHOLD_BUDGET}${hash}`;
  if (section === "anlegen") return `${HOUSEHOLD_BUDGET}#${REST_ANCHOR}`;
  return page;
}

/** A server to-do's href from rules/plan/ (left unchanged, D271): where it leads now. Phase 9's
 * `/planning?tab=haushalt` leads to the place of the group its code names, the bare `/planning`
 * is the property's scenario (`PROPERTY_HREF`) and leads to Anlagen › Immobilie. Any other href
 * is returned as it is. */
export function planTodoTarget(href: string, code: string, page: string): string {
  if (href === "/planning") return propertyPlanningHref();
  if (!href.startsWith("/planning")) return href;
  const [pathAndQuery, hash = ""] = href.split("#", 2);
  const query = new URLSearchParams(pathAndQuery!.split("?", 2)[1] ?? "");
  if (query.get("tab") === "haushalt") {
    const group = groupOfOldHash(hash ? `#${hash}` : "", null) ?? (code === "rate_missing" ? "rendite" : "reserve");
    return assumptionsHref(page, group);
  }
  return planningTarget(query, hash ? `#${hash}` : "");
}

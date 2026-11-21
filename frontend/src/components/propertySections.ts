// The property page's six sections (phase 10, mockup 25) and the cards inside them. Until then
// the page had ten tabs; each former tab is now a card with a stable id (`card-<old key>`), and
// an old `?tab=<old key>` link (a bookmark, a stored link, D266: routes are kept) opens the
// card's section and scrolls the card into view. New links name the section and, for a card
// that is not the first of its section, the card as a hash: `?tab=kauf#card-aufteilung`.

export type PropertySection = "uebersicht" | "kauf" | "darlehen" | "unterlagen" | "dokumente" | "verlauf";

export const PROPERTY_SECTIONS: readonly PropertySection[] = ["uebersicht", "kauf", "darlehen", "unterlagen", "dokumente", "verlauf"];

/** A card, named by the key of the tab it was until phase 10. */
export type PropertyCard =
  | "stammdaten"
  | "eigentuemer"
  | "einheiten"
  | "nebenkosten"
  | "aufteilung"
  | "darlehen"
  | "unterlagen"
  | "dokumente"
  | "verlauf"
  | "protokoll";

/** Each card's section, in the order the section draws its cards. */
const CARDS: Record<PropertySection, readonly PropertyCard[]> = {
  uebersicht: ["stammdaten", "eigentuemer", "einheiten"],
  kauf: ["nebenkosten", "aufteilung"],
  darlehen: ["darlehen"],
  unterlagen: ["unterlagen"],
  dokumente: ["dokumente"],
  verlauf: ["verlauf", "protokoll"],
};

function sectionOf(card: PropertyCard): PropertySection {
  return PROPERTY_SECTIONS.find((section) => CARDS[section].includes(card)) ?? "uebersicht";
}

function isSection(value: string): value is PropertySection {
  return PROPERTY_SECTIONS.some((section) => section === value);
}

function isCard(value: string): value is PropertyCard {
  return PROPERTY_SECTIONS.some((section) => CARDS[section].some((card) => card === value));
}

/** The element id of a card, e.g. `card-nebenkosten`. */
export function propertyCardId(card: PropertyCard): string {
  return `card-${card}`;
}

/** The card a `#card-…` hash names, or null. */
export function propertyCardOfHash(hash: string): PropertyCard | null {
  const name = hash.startsWith("#card-") ? hash.slice("#card-".length) : "";
  return isCard(name) ? name : null;
}

/**
 * The section a `?tab=` value opens, and the card to scroll to: a section key opens itself;
 * an old tab key opens its card's section and names the card. Anything else opens Übersicht.
 */
export function resolvePropertyTab(value: string | null): { section: PropertySection; card: PropertyCard | null } {
  if (value && isSection(value)) return { section: value, card: null };
  if (value && isCard(value)) return { section: sectionOf(value), card: value };
  return { section: "uebersicht", card: null };
}

/** The first card of a section, which needs no scroll: it is at the top. */
export function isFirstCard(card: PropertyCard): boolean {
  return CARDS[sectionOf(card)][0] === card;
}

/** The link to a card of a property page: its section, plus the card's hash unless it is first. */
export function propertyCardHref(propertyId: string, card: PropertyCard, query = ""): string {
  const section = sectionOf(card);
  const extra = query ? `&${query}` : "";
  return `/properties/${propertyId}?tab=${section}${extra}${isFirstCard(card) ? "" : `#${propertyCardId(card)}`}`;
}

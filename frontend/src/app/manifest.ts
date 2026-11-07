import type { MetadataRoute } from "next";

// Installable on the phone (§9): standalone display, no service worker and no offline mode.
// The home-screen shortcut opens the capture page (phase-2.md §9); start_url stays "/".
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Hausbuch",
    short_name: "Hausbuch",
    description: "Immobilien, Mietverhältnisse, Darlehen und Belege an einem Ort.",
    lang: "de",
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: "#f4f5f2",
    theme_color: "#1f6f54",
    shortcuts: [{ name: "Beleg fotografieren", short_name: "Fotografieren", url: "/capture" }],
    icons: [
      { src: "/icons/192", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/512", sizes: "512x512", type: "image/png", purpose: "any" },
    ],
  };
}

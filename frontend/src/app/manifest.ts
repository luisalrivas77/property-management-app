import type { MetadataRoute } from "next";

// Installable on the phone (§9): standalone display, no service worker and no offline mode
// until phase 2's capture flow needs them.
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Hausbuch",
    short_name: "Hausbuch",
    description: "Immobilien, Mietverhältnisse, Darlehen und Belege an einem Ort.",
    lang: "de",
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: "#f6f7f8",
    theme_color: "#1f4e5f",
    icons: [
      { src: "/icons/192", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/512", sizes: "512x512", type: "image/png", purpose: "any" },
    ],
  };
}

import { ImageResponse } from "next/og";

/** The app icon as a PNG: a green square with an "H". Rendered once at build time. */
export function appIcon(size: number): ImageResponse {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#1f6f54",
          color: "#ffffff",
          fontSize: Math.round(size * 0.6),
          fontWeight: 700,
        }}
      >
        H
      </div>
    ),
    { width: size, height: size },
  );
}

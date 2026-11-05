// The PWA manifest's PNG icons (/icons/192, /icons/512), prerendered at build time.
import { appIcon } from "@/lib/appIcon";

const MANIFEST_ICON_SIZES = ["192", "512"] as const; // keep in step with app/manifest.ts

export const dynamic = "force-static";
export const dynamicParams = false;

export function generateStaticParams() {
  return MANIFEST_ICON_SIZES.map((size) => ({ size }));
}

export async function GET(_request: Request, { params }: { params: Promise<{ size: string }> }) {
  const { size } = await params;
  return appIcon(Number(size));
}

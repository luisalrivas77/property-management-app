// Home-screen icon for iOS (<link rel="apple-touch-icon">), which ignores the manifest's icons.
import { appIcon } from "@/lib/appIcon";

export const size = { width: 180, height: 180 };
export const contentType = "image/png";

export default function AppleIcon() {
  return appIcon(size.width);
}

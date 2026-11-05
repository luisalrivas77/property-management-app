import type { Metadata } from "next";

import { Enroll } from "./Enroll";

export const metadata: Metadata = { title: "Passkey einrichten" };

export default function EnrollPage() {
  return <Enroll />;
}

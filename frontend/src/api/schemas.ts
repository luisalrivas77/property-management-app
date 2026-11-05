// Named aliases for the API's schema types, so pages never spell out the generated nesting.
// Chunk 3b: import from ./types instead once it is generated from the real API.
import type { components } from "./interim-auth-types";

type Schemas = components["schemas"];

export type ErrorResponse = Schemas["ErrorResponse"];
export type ErrorKind = Schemas["ErrorKind"];
export type FieldError = Schemas["FieldError"];
export type Me = Schemas["Me"];
export type AppUser = Schemas["AppUser"];
export type Passkey = Schemas["Passkey"];

export type Values = Record<string, unknown>;
export interface Field {
  key: string;
  label: string;
  unit: string;
  units: string[];
  group: string;
  kind: string;
}
export interface Evaluation {
  car: Values;
  rpm: number[];
  torque: number[];
  power: number[];
  peak_power: number;
  peak_torque: number;
  overall: number[];
  aero_efficiency: number;
  top_speed_kmh: number;
  warnings: string[];
}
export interface Preset {
  version: 1;
  base: Values;
  csv: Values;
  manual: Values;
}
export async function api(
  path: string,
  data?: unknown,
  method?: string,
): Promise<Response> {
  const isForm = data instanceof FormData;
  const response = await fetch(`/api${path}`, {
    method: method || (data === undefined ? "GET" : "POST"),
    credentials: "same-origin",
    headers: {
      "X-Requested-With": "car-builder",
      ...(!isForm && data !== undefined
        ? { "Content-Type": "application/json" }
        : {}),
    },
    body: data === undefined ? undefined : isForm ? data : JSON.stringify(data),
  });
  if (!response.ok) {
    const body = await response
      .json()
      .catch(() => ({ detail: response.statusText }));
    const detail = body.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((e: { msg: string }) => e.msg).join("; ")
          : "Request failed",
    );
  }
  return response;
}
export async function json<T>(
  path: string,
  data?: unknown,
  method?: string,
): Promise<T> {
  return (await api(path, data, method)).json() as Promise<T>;
}
export function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export async function download(path: string, data: unknown) {
  const response = await api(path, data);
  const name =
    response.headers
      .get("Content-Disposition")
      ?.match(/filename="([^"]+)"/)?.[1] || "car.OLVeh";
  saveBlob(await response.blob(), name);
}
export function display(value: unknown): string {
  return value === null || value === undefined
    ? ""
    : Array.isArray(value)
      ? value.join(", ")
      : String(value);
}
export function same(a: unknown, b: unknown) {
  return JSON.stringify(a) === JSON.stringify(b);
}

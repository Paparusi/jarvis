export const API_BASE = "";  // Uses Next.js proxy

export const fetcher = (url: string) => fetch(url).then((r) => r.json());

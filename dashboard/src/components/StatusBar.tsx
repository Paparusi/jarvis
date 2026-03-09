"use client";

import useSWR from "swr";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export default function StatusBar() {
  const { data } = useSWR("/api/status", fetcher, { refreshInterval: 30000 });

  return (
    <footer className="h-8 bg-[#12121a] border-t border-[#2a2a3e] px-4 flex items-center text-xs text-gray-500 gap-4">
      <span className="flex items-center gap-1.5">
        <span className={`w-1.5 h-1.5 rounded-full ${data ? "bg-green-400" : "bg-red-400"}`} />
        {data ? "Connected" : "Disconnected"}
      </span>
      {data && (
        <>
          <span>{data.cloud_model}</span>
          <span>{data.tools} tools</span>
          <span>{data.skills} skills</span>
          <span>Cache: {data.cache_entries}</span>
        </>
      )}
    </footer>
  );
}

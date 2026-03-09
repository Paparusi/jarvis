"use client";

import { useState } from "react";

interface Approval {
  id: string;
  symbol: string;
  direction: string;
  order_type: string;
  price: number;
  sl: number;
  tp1: number;
  tp2?: number;
  lot: number;
  risk_pct: number;
  risk_usd: number;
  confluence_score: number;
  analysis: string;
  smc_summary?: string;
  created_at: string;
}

interface Props {
  approval: Approval;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
}

export default function ApprovalCard({ approval, onApprove, onReject }: Props) {
  const isBuy = approval.direction === "buy";
  const [showDetail, setShowDetail] = useState(false);
  const [loading, setLoading] = useState(false);

  const handleApprove = async () => {
    setLoading(true);
    await onApprove(approval.id);
    setLoading(false);
  };

  const handleReject = async () => {
    setLoading(true);
    await onReject(approval.id);
    setLoading(false);
  };

  return (
    <div className={`border rounded-xl p-4 ${
      isBuy ? "border-green-500/30 bg-green-500/5" : "border-red-500/30 bg-red-500/5"
    }`}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className={`text-lg font-bold ${isBuy ? "text-green-400" : "text-red-400"}`}>
            {approval.order_type.toUpperCase()}
          </span>
          <span className="text-gray-400">{approval.symbol}</span>
        </div>
        <span className="text-xs text-gray-500">
          Score: {approval.confluence_score}/100
        </span>
      </div>

      <div className="grid grid-cols-2 gap-2 text-sm mb-3">
        <div><span className="text-gray-500">Entry:</span> {approval.price}</div>
        <div><span className="text-gray-500">Lot:</span> {approval.lot}</div>
        <div className="text-red-400"><span className="text-gray-500">SL:</span> {approval.sl}</div>
        <div className="text-green-400"><span className="text-gray-500">TP1:</span> {approval.tp1}</div>
        <div><span className="text-gray-500">Risk:</span> {approval.risk_pct}% (${(approval.risk_usd ?? 0).toFixed(0)})</div>
        {approval.tp2 && <div className="text-green-400"><span className="text-gray-500">TP2:</span> {approval.tp2}</div>}
      </div>

      <p className="text-xs text-gray-400 mb-3 line-clamp-3">{approval.analysis}</p>

      <div className="flex gap-2">
        <button onClick={handleApprove} disabled={loading}
          className="flex-1 bg-green-600 hover:bg-green-700 disabled:opacity-50 rounded-lg py-2 text-sm font-medium transition-colors">
          {loading ? "..." : "Approve"}
        </button>
        <button onClick={handleReject} disabled={loading}
          className="flex-1 bg-red-600 hover:bg-red-700 disabled:opacity-50 rounded-lg py-2 text-sm font-medium transition-colors">
          {loading ? "..." : "Reject"}
        </button>
        <button onClick={() => setShowDetail(!showDetail)}
          className="bg-[#1a1a2e] border border-[#2a2a3e] hover:bg-[#2a2a3e] rounded-lg px-3 py-2 text-sm transition-colors">
          Detail
        </button>
      </div>

      {showDetail && (
        <div className="mt-3 p-3 bg-[#0a0a0f] rounded-lg text-xs text-gray-400 whitespace-pre-wrap">
          {approval.analysis}
          {approval.smc_summary && `\n\nSMC: ${approval.smc_summary}`}
        </div>
      )}
    </div>
  );
}

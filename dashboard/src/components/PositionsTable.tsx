interface Position {
  ticket: number;
  type: string;
  volume: number;
  symbol: string;
  price_open: number;
  sl: number;
  tp: number;
  profit: number;
}

interface Props {
  positions: Position[];
}

export default function PositionsTable({ positions }: Props) {
  if (positions.length === 0) {
    return <p className="text-gray-500 text-sm">No open positions</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-gray-500 border-b border-[#2a2a3e]">
            <th className="text-left py-2 px-3">Ticket</th>
            <th className="text-left py-2 px-3">Type</th>
            <th className="text-right py-2 px-3">Volume</th>
            <th className="text-right py-2 px-3">Entry</th>
            <th className="text-right py-2 px-3">SL</th>
            <th className="text-right py-2 px-3">TP</th>
            <th className="text-right py-2 px-3">P&L</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => (
            <tr key={p.ticket} className="border-b border-[#1a1a2e] hover:bg-white/5">
              <td className="py-2 px-3 text-gray-400">{p.ticket}</td>
              <td className={`py-2 px-3 font-medium ${p.type === "buy" ? "text-green-400" : "text-red-400"}`}>
                {p.type.toUpperCase()}
              </td>
              <td className="py-2 px-3 text-right">{p.volume}</td>
              <td className="py-2 px-3 text-right">{p.price_open}</td>
              <td className="py-2 px-3 text-right text-red-400">{p.sl || "—"}</td>
              <td className="py-2 px-3 text-right text-green-400">{p.tp || "—"}</td>
              <td className={`py-2 px-3 text-right font-medium ${p.profit >= 0 ? "text-green-400" : "text-red-400"}`}>
                ${p.profit.toFixed(2)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

import { LucideIcon } from "lucide-react";

interface Props {
  title: string;
  value: string | number;
  icon: LucideIcon;
  subtitle?: string;
  color?: string;
}

export default function StatCard({ title, value, icon: Icon, subtitle, color = "blue" }: Props) {
  const colors: Record<string, string> = {
    blue: "from-blue-500/20 to-blue-600/5 text-blue-400",
    green: "from-green-500/20 to-green-600/5 text-green-400",
    purple: "from-purple-500/20 to-purple-600/5 text-purple-400",
    yellow: "from-yellow-500/20 to-yellow-600/5 text-yellow-400",
    red: "from-red-500/20 to-red-600/5 text-red-400",
  };

  return (
    <div className={`bg-gradient-to-br ${colors[color]} rounded-xl p-4 border border-white/5`}>
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm text-gray-400">{title}</span>
        <Icon size={18} className="opacity-50" />
      </div>
      <div className="text-2xl font-bold">{value}</div>
      {subtitle && <div className="text-xs text-gray-500 mt-1">{subtitle}</div>}
    </div>
  );
}

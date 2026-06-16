import { TrendingUp, TrendingDown } from "lucide-react";

export function BullBearCase({
  bull,
  bear,
}: {
  bull: string[];
  bear: string[];
}) {
  if ((!bull || bull.length === 0) && (!bear || bear.length === 0)) return null;

  return (
    <div className="bg-dark-card border border-dark-border rounded-2xl p-6">
      <h2 className="font-bold text-lg mb-4">Bull &amp; Bear Case</h2>
      <div className="grid md:grid-cols-2 gap-6">
        <div>
          <div className="flex items-center gap-2 mb-3 text-bull">
            <TrendingUp size={18} />
            <h3 className="font-semibold text-sm">Bull Case</h3>
          </div>
          {bull.length > 0 ? (
            <ul className="space-y-2">
              {bull.map((s, i) => (
                <li key={i} className="flex items-start gap-2 text-sm">
                  <span className="shrink-0 mt-1.5 w-1.5 h-1.5 rounded-full bg-bull" />
                  <span className="text-gray-300">{s}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-gray-500 text-sm">No strong positive factors detected.</p>
          )}
        </div>

        <div>
          <div className="flex items-center gap-2 mb-3 text-bear">
            <TrendingDown size={18} />
            <h3 className="font-semibold text-sm">Bear Case</h3>
          </div>
          {bear.length > 0 ? (
            <ul className="space-y-2">
              {bear.map((s, i) => (
                <li key={i} className="flex items-start gap-2 text-sm">
                  <span className="shrink-0 mt-1.5 w-1.5 h-1.5 rounded-full bg-bear" />
                  <span className="text-gray-300">{s}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-gray-500 text-sm">No significant risk factors detected.</p>
          )}
        </div>
      </div>
      <p className="text-xs text-gray-600 mt-4 pt-3 border-t border-dark-border">
        Each point is derived from a measured factor value — not generative text.
      </p>
    </div>
  );
}

"use client";
import { useState, useRef } from "react";
import { Upload, FileText, X, AlertTriangle, Check } from "lucide-react";
import clsx from "clsx";
import { importPortfolioHoldings, Market } from "@/utils/api";
import { parseHoldingsFile, parseHoldingsText, ParsedHolding, ImportRowError } from "@/utils/portfolioImport";

interface ExistingHolding { symbol: string; market: Market; qty: number; avgPrice: number }

export function ImportPortfolioModal({
  userId,
  defaultMarket,
  existingHoldings,
  onClose,
  onImported,
}: {
  userId: string;
  defaultMarket: Market;
  existingHoldings: ExistingHolding[];
  onClose: () => void;
  onImported: () => void;
}) {
  const [mode, setMode] = useState<"file" | "paste">("file");
  const [market, setMarket] = useState<Market>(defaultMarket);
  const [pasteText, setPasteText] = useState("");
  const [parsed, setParsed] = useState<ParsedHolding[] | null>(null);
  const [parseErrors, setParseErrors] = useState<ImportRowError[]>([]);
  const [excluded, setExcluded] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [successMsg, setSuccessMsg] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  const existingMap = new Map(existingHoldings.filter(h => h.market === market).map(h => [h.symbol, h]));

  const handleFile = async (file: File) => {
    setSubmitError("");
    try {
      const result = await parseHoldingsFile(file, market);
      setParsed(result.holdings);
      setParseErrors(result.errors);
      setExcluded(new Set());
    } catch {
      setSubmitError("Couldn't read that file — make sure it's a valid CSV or Excel export.");
    }
  };

  const handlePaste = async () => {
    setSubmitError("");
    const result = await parseHoldingsText(pasteText, market);
    setParsed(result.holdings);
    setParseErrors(result.errors);
    setExcluded(new Set());
  };

  const toggleExclude = (i: number) => {
    setExcluded(prev => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i); else next.add(i);
      return next;
    });
  };

  const confirmImport = async () => {
    if (!parsed) return;
    const toImport = parsed.filter((_, i) => !excluded.has(i));
    if (toImport.length === 0) { setSubmitError("No rows selected to import."); return; }

    setBusy(true);
    setSubmitError("");
    try {
      const result = await importPortfolioHoldings(userId, market, toImport);
      onImported();
      setSuccessMsg(
        `Added ${result.added}, updated ${result.updated}` +
        (result.cleaned_up > 0 ? `, removed ${result.cleaned_up} stale duplicate${result.cleaned_up !== 1 ? "s" : ""} from earlier symbol corrections.` : ".")
      );
    } catch {
      setSubmitError("Import failed — check your connection and try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" onClick={onClose}>
      <div
        onClick={(e) => e.stopPropagation()}
        className="bg-dark-card border border-dark-border rounded-2xl w-full max-w-2xl max-h-[85vh] overflow-y-auto"
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-dark-border">
          <h2 className="font-bold text-lg">Import Portfolio</h2>
          <button onClick={onClose} className="text-gray-500 hover:text-white">
            <X size={18} />
          </button>
        </div>

        <div className="p-5 space-y-4">
          <p className="text-xs text-gray-500">
            Upload a holdings export from your broker (Zerodha, Groww, Upstox, etc.) or paste rows directly.
            Matching symbols update their quantity/avg price; new symbols get added. Existing holdings not in the
            import are left untouched.
          </p>

          {/* Market + mode selectors */}
          <div className="flex items-center gap-3 flex-wrap">
            <div className="flex items-center gap-1">
              <label className="text-xs text-gray-400">Market</label>
              <div className="flex gap-1">
                {(["IN", "US"] as Market[]).map(m => (
                  <button key={m} onClick={() => { setMarket(m); setParsed(null); setSuccessMsg(""); }}
                    className={clsx("px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors",
                      market === m ? "bg-brand-500 text-white border-brand-500" : "bg-dark-bg border-dark-border text-gray-400 hover:text-white")}>
                    {m === "US" ? "🇺🇸 US" : "🇮🇳 IN"}
                  </button>
                ))}
              </div>
            </div>
            <div className="flex gap-1 ml-auto">
              <button onClick={() => { setMode("file"); setParsed(null); setSuccessMsg(""); }}
                className={clsx("flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors",
                  mode === "file" ? "bg-brand-500 text-white border-brand-500" : "bg-dark-bg border-dark-border text-gray-400 hover:text-white")}>
                <Upload size={13} /> Upload File
              </button>
              <button onClick={() => { setMode("paste"); setParsed(null); setSuccessMsg(""); }}
                className={clsx("flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors",
                  mode === "paste" ? "bg-brand-500 text-white border-brand-500" : "bg-dark-bg border-dark-border text-gray-400 hover:text-white")}>
                <FileText size={13} /> Paste Text
              </button>
            </div>
          </div>

          {mode === "file" ? (
            <div
              onClick={() => fileInputRef.current?.click()}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files?.[0]; if (f) handleFile(f); }}
              className="border-2 border-dashed border-dark-border rounded-xl p-8 text-center cursor-pointer hover:border-brand-500/50 transition-colors"
            >
              <Upload size={24} className="text-gray-500 mx-auto mb-2" />
              <p className="text-sm text-gray-400">Click to choose a file, or drag &amp; drop</p>
              <p className="text-xs text-gray-600 mt-1">CSV, XLSX, or XLS</p>
              <input
                ref={fileInputRef}
                type="file"
                accept=".csv,.xlsx,.xls"
                className="hidden"
                onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f); }}
              />
            </div>
          ) : (
            <div className="space-y-2">
              <textarea
                value={pasteText}
                onChange={(e) => setPasteText(e.target.value)}
                placeholder={"Symbol\tQty\tAvg Price\nRELIANCE\t10\t2450.50\nTCS\t5\t3800"}
                rows={6}
                className="w-full bg-dark-bg border border-dark-border rounded-xl px-3 py-2 text-white text-sm font-mono outline-none focus:border-brand-500 resize-none"
              />
              <button
                onClick={handlePaste}
                disabled={!pasteText.trim()}
                className="px-4 py-2 rounded-lg bg-brand-500 text-white text-sm font-medium hover:bg-brand-600 disabled:opacity-40 transition-colors"
              >
                Parse
              </button>
            </div>
          )}

          {submitError && <p className="text-bear text-xs">{submitError}</p>}

          {successMsg ? (
            <div className="bg-bull/10 border border-bull/30 rounded-lg p-4 space-y-3">
              <p className="text-sm text-bull flex items-center gap-1.5">
                <Check size={14} /> {successMsg}
              </p>
              <button
                onClick={onClose}
                className="px-4 py-2 rounded-lg bg-brand-500 text-white text-sm font-medium hover:bg-brand-600 transition-colors"
              >
                Done
              </button>
            </div>
          ) : (
          <>
          {parseErrors.length > 0 && (
            <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-lg p-3">
              <p className="text-xs font-semibold text-yellow-400 flex items-center gap-1.5 mb-1">
                <AlertTriangle size={12} /> {parseErrors.length} row{parseErrors.length !== 1 ? "s" : ""} couldn't be parsed
              </p>
              <ul className="text-[11px] text-yellow-300/80 space-y-0.5 max-h-20 overflow-y-auto">
                {parseErrors.slice(0, 10).map((e, i) => (
                  <li key={i}>{e.rowIndex >= 0 ? `Row ${e.rowIndex + 2}: ` : ""}{e.reason}</li>
                ))}
              </ul>
            </div>
          )}

          {parsed && parsed.length > 0 && (
            <div>
              <p className="text-xs text-gray-400 mb-2">
                {parsed.length - excluded.size} of {parsed.length} row{parsed.length !== 1 ? "s" : ""} selected — uncheck any you don't want to import
              </p>
              <div className="border border-dark-border rounded-xl overflow-hidden max-h-64 overflow-y-auto">
                <table className="w-full text-xs">
                  <thead className="bg-dark-bg sticky top-0">
                    <tr className="text-gray-500">
                      <th className="px-3 py-2 text-left w-8"></th>
                      <th className="px-3 py-2 text-left">Symbol</th>
                      <th className="px-3 py-2 text-right">Qty</th>
                      <th className="px-3 py-2 text-right">Avg Price</th>
                      <th className="px-3 py-2 text-left">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-dark-border">
                    {parsed.map((h, i) => {
                      const existing = existingMap.get(h.symbol);
                      const isExcluded = excluded.has(i);
                      return (
                        <tr key={i} className={isExcluded ? "opacity-40" : ""}>
                          <td className="px-3 py-1.5">
                            <button onClick={() => toggleExclude(i)} className="text-gray-400 hover:text-white">
                              {isExcluded ? <X size={14} /> : <Check size={14} className="text-bull" />}
                            </button>
                          </td>
                          <td className="px-3 py-1.5 font-mono font-bold">
                            {h.symbol}
                            {h.corrected && (
                              <p className="text-[10px] font-normal text-yellow-400" title="Your broker's internal code didn't match a real ticker — corrected via company name">
                                was &quot;{h.originalSymbol}&quot;
                              </p>
                            )}
                          </td>
                          <td className="px-3 py-1.5 text-right font-mono">{h.qty}</td>
                          <td className="px-3 py-1.5 text-right font-mono">{h.avgPrice}</td>
                          <td className="px-3 py-1.5 text-gray-500">
                            {existing
                              ? `Update (was ${existing.qty} @ ${existing.avgPrice})`
                              : "Add new"}
                            {h.unverified && (
                              <span className="ml-1.5 inline-flex items-center gap-1 text-yellow-400" title="Couldn't verify this symbol against our stock list — it may be delisted, illiquid, or not yet in our universe. Live prices/signals may not populate.">
                                <AlertTriangle size={10} /> unverified
                              </span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <button
                onClick={confirmImport}
                disabled={busy}
                className="mt-3 w-full py-2.5 rounded-xl bg-brand-500 text-white text-sm font-semibold hover:bg-brand-600 disabled:opacity-50 transition-colors"
              >
                {busy ? "Importing…" : `Import ${parsed.length - excluded.size} Holding${parsed.length - excluded.size !== 1 ? "s" : ""}`}
              </button>
            </div>
          )}
          </>
          )}
        </div>
      </div>
    </div>
  );
}
